#!/usr/bin/env python3
import os
import sys
import json
import argparse
import re
import pandas as pd
from datetime import datetime
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import insert
from dotenv import load_dotenv

# Add parent directory to path to allow importing core and scrapers
sys.path.append(str(Path(__file__).parent.parent))

# Load environment variables
for p in [Path('.'), Path('..'), Path(__file__).parent.parent]:
    for env_file in ['.env.local', '.env']:
        env_path = p / env_file
        if env_path.exists():
            load_dotenv(env_path)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/maritime_dw")

# Import our custom auditor, second brain builder, downloader, and inference engine
from dw.data_quality_auditor import DataQualityAuditor
from core.second_brain import SecondBrainBuilder
from scrapers.semarnat_downloader import SemarnatDownloader
from core.inference_engine import generate_report
from core.graph_builder import parse_semarnat_key
from core.pdf_processor import iter_pages_as_markdown

# Regex de clave SEMARNAT
_CLAVE_RE = re.compile(r"\b(\d{2}[A-Z]{2}\d{4}[A-Z0-9]\d{3,5})\b")

def postgres_upsert_method(table, conn, keys, data_iter):
    """
    Custom upsert method for pandas.DataFrame.to_sql targeting PostgreSQL.
    """
    table_name = table.table.name
    
    conflict_cols_map = {
        "semarnat_projects": ["clave"],
        "project_evaluations": ["clave"]
    }
    
    conflict_cols = conflict_cols_map.get(table_name)
    data = [dict(zip(keys, row)) for row in data_iter]
    
    if not conflict_cols:
        conn.execute(table.table.insert(), data)
        return

    stmt = insert(table.table).values(data)
    update_dict = {
        c.name: getattr(stmt.excluded, c.name)
        for c in table.table.columns
        if c.name not in conflict_cols and c.name != "id" and not c.primary_key
    }
    
    if update_dict:
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=conflict_cols,
            set_=update_dict
        )
    else:
        upsert_stmt = stmt.on_conflict_do_nothing(
            index_elements=conflict_cols
        )
        
    conn.execute(upsert_stmt)

class SemarnatDwPipeline:
    def __init__(self, db_url: str, dry_run: bool = False):
        self.db_url = db_url
        self.dry_run = dry_run
        self.auditor = DataQualityAuditor()
        self.csv_metadata = {}
        
        self.base_dir = Path(__file__).parent.parent
        self.downloads_dir = self.base_dir / "downloads"
        self.estudios_dir = self.downloads_dir / "estudios"
        self.resumenes_dir = self.downloads_dir / "resumenes"
        self.resolutivos_dir = self.downloads_dir / "resolutivos"
        self.extractions_dir = self.base_dir / "extractions"
        self.data_dir = self.base_dir / "data"
        self.inference_cache_dir = self.data_dir / "inference_cache"

        for d in [self.downloads_dir, self.estudios_dir, self.resumenes_dir, 
                  self.resolutivos_dir, self.extractions_dir, self.data_dir, 
                  self.inference_cache_dir]:
            d.mkdir(parents=True, exist_ok=True)
        
        if not self.dry_run:
            print(f"[Init] Connecting to database: {self.db_url.split('@')[-1]}")
            self.engine = create_engine(self.db_url)
        else:
            print("[Init] Running in DRY-RUN mode. No database writes.")
            self.engine = None

    def initialize_schema(self):
        """Executes the DDL schema.sql script to prepare database tables."""
        if self.dry_run:
            print("[Schema] Dry run: skipping schema creation.")
            return

        schema_path = Path(__file__).parent / "schema.sql"
        if not schema_path.exists():
            print("[Error] schema.sql not found!")
            sys.exit(1)

        print("[Schema] Executing schema.sql...")
        with open(schema_path, "r", encoding="utf-8") as f:
            ddl_commands = f.read()

        with self.engine.connect() as conn:
            conn.execute(text(ddl_commands))
            conn.commit()
        print("[Schema] Database schema initialized successfully.")

    def load_target_claves(self) -> list[str]:
        """Loads target claves from CSV or extracts them from gacetas."""
        print("[Claves] Loading target environmental claves...")
        claves = set()

        # Fallback list of sample valid keys that work in SEMARNAT portal
        fallback_claves = ["02BC2025E0049", "09LP2026X0001"]

        # 1. Read from data/claves_2026.csv if exists
        csv_path = self.data_dir / "claves_2026.csv"
        if csv_path.exists():
            try:
                df = pd.read_csv(csv_path)
                df.columns = [c.upper() for c in df.columns]
                if "CLAVE" in df.columns:
                    for _, row_data in df.iterrows():
                        key = str(row_data["CLAVE"]).strip().upper()
                        if _CLAVE_RE.match(key):
                            claves.add(key)
                            proj_name = row_data.get("PROJECT_NAME")
                            loc = row_data.get("LOCATION")
                            prom = row_data.get("PROMOVENTE")
                            self.csv_metadata[key] = {
                                "project_name": proj_name if pd.notna(proj_name) else f"Proyecto {key}",
                                "location": loc if pd.notna(loc) else "",
                                "promovente": prom if pd.notna(prom) else "Desconocido"
                            }
                    print(f"[Claves] Loaded {len(df)} keys from {csv_path.name}")
            except Exception as exc:
                print(f"[Claves] Warning reading CSV: {exc}")

        # 2. Extract from existing markdown extractions of gacetas
        for md_file in self.extractions_dir.glob("*.md"):
            if "gaceta" in md_file.name.lower() or md_file.name.startswith("ASEA_"):
                try:
                    content = md_file.read_text(encoding="utf-8", errors="ignore")
                    found = _CLAVE_RE.findall(content.upper())
                    claves.update(found)
                except Exception as exc:
                    print(f"[Claves] Error scanning {md_file.name}: {exc}")

        # Add fallbacks if empty
        if not claves:
            print(f"[Claves] No keys found in workspace. Using fallbacks: {fallback_claves}")
            claves.update(fallback_claves)

        # Sort and clean
        final_list = sorted([c for c in claves if _CLAVE_RE.match(c)])
        print(f"[Claves] Total unique environmental keys to process: {len(final_list)}")
        return final_list

    def process_semarnat_portal(self, claves: list[str]):
        """Queries SEMARNAT portal and downloads documents for missing files."""
        print("[SEMARNAT] Querying portal and downloading missing files...")
        
        # Check which claves are missing files to avoid redundant Selenium runs
        to_download = []
        for clave in claves:
            estudio = self.estudios_dir / f"{clave}.pdf"
            resumen = self.resumenes_dir / f"{clave}.pdf"
            resolutivo = self.resolutivos_dir / f"{clave}.pdf"
            if not estudio.exists() and not resumen.exists() and not resolutivo.exists():
                to_download.append(clave)

        if not to_download:
            print("[SEMARNAT] All documents are already cached locally. Skipping downloads.")
            return

        print(f"[SEMARNAT] Need to download files for {len(to_download)} keys: {to_download}")
        if self.dry_run:
            print("[SEMARNAT] Dry run: skipping Selenium portal downloads.")
            return

        try:
            # We initialize one downloader session for all keys
            downloader = SemarnatDownloader(
                download_dir=str(self.downloads_dir),
                carpeta_estudios=str(self.estudios_dir),
                carpeta_resumenes=str(self.resumenes_dir),
                carpeta_resolutivos=str(self.resolutivos_dir),
                headless=True
            )
            for idx, clave in enumerate(to_download):
                print(f"[SEMARNAT] Running downloader for key {clave} ({idx+1}/{len(to_download)})...")
                # Consume downloader generator to print log statements
                for event in downloader._descargar_clave_gen(clave):
                    if event.get("status") == "log":
                        print(f"  [Downloader] {event.get('msg')}")
        except Exception as exc:
            print(f"[SEMARNAT] Downloader error: {exc}")

    def _find_estudio_pdf(self, clave: str) -> Path | None:
        """
        Localiza el PDF de estudio para una clave SEMARNAT.
        El downloader nombra los archivos: {clave}.estudio.{idx:02d}.pdf
        pero también puede existir como {clave}.pdf (path legacy).
        """
        candidates = sorted(self.estudios_dir.glob(f"{clave}.estudio.*.pdf"))
        if candidates:
            return candidates[0]
        legacy = self.estudios_dir / f"{clave}.pdf"
        if legacy.exists():
            return legacy
        return None

    def _find_extraction_md(self, clave: str) -> Path | None:
        """
        Localiza el archivo .md de extracción para una clave.
        Puede ser: {clave}.estudio.00.md, {clave}.md, etc.
        """
        candidates = sorted(self.extractions_dir.glob(f"{clave}.estudio.*.md"))
        if candidates:
            return candidates[0]
        legacy = self.extractions_dir / f"{clave}.md"
        if legacy.exists():
            return legacy
        return None

    def convert_to_markdown(self, claves: list[str]):
        """Converts study PDFs to Markdown."""
        print("[Markdown] Converting study PDFs to Markdown...")
        for clave in claves:
            estudio_pdf = self._find_estudio_pdf(clave)
            if not estudio_pdf:
                continue

            md_path = self.extractions_dir / (estudio_pdf.stem + ".md")

            if md_path.exists() and md_path.stat().st_mtime >= estudio_pdf.stat().st_mtime:
                continue

            print(f"[Markdown] Extracting text from {estudio_pdf.name}...")
            if self.dry_run:
                continue

            try:
                pages = []
                for _, _, md_text, _ in iter_pages_as_markdown(estudio_pdf):
                    pages.append(md_text)
                md_path.write_text("\n".join(pages), encoding="utf-8")
                print(f"[Markdown] Extracted {len(pages)} pages to {md_path.name}")
            except Exception as exc:
                print(f"[Markdown] Extraction failed for {clave}: {exc}")

    def generate_ai_inferences(self, claves: list[str]):
        """Runs AI viability report on extracted Markdown files."""
        print("[Inferencia] Running AI environmental viability evaluations...")
        for clave in claves:
            md_path = self._find_extraction_md(clave)
            cache_path = self.inference_cache_dir / f"{clave}.json"
            
            if not md_path:
                continue

            if cache_path.exists() and cache_path.stat().st_size > 50:
                try:
                    cached = json.loads(cache_path.read_text(encoding="utf-8", errors="ignore"))
                    if cached.get("veredicto") != "PENDIENTE":
                        continue
                except Exception:
                    pass

            print(f"[Inferencia] Generating report for key {clave} using {md_path.name}...")
            if self.dry_run:
                continue

            try:
                report = generate_report(md_path)
                cache_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
                print(f"[Inferencia] Report created for {clave}: {report.get('veredicto', 'SIN VEREDICTO')}")
            except Exception as exc:
                print(f"[Inferencia] Inferences failed for {clave}: {exc}")

    def run_auditor_and_ingest(self, claves: list[str]):
        """Builds DataFrame, audits it, and ingests to PostgreSQL."""
        print("[Auditor] Gathering data and running Quality Auditor...")
        
        projects_data = []
        evaluations_data = []

        for clave in claves:
            # 1. Parse clave parameters
            parsed = parse_semarnat_key(clave + ".pdf")
            
            # 2. Check downloaded files
            downloaded = []
            if (self.resumenes_dir / f"{clave}.pdf").exists():
                downloaded.append("resumen")
            if (self.estudios_dir / f"{clave}.pdf").exists():
                downloaded.append("estudio")
            if (self.resolutivos_dir / f"{clave}.pdf").exists():
                downloaded.append("resolutivo")

            # 3. Read status and details from inference cache if exists
            project_name = f"Proyecto {clave}"
            if clave in self.csv_metadata:
                project_name = self.csv_metadata[clave]["project_name"]
            status = "En evaluación"
            if len(downloaded) == 3 or (self.resolutivos_dir / f"{clave}.pdf").exists():
                status = "Resuelto"

            cache_path = self.inference_cache_dir / f"{clave}.json"
            eval_record = {
                "clave": clave,
                "veredicto": "PENDIENTE",
                "score": 0.0,
                "confianza_pct": 0,
                "knockouts": json.dumps([]),
                "yes_signals": json.dumps([]),
                "no_signals": json.dumps([]),
                "condicionantes": json.dumps([])
            }

            if cache_path.exists():
                try:
                    report = json.loads(cache_path.read_text(encoding="utf-8"))
                    project_name = report.get("project_name", project_name)
                    eval_record = {
                        "clave": clave,
                        "veredicto": report.get("veredicto", "PENDIENTE"),
                        "score": float(report.get("score", 0.0)),
                        "confianza_pct": int(report.get("confianza_pct", 0)),
                        "knockouts": json.dumps(report.get("knockouts", [])),
                        "yes_signals": json.dumps(report.get("yes_signals", [])),
                        "no_signals": json.dumps(report.get("no_signals", [])),
                        "condicionantes": json.dumps(report.get("condicionantes", []))
                    }
                except Exception as exc:
                    print(f"[Auditor] Error reading cache for {clave}: {exc}")

            state = parsed.get("estado_nombre")
            if clave in self.csv_metadata and self.csv_metadata[clave]["location"]:
                state = self.csv_metadata[clave]["location"]

            promovente = "Desconocido"
            if clave in self.csv_metadata and "promovente" in self.csv_metadata[clave] and self.csv_metadata[clave]["promovente"]:
                promovente = self.csv_metadata[clave]["promovente"]

            projects_data.append({
                "clave": clave,
                "project_name": project_name,
                "status": status,
                "sector": parsed.get("sector"),
                "state": state,
                "year": parsed.get("year"),
                "files_downloaded": downloaded,
                "promovente": promovente
            })
            evaluations_data.append(eval_record)

        # Build DataFrames
        df_projects = pd.DataFrame(projects_data)
        df_evals = pd.DataFrame(evaluations_data)

        # Run custom auditor
        cleaned_projects, metrics = self.auditor.audit_semarnat_projects(df_projects)
        
        # Filter evaluations to match only successfully ingested/cleaned projects
        valid_claves = set(cleaned_projects["clave"])
        filtered_evals = df_evals[df_evals["clave"].isin(valid_claves)].reset_index(drop=True)

        # Run evaluations auditor
        cleaned_evals, eval_metrics = self.auditor.audit_project_evaluations(filtered_evals)

        print("[Ingest] Ingesting audited records into database...")
        if self.dry_run:
            print("[Ingest] Dry run: skipping database ingestion.")
            return

        with self.engine.connect() as conn:
            # Insert projects
            cleaned_projects.to_sql(
                "semarnat_projects",
                con=conn,
                schema="public",
                if_exists="append",
                index=False,
                method=postgres_upsert_method
            )
            # Insert evaluations
            cleaned_evals.to_sql(
                "project_evaluations",
                con=conn,
                schema="public",
                if_exists="append",
                index=False,
                method=postgres_upsert_method
            )
            conn.commit()

        print(f"[Ingest] Ingested {len(cleaned_projects)} projects and {len(cleaned_evals)} AI evaluations successfully.")

    def update_second_brain(self):
        """Triggers second brain vault compilation."""
        print("[Second Brain] Sincronizando notas del Second Brain...")
        if self.dry_run:
            return
        try:
            builder = SecondBrainBuilder(self.base_dir)
            stats = builder.build_vault()
            print(f"[Second Brain] Built vault: {stats.get('total_proyectos', 0)} projects, {stats.get('total_gacetas', 0)} gacetas.")
        except Exception as exc:
            print(f"[Second Brain] Warning: {exc}")

    def run(self):
        print("=== LOGR DATA WAREHOUSE PIPELINE RUN ===")
        self.initialize_schema()
        claves = self.load_target_claves()
        self.process_semarnat_portal(claves)
        self.convert_to_markdown(claves)
        self.generate_ai_inferences(claves)
        self.run_auditor_and_ingest(claves)
        self.update_second_brain()
        print("=== PIPELINE RUN COMPLETED SUCCESSFULLY ===")

def main():
    parser = argparse.ArgumentParser(description="Zohar SEMARNAT Data Warehouse Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Run without writing to database")
    args = parser.parse_args()
    
    pipeline = SemarnatDwPipeline(DATABASE_URL, dry_run=args.dry_run)
    pipeline.run()

if __name__ == "__main__":
    main()
