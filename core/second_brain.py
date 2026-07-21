"""
core/second_brain.py
Motor de generación de la base de conocimiento "Second Brain" de la Base de Conocimiento de Zohar.
Procesa archivos descargados, gacetas, extracciones e inferencias.
"""

from __future__ import annotations

import json
import re
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

# Reutilizar el regex y mapeos de graph_builder
from core.graph_builder import parse_semarnat_key, ESTADO_NOMBRES, TIPO_MIA

class SecondBrainBuilder:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.downloads_dir = self.base_dir / "downloads"
        self.extractions_dir = self.base_dir / "extractions"
        self.data_dir = self.base_dir / "data"
        self.inference_cache_dir = self.data_dir / "inference_cache"
        self.sb_dir = self.base_dir / "second_brain"
        self.sources_dir = self.sb_dir / "01_Sources"
        self.entities_dir = self.sb_dir / "02_Entities"
        self.inferences_dir = self.sb_dir / "03_Inferences"

        self.clave_re = re.compile(r"(?<![A-Z0-9])(\d{2}[A-Z]{2}\d{4}[A-Z0-9]\d{3,5})(?![A-Z0-9])")
        self.clave_anchored_re = re.compile(r"^\d{2}[A-Z]{2}\d{4}[A-Z0-9]\d{3,5}$")
        self._extraction_index: dict[str, list[Path]] | None = None

    def _build_extraction_index(self) -> dict[str, list[Path]]:
        """
        Escanea extractions_dir UNA vez y agrupa archivos por clave SINAT
        detectada en el nombre. Archivos sin clave (ASEA_*, gaceta_*, TEST_*)
        quedan fuera del índice a propósito: no tienen equivalente SINAT.
        """
        index: dict[str, list[Path]] = {}
        if not self.extractions_dir.exists():
            return index

        for path in self.extractions_dir.glob("*.md"):
            candidate = path.name.split(".")[0].upper()
            if self.clave_anchored_re.match(candidate):
                index.setdefault(candidate, []).append(path)

        logger.info("Índice de extracciones construido: %d claves con archivo", len(index))
        return index


    # ──────────────────────────────────────────────────────────────────────────
    # API pública: resolución de rutas para el motor de inferencia
    # ──────────────────────────────────────────────────────────────────────────

    def get_extraction_path(self, clave: str) -> Path | None:
        """
        Resuelve la ruta del archivo .md de extracción para una clave SINAT,
        usando un índice construido en runtime (no requiere nombres exactos
        ni versión fija). Prioridad: estudio > resumen > resolutivo > genérico,
        tomando la versión más alta disponible por tipo.

        IMPORTANTE: devuelve la ruta del archivo de EXTRACCIÓN, no la ficha
        del second_brain en 02_Entities/. La ficha solo es un índice.
        """
        if self._extraction_index is None:
            self._extraction_index = self._build_extraction_index()

        candidates = self._extraction_index.get(clave.upper(), [])
        if not candidates:
            logger.warning("Sin extracción disponible para clave: %s", clave)
            return None

        priority = ["estudio", "resumen", "resolutivo"]

        def sort_key(p: Path):
            parts = p.name.split(".")
            doc_type = parts[1] if len(parts) > 2 else ""
            version = parts[2] if len(parts) > 3 else "00"
            try:
                rank = priority.index(doc_type)
            except ValueError:
                rank = len(priority)
            v_num = int(re.sub(r"\D", "", version)) if re.sub(r"\D", "", version) else 0
            return (rank, -v_num)

        best = sorted(candidates, key=sort_key)[0]
        logger.debug("Extracción encontrada para %s: %s", clave, best.name)
        return best

    def build_vault(self) -> dict:
        """
        Construye la bóveda completa de la Base de Conocimiento de Zohar en second_brain/.
        Retorna estadísticas de la generación.
        """
        # 1. Asegurar directorios de salida
        self.sb_dir.mkdir(parents=True, exist_ok=True)
        sources_dir = self.sb_dir / "01_Sources"
        entities_dir = self.sb_dir / "02_Entities"
        inferences_dir = self.sb_dir / "03_Inferences"

        for d in [sources_dir, entities_dir, inferences_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # 2. Escanear todo el corpus
        self._extraction_index = self._build_extraction_index()
        pdfs = self._scan_pdfs()
        extractions = self._scan_extractions()
        inferences = self._scan_inferences()

        # 3. Indexar proyectos
        projects = self._index_projects(pdfs, extractions, inferences)

        # 4. Escanear gacetas y asociar proyectos
        gacetas = self._process_gacetas(pdfs, extractions, projects)

        # 5. Generar notas de Gacetas
        for gaceta_name, gaceta_info in gacetas.items():
            self._write_gaceta_note(sources_dir, gaceta_name, gaceta_info)

        # 6. Generar notas de Proyectos, Extracciones e Inferencias
        municipios = {}
        sectores = {}
        tipos = {}

        for clave, proj in projects.items():
            # Generar nota del proyecto
            self._write_project_note(entities_dir, clave, proj)

            # Agrupar por entidad para las notas colectoras
            if proj.get("valid"):
                muni_name = proj.get("estado_nombre")
                if muni_name:
                    municipios.setdefault(muni_name, []).append(clave)

                sect_id = proj.get("sector")
                if sect_id:
                    sectores.setdefault(sect_id, []).append(clave)

                tipo_name = proj.get("tipo_nombre")
                if tipo_name:
                    tipos.setdefault(tipo_name, []).append(clave)

            # Generar nota de inferencia si existe
            if proj.get("inference"):
                self._write_inference_note(inferences_dir, clave, proj["inference"])

        # 7. Generar notas agrupadoras (Entidades de segundo nivel)
        for muni_name, p_list in municipios.items():
            self._write_collector_note(entities_dir, f"Municipio - {muni_name}", "Municipio/Estado", p_list)

        for sect_id, p_list in sectores.items():
            self._write_collector_note(entities_dir, f"Sector - {sect_id}", "Sector de Impacto", p_list)

        for tipo_name, p_list in tipos.items():
            self._write_collector_note(entities_dir, f"Tipo - {tipo_name}", "Tipo de MIA", p_list)

        # 8. Generar 00_Index.md y 00_Workflow.md
        self._write_index_note(projects, gacetas, pdfs)
        self._write_workflow_note()

        return {
            "total_proyectos": len(projects),
            "total_gacetas": len(gacetas),
            "total_municipios": len(municipios),
            "total_inferencias": sum(1 for p in projects.values() if p.get("inference")),
        }

    def _scan_pdfs(self) -> dict[str, list[dict]]:
        """Busca y agrupa archivos PDF en downloads/."""
        pdf_groups = {}
        if not self.downloads_dir.exists():
            return pdf_groups

        for pdf in self.downloads_dir.rglob("*.pdf"):
            folder = pdf.parent.name
            stat = pdf.stat()
            pdf_info = {
                "name": pdf.name,
                "path": pdf,
                "size_bytes": stat.st_size,
                "modified_ts": stat.st_mtime,
            }
            pdf_groups.setdefault(folder, []).append(pdf_info)
        return pdf_groups

    def _scan_extractions(self) -> dict[str, dict]:
        """Escanea la carpeta extractions/ buscando archivos .md."""
        md_files = {}
        if not self.extractions_dir.exists():
            return md_files

        for md in self.extractions_dir.glob("*.md"):
            md_files[md.stem] = {
                "name": md.name,
                "path": md,
                "modified_ts": md.stat().st_mtime,
            }
        return md_files

    def _scan_inferences(self) -> dict[str, dict]:
        """Escanea la caché de reportes de inferencia en data/inference_cache/ y base de datos."""
        inf_reports = {}
        if self.inference_cache_dir.exists():
            for js in self.inference_cache_dir.glob("*.json"):
                try:
                    data = json.loads(js.read_text(encoding="utf-8", errors="ignore"))
                    inf_reports[js.stem] = data
                except Exception as exc:
                    logger.warning("Error leyendo reporte inferencia %s: %s", js.name, exc)

        # Cargar adicionalmente desde la base de datos PostgreSQL
        try:
            import os
            import sqlalchemy as sa
            db_url = os.getenv("DATABASE_URL")
            if not db_url:
                from dotenv import load_dotenv
                from pathlib import Path
                for env_file in [Path(".env.local"), Path(".env"), Path(__file__).parent.parent / ".env"]:
                    if env_file.exists():
                        load_dotenv(env_file)
                db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/maritime_dw")

            if db_url:
                engine = sa.create_engine(db_url)
                with engine.connect() as conn:
                    result = conn.execute(sa.text(
                        "SELECT clave, veredicto, score, confianza_pct, knockouts, yes_signals, no_signals, condicionantes FROM project_evaluations"
                    ))
                    for row in result:
                        clave = row[0]
                        # Evitar sobreescribir si ya está cargado desde disco
                        if clave in inf_reports:
                            continue
                        
                        # Mapear a formato esperado de reporte
                        inf_reports[clave] = {
                            "veredicto": row[1],
                            "score": float(row[2]) if row[2] is not None else 0.0,
                            "confianza_pct": int(row[3]) if row[3] is not None else 0,
                            "knockouts": row[4] if isinstance(row[4], list) else (json.loads(row[4]) if row[4] else []),
                            "yes_signals": row[5] if isinstance(row[5], list) else (json.loads(row[5]) if row[5] else []),
                            "no_signals": row[6] if isinstance(row[6], list) else (json.loads(row[6]) if row[6] else []),
                            "condicionantes": row[7] if isinstance(row[7], list) else (json.loads(row[7]) if row[7] else []),
                            "meta": {"modelo": "db-sync"}
                        }
                logger.info("Cargadas %d inferencias adicionales desde la base de datos", len(inf_reports))
        except Exception as exc:
            logger.warning("No se pudieron cargar inferencias de la base de datos: %s", exc)

        return inf_reports

    def _index_projects(self, pdfs: dict, extractions: dict, inferences: dict) -> dict:
        """Asocia todos los recursos de un proyecto en un solo mapa indexado."""
        projects = {}

        # 1. Recopilar desde los PDFs de estudios, resumenes, resolutivos
        for category in ["estudios", "resumenes", "resolutivos"]:
            for pdf in pdfs.get(category, []):
                parsed = parse_semarnat_key(pdf["name"])
                clave = parsed["clave"]
                
                projects.setdefault(clave, {
                    "clave": clave,
                    "valid": parsed.get("valid", False),
                    "sector": parsed.get("sector"),
                    "estado": parsed.get("estado"),
                    "estado_nombre": parsed.get("estado_nombre"),
                    "year": parsed.get("year"),
                    "tipo": parsed.get("tipo"),
                    "tipo_nombre": parsed.get("tipo_nombre"),
                    "project_name": f"Proyecto {clave}",
                    "promovente": "Desconocido",
                    "status": "INGRESADO",
                    "estudio_pdf": None,
                    "resumen_pdf": None,
                    "resolutivo_pdf": None,
                    "extraction": None,
                    "inference": None,
                })

                projects[clave][f"{category[:-1]}_pdf"] = pdf

        # 2. Asociar extractions
        for name, md in extractions.items():
            # Si el extraction no es una gaceta (las gacetas no tienen clave SINAT de 12-14 chars)
            parsed = parse_semarnat_key(name + ".pdf")
            if parsed.get("valid"):
                clave = parsed["clave"]
                projects.setdefault(clave, {
                    "clave": clave,
                    "valid": True,
                    "sector": parsed.get("sector"),
                    "estado": parsed.get("estado"),
                    "estado_nombre": parsed.get("estado_nombre"),
                    "year": parsed.get("year"),
                    "tipo": parsed.get("tipo"),
                    "tipo_nombre": parsed.get("tipo_nombre"),
                    "project_name": f"Proyecto {clave}",
                    "promovente": "Desconocido",
                    "status": "INGRESADO",
                    "estudio_pdf": None,
                    "resumen_pdf": None,
                    "resolutivo_pdf": None,
                    "extraction": None,
                    "inference": None,
                })
                projects[clave]["extraction"] = md

        # 3. Consultar la base de datos para cargar metadatos reales
        db_metadata = {}
        try:
            import os
            import sqlalchemy as sa
            db_url = os.getenv("DATABASE_URL")
            if not db_url:
                from dotenv import load_dotenv
                from pathlib import Path
                for env_file in [Path(".env.local"), Path(".env"), Path(__file__).parent.parent / ".env"]:
                    if env_file.exists():
                        load_dotenv(env_file)
                db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/maritime_dw")

            if db_url:
                engine = sa.create_engine(db_url)
                with engine.connect() as conn:
                    result = conn.execute(sa.text(
                        "SELECT clave, project_name, status, sector, state, year, promovente FROM public.semarnat_projects"
                    ))
                    for row in result:
                        clave = row[0]
                        db_metadata[clave] = {
                            "project_name": row[1],
                            "status": row[2],
                            "sector": row[3],
                            "state": row[4],
                            "year": row[5],
                            "promovente": row[6]
                        }
        except Exception as exc:
            logger.warning("No se pudo cargar metadatos de semarnat_projects: %s", exc)

        # 4. Enriquecer proyectos existentes y añadir proyectos de la base de datos sin archivos locales
        for clave, meta in db_metadata.items():
            if clave in projects:
                if meta.get("project_name"):
                    projects[clave]["project_name"] = meta["project_name"]
                if meta.get("promovente"):
                    projects[clave]["promovente"] = meta["promovente"]
                if meta.get("status"):
                    projects[clave]["status"] = meta["status"]
                if meta.get("sector"):
                    projects[clave]["sector"] = meta["sector"]
                if meta.get("state"):
                    projects[clave]["estado_nombre"] = meta["state"]
                if meta.get("year"):
                    projects[clave]["year"] = meta["year"]
            else:
                # Crear ficha esqueleto enriquecida
                parsed = parse_semarnat_key(clave + ".pdf")
                projects[clave] = {
                    "clave": clave,
                    "valid": parsed.get("valid", False),
                    "sector": meta.get("sector") or parsed.get("sector"),
                    "estado": parsed.get("estado"),
                    "estado_nombre": meta.get("state") or parsed.get("estado_nombre"),
                    "year": meta.get("year") or parsed.get("year"),
                    "tipo": parsed.get("tipo"),
                    "tipo_nombre": parsed.get("tipo_nombre"),
                    "project_name": meta.get("project_name") or f"Proyecto {clave}",
                    "promovente": meta.get("promovente") or "Desconocido",
                    "status": meta.get("status") or "PENDIENTE",
                    "estudio_pdf": None,
                    "resumen_pdf": None,
                    "resolutivo_pdf": None,
                    "extraction": None,
                    "inference": None,
                }

        # 5. Asociar inferencias
        for name, report in inferences.items():
            parsed = parse_semarnat_key(name + ".pdf")
            if parsed.get("valid"):
                clave = parsed["clave"]
                if clave in projects:
                    projects[clave]["inference"] = report

        return projects

    def _process_gacetas(self, pdfs: dict, extractions: dict, projects: dict) -> dict:
        """Encuentra gacetas y busca qué proyectos están asociados a ellas."""
        gacetas = {}

        # 1. Indexar gacetas en PDFs (tanto de SINAT en "gacetas" como de ASEA en "asea")
        for pdf in pdfs.get("gacetas", []):
            stem = Path(pdf["name"]).stem
            gacetas[stem] = {
                "pdf": pdf,
                "extraction": extractions.get(stem),
                "proyectos": [],
            }

        for pdf in pdfs.get("asea", []):
            stem = Path(pdf["name"]).stem
            gacetas[stem] = {
                "pdf": pdf,
                "extraction": extractions.get(stem),
                "proyectos": [],
            }

        # 2. Si no hay PDFs pero hay extractions de gacetas o archivos de la ASEA
        for name, md in extractions.items():
            if ("gaceta" in name.lower() or name.startswith("ASEA_")) and name not in gacetas:
                gacetas[name] = {
                    "pdf": None,
                    "extraction": md,
                    "proyectos": [],
                }

        # 3. Escanear textos extraídos de las gacetas para vincular proyectos (relación bidireccional)
        for name, info in gacetas.items():
            if info["extraction"]:
                try:
                    content = info["extraction"]["path"].read_text(encoding="utf-8", errors="ignore")
                    # Extraer claves SINAT usando regex
                    found_claves = set(self.clave_re.findall(content.upper()))
                    for c in found_claves:
                        # Crear entrada stub si el proyecto aún no tiene archivos indexados
                        if c not in projects:
                            from core.graph_builder import parse_semarnat_key
                            parsed = parse_semarnat_key(c + ".pdf")
                            projects[c] = {
                                "clave": c,
                                "valid": parsed.get("valid", False),
                                "sector": parsed.get("sector"),
                                "estado": parsed.get("estado"),
                                "estado_nombre": parsed.get("estado_nombre"),
                                "year": parsed.get("year"),
                                "tipo": parsed.get("tipo"),
                                "tipo_nombre": parsed.get("tipo_nombre"),
                                "estudio_pdf": None,
                                "resumen_pdf": None,
                                "resolutivo_pdf": None,
                                "extraction": None,
                                "inference": None,
                            }
                        info["proyectos"].append(c)
                        # También asociamos la gaceta al proyecto
                        projects[c]["gaceta_origen"] = name
                except Exception as exc:
                    logger.error("Error analizando contenido de gaceta %s: %s", name, exc)

        return gacetas


    # ===========================================================================
    # Escritura de Notas Markdown (Zohar KB Templates)
    # ===========================================================================

    def _write_gaceta_note(self, dest_dir: Path, name: str, info: dict):
        """Escribe una nota para una gaceta."""
        is_asea = name.upper().startswith("ASEA_")
        prefix = "Gaceta ASEA" if is_asea else "Gaceta"
        note_path = dest_dir / f"{prefix} - {name}.md"
        pdf_link = f"[{info['pdf']['name']}](file://{info['pdf']['path']})" if info.get("pdf") else "No disponible"
        md_link = f"[{info['extraction']['name']}](file://{info['extraction']['path']})" if info.get("extraction") else "No extraído"

        proyectos_section = ""
        if info["proyectos"]:
            proyectos_section = "\n".join(f"- [[Proyecto - {c}]]" for c in sorted(info["proyectos"]))
        else:
            proyectos_section = "_No se detectaron proyectos vinculados en esta gaceta o no han sido indexados._"

        content = f"""---
type: source
category: gaceta
name: {name}
source: {"ASEA" if is_asea else "SEMARNAT"}
date_generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
---

# {"Gaceta Ecológica ASEA" if is_asea else "Gaceta Ecológica"}: {name}

## [ARCHIVOS] Relacionados
- **PDF Original:** {pdf_link}
- **Texto Extraído (.md):** {md_link}

---

## [PROYECTOS] Anunciados / Vinculados
Esta gaceta anuncia los siguientes proyectos ecológicos evaluados:

{proyectos_section}
"""
        note_path.write_text(content, encoding="utf-8")

    def _write_project_note(self, dest_dir: Path, clave: str, proj: dict):
        """Escribe una nota estructurada para un proyecto (clave SINAT)."""
        note_path = dest_dir / f"Proyecto - {clave}.md"

        # ── Ficha técnica ──────────────────────────────────────────────────
        if proj.get("valid"):
            metadata_sec = f"""## [FICHA] Técnica
- **Nombre del Proyecto:** {proj.get('project_name', f'Proyecto {clave}')}
- **Promovente:** {proj.get('promovente', 'Desconocido')}
- **Estatus de Trámite:** **{proj.get('status', 'INGRESADO')}**
- **Clave de Proyecto:** `{clave}`
- **Estado/Ubicación:** [[Municipio - {proj.get('estado_nombre') or 'Desconocido'}]]
- **Año de Registro:** {proj.get('year')}
- **Sector Productivo:** [[Sector - {proj.get('sector') or 'Desconocido'}]]
- **Tipo de Trámite:** [[Tipo - {proj.get('tipo_nombre') or 'Desconocido'}]]"""
        else:
            metadata_sec = f"""## [!] Ficha Técnica (Formato Especial)
- **Clave Identificada:** `{clave}`
- _Nota: Esta clave no cumple el formato estándar SEMARNAT de 12-14 caracteres._"""

        # ── Gaceta de origen ───────────────────────────────────────────────
        if proj.get("gaceta_origen"):
            orig = proj['gaceta_origen']
            prefix = "Gaceta ASEA" if orig.upper().startswith("ASEA_") else "Gaceta"
            gaceta_sec = f"- **Gaceta de Anuncio:** [[{prefix} - {orig}]]"
        else:
            gaceta_sec = "- **Gaceta de Anuncio:** _No detectada en el corpus local._"

        # ── Archivos PDF ───────────────────────────────────────────────────
        files_sec = ""
        for cat in ["estudio", "resumen", "resolutivo"]:
            field = f"{cat}_pdf"
            if proj.get(field):
                files_sec += f"- **PDF de {cat.capitalize()}:** [{proj[field]['name']}](file://{proj[field]['path']})\n"
            else:
                files_sec += f"- **PDF de {cat.capitalize()}:** _No descargado_\n"

        # ── Extracción .md ─────────────────────────────────────────────────
        # Buscar y listar todas las extracciones disponibles por tipo de documento
        ext_sec = ""
        for cat in ["estudio", "resumen", "resolutivo"]:
            candidates = [
                self.extractions_dir / f"{clave}.{cat}.00.md",
                self.extractions_dir / f"{clave}.{cat}.01.md",
            ]
            if cat == "estudio":
                candidates.append(self.extractions_dir / f"{clave}.md")
                
            md_path = None
            for cand in candidates:
                if cand.exists():
                    md_path = cand
                    break
            
            if md_path:
                ext_size_kb = round(md_path.stat().st_size / 1024, 1)
                ext_sec += f"- **Texto Extraído de {cat.capitalize()} (.md):** [{md_path.name}](file://{md_path}) (`{ext_size_kb} KB`)\n"
            else:
                ext_sec += f"- **Texto Extraído de {cat.capitalize()} (.md):** _No extraído_\n"

        extraction_path = self.get_extraction_path(clave)
        snippet_sec = ""
        if extraction_path:
            ext_sec += f"- **Ruta principal para inferencia:** `{extraction_path}`"
            try:
                raw_text = extraction_path.read_text(encoding="utf-8", errors="replace")
                words = raw_text.split()
                snippet = " ".join(words[:500])
                if len(words) > 500:
                    snippet += "…"
                snippet_sec = f"\n\n### Vista previa del Estudio extraído\n```\n{snippet}\n```"
            except Exception:
                pass

        # ── Dictamen de inferencia ─────────────────────────────────────────
        if proj.get("inference"):
            veredicto_badge = proj['inference'].get('veredicto', 'SIN EVALUAR')
            score_pct = round(proj['inference'].get('score', 0) * 100, 1)
            confianza = proj['inference'].get('confianza_pct', '?')
            inf_sec = (
                f"- **Reporte de Dictamen:** [[Inferencia - {clave}]]\n"
                f"- **Veredicto:** **{veredicto_badge}** | Score: `{score_pct}%` | "
                f"Confianza: `{confianza}%`"
            )
        else:
            inf_sec = (
                "- **Reporte de Dictamen:** _Pendiente_\n"
                f"- **Comando para evaluar:** `generate_report(Path('{extraction_path or 'extractions/' + clave + '.md'}'))`"
            )

        content = f"""---
type: entity
category: proyecto
clave: {clave}
valid: {proj.get('valid', False)}
extraction_path: "{extraction_path or ''}"
date_generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
---

# Proyecto SEMARNAT: {clave}

{metadata_sec}

---

## [ARCHIVOS] Documentos del Trámite
{files_sec}
{ext_sec}{snippet_sec}

---

## [DICTAMEN] Inteligencia Jurídica & Dictamen
{gaceta_sec}
{inf_sec}
"""
        note_path.write_text(content, encoding="utf-8")

    def _write_inference_note(self, dest_dir: Path, clave: str, report: dict):
        """Escribe una nota detallada del reporte de inferencia de Gemini."""
        note_path = dest_dir / f"Inferencia - {clave}.md"

        veredicto = report.get("veredicto", "SINDICTAMEN")
        score = report.get("score", 0.0)
        confianza = report.get("confianza_pct", 0)

        # Formatear señales a favor y en contra
        yes_signals = "\n".join(f"- {s}" for s in report.get("yes_signals", [])) or "_Sin señales registradas_"
        no_signals = "\n".join(f"- {s}" for s in report.get("no_signals", [])) or "_Sin señales de rechazo registradas_"
        knockouts = "\n".join(f"- [X] **{s}**" for s in report.get("knockouts", [])) or "_Ningún knockout activado_"
        condicionantes = "\n".join(f"- [*] {s}" for s in report.get("condicionantes", [])) or "_Sin condicionantes especificadas_"

        meta_mod = report.get("meta", {}).get("modelo", "Desconocido")

        content = f"""---
type: inference
category: dictamen
clave: {clave}
veredicto: {veredicto}
score: {score}
date_generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
---

# Dictamen de Inferencia: {clave}
Asociado al proyecto: [[Proyecto - {clave}]]

---

## [DICTAMEN] Veredicto: **{veredicto}**
- **Viabilidad Socio-Ambiental (Score):** `{score * 100}%`
- **Confianza de la Evaluación:** `{confianza}%`
- **Modelo de Evaluación:** `{meta_mod}`

---

## [X] Filtros Fatales (Knockouts Detectados)
Si se encuentra algún knockout, la viabilidad se reduce a 0 de forma automática:
{knockouts}

---

## [+] Señales de Viabilidad (A Favor)
{yes_signals}

---

## [-] Riesgos e Impactos Negativos (En Contra)
{no_signals}

---

## [*] Medidas de Mitigación Requeridas (Condicionantes)
{condicionantes}
"""
        note_path.write_text(content, encoding="utf-8")

    def _write_collector_note(self, dest_dir: Path, name: str, type_label: str, projects_list: list[str]):
        """Escribe una nota colectora que agrupa proyectos (ej. por Municipio o Sector)."""
        safe_name = name.replace("/", "-").replace("\\", "-")
        note_path = dest_dir / f"{safe_name}.md"


        proyectos_section = "\n".join(f"- [[Proyecto - {c}]]" for c in sorted(projects_list))

        content = f"""---
type: collector
category: {type_label.lower().replace('/', '_')}
name: {name}
date_generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
---

# {type_label}: {name.split(" - ")[-1]}

## [ARCHIVOS] Proyectos Relacionados
Se registran `{len(projects_list)}` proyectos vinculados a esta categoría en el corpus local:

{proyectos_section}
"""
        note_path.write_text(content, encoding="utf-8")

    def _write_index_note(self, projects: dict, gacetas: dict, pdfs: dict):
        """Escribe el archivo 00_Index.md raíz de la bóveda."""
        note_path = self.sb_dir / "00_Index.md"

        total_estudios = sum(1 for p in projects.values() if p.get("estudio_pdf"))
        total_resolutivos = sum(1 for p in projects.values() if p.get("resolutivo_pdf"))
        total_inferencias = sum(1 for p in projects.values() if p.get("inference"))

        # Lista de gacetas
        gacetas_lines = []
        for g, info in sorted(gacetas.items()):
            is_asea = g.upper().startswith("ASEA_")
            prefix = "Gaceta ASEA" if is_asea else "Gaceta"
            gacetas_lines.append(f"- [[{prefix} - {g}]] (`{len(info['proyectos'])}` proyectos)")
        gacetas_sec = "\n".join(gacetas_lines) or "_Ninguna gaceta indexada_"

        # Lista de proyectos recientes (últimos 15)
        recientes_sec = "\n".join(f"- [[Proyecto - {c}]] (Ubicación: {p.get('estado_nombre', 'Desconocida')} | Dictamen: **{p['inference'].get('veredicto', 'SIN EVALUAR') if p.get('inference') else 'PENDIENTE'}**)" for c, p in sorted(projects.items(), key=lambda x: x[0], reverse=True)[:15]) or "_Ningún proyecto indexado_"

        content = f"""---
type: index
name: Zohar Second Brain
date_generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
---

# Zohar Intelligence v4 — Second Brain

Bienvenido a la Bóveda de Conocimiento de Proyectos Ambientales SEMARNAT/ASEA. Este espacio está estructurado con enlaces bidireccionales y notas Markdown listas para ser navegadas en el dashboard de Zohar v4 de manera local.

---

## Workflow del Sistema
Para comprender el pipeline completo de adquisición y procesamiento de datos, consulta la guía detallada:
- [[00_Workflow|Explicación del Workflow del Sistema y Flujo de Información]]

---

## Estadísticas del Corpus
- Total de Gacetas Indexadas: `{len(gacetas)}`
- Total de Proyectos Identificados: `{len(projects)}`
- Estudios de Impacto Ambiental (MIA): `{total_estudios}`
- Resolutivos Oficiales: `{total_resolutivos}`
- Evaluaciones de Inferencia (IA): `{total_inferencias}`

---

## Gacetas Ecológicas
Navega las gacetas y los proyectos anunciados en ellas:
{gacetas_sec}

---

## Proyectos Indexados Recientemente
{recientes_sec}

---

## Directorios de la Bóveda
- [[01_Sources/|Fuentes Crudas (PDFs y Conversiones)]]
- [[02_Entities/|Entidades (Proyectos, Municipios, Sectores y Tipos)]]
- [[03_Inferences/|Dictámenes de Inferencia (Filtros y Mitigación)]]
"""
        note_path.write_text(content, encoding="utf-8")

    def _write_workflow_note(self):
        """Escribe el archivo 00_Workflow.md explicando el funcionamiento del pipeline."""
        note_path = self.sb_dir / "00_Workflow.md"

        content = f"""---
type: index
name: Zohar Workflow
date_generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
---

# Workflow del Sistema Zohar Intelligence v4

Esta nota detalla el flujo de información del sistema, desde la ingesta de las gacetas oficiales de SEMARNAT/ASEA hasta el dictamen de inferencia y la exportación de conocimiento.

---

## Fase 1: Adquisición de Gacetas (Scraping)
El pipeline inicia peinando y monitoreando los portales de publicaciones oficiales de gacetas ecológicas:
- **SEMARNAT (SINAT):** Utiliza un motor de navegación Selenium (`GazetteScraper`) que entra al portal de trámites, interactúa con los iframes de descarga y adquiere los archivos de las gacetas del año configurado.
- **ASEA:** Scraper directo basado en HTTP requests (`ASEAScraper`) que consulta y descarga las gacetas disponibles.
- Los archivos resultantes se guardan de forma organizada en `downloads/gacetas/`.

---

## Fase 2: Extracción de Claves SINAT
Una vez descargada una gaceta:
- Se convierte a texto Markdown (.md) mediante un extractor de texto.
- Se ejecuta una búsqueda por expresiones regulares buscando claves del formato de trámite de SEMARNAT (SINAT): `XX[ESTADO]YYYY[TIPO][SECUENCIA]` (ejemplo: `21PU2025H0155`).
- El backend asocia estas claves con su gaceta de origen en el log del sistema.

---

## Fase 3: Descarga de Estudios y Resolutivos
Con las claves SINAT identificadas en las gacetas:
- El descargador de SEMARNAT interactúa dinámicamente con el portal de consulta de trámites ingresando la clave.
- Detecta los botones de descarga correspondientes a:
  - Resumen del Proyecto.
  - Estudio de Impacto Ambiental (MIA).
  - Resolutivo Oficial de Dictamen.
- Los PDFs resultantes se descargan y clasifican mediante un clasificador posicional heurístico en `downloads/resumenes/`, `downloads/estudios/` y `downloads/resolutivos/`.

---

## Fase 4: Conversión a Markdown (MD_LAB)
Para poder realizar análisis semánticos y de inferencia jurídica:
- Se procesan los archivos PDF a través del motor `pdf_processor.py` (usando `pymupdf4llm`).
- Genera archivos Markdown limpios estructurados por bloques, tablas y secciones en `extractions/`.
- Este proceso cuenta con una estrategia de caché reactiva basada en marcas de tiempo (`mtime`) para evitar reprocesamientos costosos.

---

## Fase 5: Inferencia Socio-Ambiental (INFERENCE_LAB)
El motor de inferencia (`inference_engine.py`):
- Evalúa el texto Markdown de los estudios utilizando la API de Gemini (o fallbacks locales).
- Busca activamente señales de aprobación (Yes Signals), riesgos socio-ambientales (No Signals), traslapes de coordenadas (WKT), especies protegidas (NOM-059) y knockouts de rechazo inmediatos.
- El resultado se compila en un dictamen estructurado en formato JSON y se almacena en `data/inference_cache/`.

---

## Fase 6: Consolidación del Grafo y Second Brain
Como paso de cierre:
- **Grafo D3:** Genera un grafo de relaciones de red para explorar geográficamente los proyectos por estado, sector y año.
- **Second Brain:** Se estructuran de forma automática todos los archivos en el directorio `second_brain/`, generando un repositorio de notas interconectadas vinculadas bidireccionalmente mediante enlaces `[[Wiki-Link]]` para la base de datos de Zohar.
"""
        note_path.write_text(content, encoding="utf-8")

    def update_note_frontmatter(self, clave: str, evaluation_data: dict) -> bool:
        """Actualiza o enriquece el Frontmatter YAML de la nota de proyecto en Obsidian."""
        note_path = self.sources_dir / f"Proyecto - {clave}.md"
        if not note_path.exists():
            note_path = self.entities_dir / f"Proyecto - {clave}.md"
            if not note_path.exists():
                return False

        try:
            content = note_path.read_text(encoding="utf-8")
            legal_risk = evaluation_data.get("legal_risk_level", "MEDIO")
            summary = evaluation_data.get("summary", "").replace("\n", " ")

            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    yaml_block = parts[1]
                    body = parts[2]
                    
                    if "legal_risk:" not in yaml_block:
                        yaml_block += f"\nlegal_risk: {legal_risk}"
                    if "summary:" not in yaml_block:
                        yaml_block += f"\nsummary: \"{summary[:150]}...\""

                    new_content = f"---{yaml_block}---{body}"
                    note_path.write_text(new_content, encoding="utf-8")
                    return True
            return False
        except Exception as exc:
            logger.warning("Error actualizando Frontmatter de nota %s: %s", clave, exc)
            return False

    def autolink_vault(self) -> dict:
        """
        Analiza masivamente todas las notas en second_brain/ para:
        1. Inyectar etiquetas temáticas (tags: [...]) en el Frontmatter YAML.
        2. Convertir menciones de texto de claves SINAT/gacetas/entidades en wikilinks [[Nota]].
        """
        if not self.sb_dir.exists():
            return {"status": "error", "msg": "Directorio second_brain no existe"}

        all_notes = list(self.sb_dir.rglob("*.md"))
        if not all_notes:
            return {"status": "ok", "processed": 0, "tags_added": 0, "wikilinks_added": 0}

        note_titles = {note.stem: note.name for note in all_notes}

        KEYWORD_TAGS = {
            "baja california sur": "baja-california-sur",
            "baja california": "baja-california",
            "puebla": "puebla",
            "quintana roo": "quintana-roo",
            "veracruz": "veracruz",
            "sonora": "sonora",
            "sinaloa": "sinaloa",
            "tabasco": "tabasco",
            "hidrocarburos": "sector-hidrocarburos",
            "petróleo": "sector-hidrocarburos",
            "gas LP": "sector-hidrocarburos",
            "eléctrico": "sector-electrico",
            "energía": "sector-electrico",
            "turístico": "sector-turistico",
            "hotel": "sector-turistico",
            "marina": "sector-turistico",
            "desarrollo urbano": "desarrollo-urbano",
            "inmobiliario": "desarrollo-urbano",
            "mia particular": "mia-particular",
            "mia regional": "mia-regional",
            "informe preventivo": "informe-preventivo",
            "nom-059": "nom-059",
            "manglar": "manglar",
            "arrecife": "arrecife",
            "costero": "zona-costera",
        }

        tags_added_total = 0
        wikilinks_added_total = 0
        processed_count = 0

        for note_path in all_notes:
            try:
                content = note_path.read_text(encoding="utf-8", errors="ignore")
                original_content = content

                yaml_block = ""
                body = content
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        yaml_block = parts[1]
                        body = parts[2]

                found_tags = set()
                lower_body = body.lower()
                for kw, tag in KEYWORD_TAGS.items():
                    if kw.lower() in lower_body:
                        found_tags.add(tag)

                if "source: ASEA" in yaml_block or "ASEA" in note_path.name:
                    found_tags.add("asea")
                elif "source: SEMARNAT" in yaml_block or "SEMARNAT" in note_path.name:
                    found_tags.add("semarnat")

                if found_tags:
                    tag_list_str = ", ".join(sorted(found_tags))
                    if "tags:" in yaml_block:
                        yaml_block = re.sub(r"tags:.*", f"tags: [{tag_list_str}]", yaml_block)
                    else:
                        yaml_block = yaml_block.strip() + f"\ntags: [{tag_list_str}]\n"
                    tags_added_total += len(found_tags)

                def replace_clave_with_wikilink(match):
                    clave = match.group(1)
                    target_stem = f"Proyecto - {clave}"
                    if target_stem in note_titles:
                        return f"[[{target_stem}|{clave}]]"
                    return match.group(0)

                body_new = re.sub(
                    r"(?<!\[\[)(?<![A-Z0-9])(\d{2}[A-Z]{2}\d{4}[A-Z0-9]\d{3,5})(?![A-Z0-9])(?!\]\])",
                    replace_clave_with_wikilink,
                    body,
                )

                if body_new != body:
                    wikilinks_added_total += 1
                    body = body_new

                if yaml_block:
                    new_full_content = f"---{yaml_block}---{body}"
                else:
                    new_full_content = body

                if new_full_content != original_content:
                    note_path.write_text(new_full_content, encoding="utf-8")

                processed_count += 1
            except Exception as exc:
                logger.warning("Error ejecutando autolink en nota %s: %s", note_path.name, exc)

        return {
            "status": "ok",
            "processed": processed_count,
            "tags_added": tags_added_total,
            "wikilinks_added": wikilinks_added_total,
        }


