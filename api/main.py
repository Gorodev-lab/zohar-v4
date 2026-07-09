"""
api/main.py
FastAPI unificado para Zohar Intelligence v4.
Endpoints SSE, GZip, corpus, grafo, inferencia y scraper.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import platform
import re
import time
from pathlib import Path
from typing import AsyncGenerator, Optional

import psutil
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()
logger = logging.getLogger(__name__)

# Regex de clave SEMARNAT válida: ej. 23QR2024TD085, 05CO2026I0001
_CLAVE_RE = re.compile(r"(?<![A-Z0-9])(\d{2}[A-Z]{2}\d{4}[A-Z0-9]\d{3,5})(?![A-Z0-9])")

# ---------------------------------------------------------------------------
# Directorios base
# ---------------------------------------------------------------------------
BASE_DIR        = Path(__file__).parent.parent
DOWNLOADS_DIR   = BASE_DIR / "downloads"
EXTRACTIONS_DIR = BASE_DIR / "extractions"
DATA_DIR        = BASE_DIR / "data"
DASHBOARD_DIR   = BASE_DIR / "dashboard"

RESUMENES_DIR   = DOWNLOADS_DIR / "resumenes"
ESTUDIOS_DIR    = DOWNLOADS_DIR / "estudios"
RESOLUTIVOS_DIR = DOWNLOADS_DIR / "resolutivos"
GACETAS_DIR     = DOWNLOADS_DIR / "gacetas"

def _safe_relative_path(path: Path) -> str:
    """Retorna el path relativo a BASE_DIR, o el path absoluto si no está dentro de él."""
    try:
        return str(path.relative_to(BASE_DIR))
    except ValueError:
        return str(path)

# SSE stop flag
_sse_stop: dict[str, bool] = {}

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Zohar Intelligence v4",
    description="API para automatización de Gacetas Ecológicas SEMARNAT",
    version="4.0.0",
)
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Montar static si existe
if (DASHBOARD_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR / "static")), name="static")


# ---------------------------------------------------------------------------
# Helpers SSE
# ---------------------------------------------------------------------------

async def _event_stream(gen) -> AsyncGenerator[str, None]:
    """Convierte un generador síncrono en stream SSE async."""
    try:
        for event in gen:
            data = json.dumps(event, ensure_ascii=False, default=str)
            yield f"data: {data}\n\n"
            await asyncio.sleep(0)
    except asyncio.CancelledError:
        pass


def _sse_response(gen, session_id: str = "default") -> StreamingResponse:
    """Crea StreamingResponse SSE con headers correctos."""
    return StreamingResponse(
        _event_stream(gen),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Rutas — Health & Status
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    """Sirve el dashboard SPA."""
    index = DASHBOARD_DIR / "index.html"
    if index.exists():
        return HTMLResponse(content=index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Zohar v4 — Dashboard no disponible</h1>", status_code=503)


@app.get("/api/status", tags=["system"])
async def api_status():
    """Retorna métricas del sistema: CPU, RAM, disco, uptime, Second Brain."""
    boot_time = psutil.boot_time()
    uptime_sec = int(time.time() - boot_time)
    disk = psutil.disk_usage("/")

    # Métricas de Second Brain si existe
    sb_dir = BASE_DIR / "second_brain"
    sb_stats = {"total_notes": 0, "sources": 0, "entities": 0, "inferences": 0}
    if sb_dir.exists():
        sb_stats["total_notes"] = len(list(sb_dir.rglob("*.md")))
        sb_stats["sources"] = len(list((sb_dir / "01_Sources").glob("*.md")))
        sb_stats["entities"] = len(list((sb_dir / "02_Entities").glob("*.md")))
        sb_stats["inferences"] = len(list((sb_dir / "03_Inferences").glob("*.md")))

    return {
        "status": "ok",
        "uptime_seconds": uptime_sec,
        "cpu_pct": psutil.cpu_percent(interval=0.1),
        "ram_pct": psutil.virtual_memory().percent,
        "ram_used_gb": round(psutil.virtual_memory().used / 1e9, 2),
        "disk_free_gb": round(disk.free / 1e9, 2),
        "disk_used_pct": disk.percent,
        "platform": platform.system(),
        "python": platform.python_version(),
        "second_brain": sb_stats,
    }


# ---------------------------------------------------------------------------
# Rutas — Corpus PDF
# ---------------------------------------------------------------------------

@app.get("/api/corpus/pdfs", tags=["corpus"])
async def list_pdfs():
    """Lista todos los PDFs del corpus con metadata."""
    pdfs = []
    for folder_name, folder_path in [
        ("resumenes",   RESUMENES_DIR),
        ("estudios",    ESTUDIOS_DIR),
        ("resolutivos", RESOLUTIVOS_DIR),
        ("gacetas",     GACETAS_DIR),
    ]:
        if not folder_path.exists():
            continue
        for pdf in sorted(folder_path.glob("*.pdf")):
            stat = pdf.stat()
            pdfs.append({
                "name":        pdf.name,
                "folder":      folder_name,
                "size_bytes":  stat.st_size,
                "size_mb":     round(stat.st_size / 1e6, 2),
                "modified_ts": stat.st_mtime,
                "path":        _safe_relative_path(pdf),
            })
    return {"pdfs": pdfs, "total": len(pdfs)}


@app.get("/stream/single", tags=["corpus"])
async def stream_single(pdf_name: str = Query(..., description="Nombre del PDF")):
    """
    SSE: extrae páginas de un PDF como Markdown en tiempo real y persiste .md en disco.
    Aplica caché: si el .md ya existe y es más nuevo que el PDF, simula el streaming rápido.
    """
    from core.pdf_processor import iter_pages_as_markdown, classify_page

    pdf_path = None
    for folder in [RESUMENES_DIR, ESTUDIOS_DIR, RESOLUTIVOS_DIR, GACETAS_DIR, EXTRACTIONS_DIR]:
        candidate = folder / pdf_name
        if candidate.exists():
            pdf_path = candidate
            break

    if pdf_path is None:
        raise HTTPException(404, detail=f"PDF no encontrado: {pdf_name}")

    session_id = pdf_name
    _sse_stop[session_id] = False

    # Comprobar si existe caché del MD válida
    md_filename = Path(pdf_name).stem + ".md"
    md_path = EXTRACTIONS_DIR / md_filename
    if md_path.exists() and md_path.stat().st_mtime >= pdf_path.stat().st_mtime:
        # Servir desde caché simulando stream rápido
        logger.info("Sirviendo extracción desde caché para %s", pdf_name)

        def gen_cached():
            yield {"status": "progress", "msg": "Servido desde caché local...", "pct": 0}
            content = md_path.read_text(encoding="utf-8", errors="replace")
            # Separar por páginas (pymupdf4llm usa '---' o similar, o simplemente dividimos)
            # Para simular, dividimos por el separador standard '\n\n---\n\n'
            pages = content.split("\n\n---\n\n")
            if len(pages) <= 1:
                # Si no tiene separador, intentar por '---'
                pages = content.split("\n---\n")

            # Quitar la cabecera si existe
            if pages and pages[0].startswith(f"# {Path(pdf_name).stem}"):
                # Opcional: limpiar la cabecera del primer elemento de stream si se prefiere
                pass

            total = len(pages)
            for page_num, page_content in enumerate(pages, 1):
                if _sse_stop.get(session_id):
                    yield {"status": "stopped", "msg": "Extracción detenida"}
                    return
                
                blocks = classify_page(page_content)
                yield {
                    "status": "progress",
                    "page": page_num,
                    "total": total,
                    "pct": int(100 * page_num / max(total, 1)),
                    "is_scanned": len(page_content.strip()) < 80,
                    "md": page_content[:3000],
                    "blocks": blocks,
                }
                # Un pequeño sleep no bloqueante para simular visualmente la barra de progreso
                # en vez de escupirlo todo en 1ms.
                time.sleep(0.02)

            yield {
                "status": "saved",
                "msg": f"MD cargado (Caché): {md_filename}",
                "md_name": md_filename,
                "md_path": _safe_relative_path(md_path),
            }
            yield {"status": "complete", "msg": f"Extracción completa (Caché): {pdf_name}", "md_name": md_filename}

        return _sse_response(gen_cached(), session_id)

    # Si no hay caché válida, ejecutar extracción real
    def gen():
        pages_md: list[str] = []

        for page_num, total, md_text, is_scanned in iter_pages_as_markdown(pdf_path):
            if _sse_stop.get(session_id):
                yield {"status": "stopped", "msg": "Extracción detenida"}
                return

            blocks = classify_page(md_text) if not is_scanned else {}
            pages_md.append(md_text)

            yield {
                "status": "progress",
                "page": page_num,
                "total": total,
                "pct": int(100 * page_num / max(total, 1)),
                "is_scanned": is_scanned,
                "md": md_text[:3000],  # Limitar tamaño SSE
                "blocks": blocks,
            }

        # Persistir extracción completa como .md
        EXTRACTIONS_DIR.mkdir(parents=True, exist_ok=True)
        full_md = (
            f"# {Path(pdf_name).stem}\n\n"
            f"_Extraído de: {pdf_name}_\n\n"
            + "\n\n---\n\n".join(pages_md)
        )
        try:
            md_path.write_text(full_md, encoding="utf-8")
            yield {
                "status": "saved",
                "msg": f"MD guardado: {md_filename}",
                "md_name": md_filename,
                "md_path": _safe_relative_path(md_path),
            }
        except Exception as exc:
            logger.error("Error guardando .md para %s: %s", pdf_name, exc)

        yield {"status": "complete", "msg": f"Extracción completa: {pdf_name}", "md_name": md_filename}

    return _sse_response(gen(), session_id)


@app.get("/stop_single", tags=["corpus"])
async def stop_single(pdf_name: str = Query(...)):
    """Detiene la extracción SSE activa."""
    _sse_stop[pdf_name] = True
    return {"stopped": True, "pdf_name": pdf_name}


# ---------------------------------------------------------------------------
# Rutas — Markdown Lab
# ---------------------------------------------------------------------------

@app.get("/api/md/list", tags=["md"])
async def list_md():
    """Lista archivos .md del directorio de extracciones."""
    mds = []
    if EXTRACTIONS_DIR.exists():
        for md in sorted(EXTRACTIONS_DIR.glob("**/*.md")):
            stat = md.stat()
            mds.append({
                "name":        md.name,
                "path":        _safe_relative_path(md),
                "size_bytes":  stat.st_size,
                "modified_ts": stat.st_mtime,
            })
    return {"mds": mds, "total": len(mds)}


@app.get("/api/md/read", tags=["md"])
async def read_md(filename: str = Query(...), page: int = Query(1), page_size: int = Query(100)):
    """Lee un archivo .md con paginación por líneas."""
    md_path = EXTRACTIONS_DIR / filename
    if not md_path.exists():
        raise HTTPException(404, detail=f"MD no encontrado: {filename}")

    lines = md_path.read_text(encoding="utf-8", errors="replace").split("\n")
    total_lines = len(lines)
    start = (page - 1) * page_size
    end = start + page_size

    return {
        "filename":    filename,
        "page":        page,
        "page_size":   page_size,
        "total_lines": total_lines,
        "total_pages": (total_lines + page_size - 1) // page_size,
        "content":     "\n".join(lines[start:end]),
    }


@app.get("/api/md/download", tags=["md"])
async def download_md(filename: str = Query(..., description="Nombre del archivo .md")):
    """Descarga directa de un archivo .md al navegador."""
    # Sanitizar: solo el nombre del archivo, sin rutas
    safe_name = Path(filename).name
    if not safe_name.endswith(".md"):
        raise HTTPException(400, detail="Solo se permiten archivos .md")
    md_path = EXTRACTIONS_DIR / safe_name
    if not md_path.exists():
        raise HTTPException(404, detail=f"MD no encontrado: {safe_name}")
    return FileResponse(
        path=str(md_path),
        filename=safe_name,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


# ---------------------------------------------------------------------------
# Rutas — Knowledge Graph
# ---------------------------------------------------------------------------

@app.get("/api/graph", tags=["graph"])
async def get_graph(format: str = Query("compact")):
    """Retorna el grafo de conocimiento utilizando invalidación de caché reactiva."""
    from core.graph_builder import build_full_graph

    # Calcular el mtime máximo de los archivos PDF en DOWNLOADS_DIR
    max_pdf_mtime = 0.0
    if DOWNLOADS_DIR.exists():
        for pdf in DOWNLOADS_DIR.rglob("*.pdf"):
            try:
                mtime = pdf.stat().st_mtime
                if mtime > max_pdf_mtime:
                    max_pdf_mtime = mtime
            except Exception:
                pass

    # Intentar cargar caché si es más nueva que la última descarga/modificación
    cache_path = DATA_DIR / "graph_cache.json"
    if cache_path.exists():
        cache_mtime = cache_path.stat().st_mtime
        # Si la caché se modificó después del último PDF descargado, es válida
        if cache_mtime >= max_pdf_mtime:
            try:
                return JSONResponse(json.loads(cache_path.read_text(encoding="utf-8")))
            except Exception as exc:
                logger.warning("Error leyendo cache de grafo, se regenerará: %s", exc)

    logger.info("Regenerando grafo de conocimiento (cache expirada o no existe)")
    graph = build_full_graph(DOWNLOADS_DIR)

    # Guardar caché
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        cache_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.error("No se pudo escribir cache de grafo: %s", exc)

    return graph


# ---------------------------------------------------------------------------
# Rutas — Inferencia
# ---------------------------------------------------------------------------

@app.get("/api/inference", tags=["inference"])
async def list_inference():
    """Lista estudios disponibles para inferencia."""
    estudios = []
    if ESTUDIOS_DIR.exists():
        for pdf in sorted(ESTUDIOS_DIR.glob("*.pdf")):
            md_name = pdf.stem + ".md"
            md_path = EXTRACTIONS_DIR / md_name
            estudios.append({
                "pdf_name": pdf.name,
                "md_name":  md_name,
                "md_ready": md_path.exists(),
                "size_mb":  round(pdf.stat().st_size / 1e6, 2),
            })
    return {"estudios": estudios, "total": len(estudios)}


@app.get("/api/inference/{filename}", tags=["inference"])
async def get_inference(filename: str):
    """
    Genera o retorna reporte de inferencia para un estudio con caché reactiva.
    Invalidación basada en el mtime del archivo MD de origen.
    """
    from core.inference_engine import generate_report

    md_path = EXTRACTIONS_DIR / filename
    if not md_path.exists():
        raise HTTPException(404, detail=f"Estudio MD no encontrado: {filename}")

    # Estructura de caché de inferencia
    inference_cache_dir = DATA_DIR / "inference_cache"
    inference_cache_path = inference_cache_dir / (Path(filename).stem + ".json")

    # Intentar recuperar desde caché si es válida
    if inference_cache_path.exists():
        try:
            cache_mtime = inference_cache_path.stat().st_mtime
            md_mtime = md_path.stat().st_mtime
            if cache_mtime >= md_mtime:
                logger.info("Sirviendo reporte de inferencia desde caché para %s", filename)
                return JSONResponse(json.loads(inference_cache_path.read_text(encoding="utf-8")))
        except Exception as exc:
            logger.warning("Error leyendo cache de inferencia para %s: %s", filename, exc)

    # Generar reporte real
    logger.info("Generando reporte de inferencia (Gemini) para %s", filename)
    report = generate_report(md_path)

    # Guardar en caché
    inference_cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        inference_cache_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.error("No se pudo escribir cache de inferencia para %s: %s", filename, exc)

    return report


# ---------------------------------------------------------------------------
# Rutas — Scraper SSE
# ---------------------------------------------------------------------------

def _extract_year_from_name(name: str) -> Optional[int]:
    """Helper para extraer el año de una gaceta por su formato de nombre."""
    import re
    # Formato ASEA: ASEA_GACETA_01-2026.pdf
    m = re.search(r"20\d{2}", name)
    if m:
        return int(m.group(0))
    # Formato SINAT: gaceta_0001-26.pdf
    m2 = re.search(r"-(\d{2})\.pdf$", name.lower())
    if m2:
        return 2000 + int(m2.group(1))
    return None


@app.get("/api/scraper/extract-keys", tags=["scraper"])
async def extract_keys(year: int = Query(2026), source: str = Query("sinat", description="sinat | asea | all")):
    r"""
    SSE: Extrae claves SINAT del contenido de texto de las gacetas del año dado.
    Valida el formato de clave SEMARNAT (\d{2}[A-Z]{2}\d{4}[A-Z0-9]\d{3,5})
    antes de escribir al CSV.
    """
    from scrapers.gazette_scraper import GazetteScraper
    from scrapers.asea_scraper import ASEAScraper
    from core.pdf_processor import iter_pages_as_markdown
    import csv

    csv_path = DATA_DIR / f"claves_{year}.csv"

    def gen():
        # Cargar claves existentes para no pisar la otra fuente
        existing_claves = []
        seen_claves = set()
        if csv_path.exists() and source != "all":
            try:
                with open(csv_path, "r", encoding="utf-8") as f:
                    reader = csv.reader(f)
                    header = next(reader, None)
                    for row in reader:
                        if len(row) >= 3:
                            row_clave, row_year, row_file = row
                            # Si es placeholder huérfano, omitirlo
                            if row_clave == "23QR2024TD085" and not row_file:
                                continue
                            is_row_asea = row_file and ("asea" in row_file.lower() or Path(row_file).name.startswith("ASEA_"))
                            if source == "sinat" and is_row_asea:
                                existing_claves.append({"CLAVE": row_clave, "YEAR": int(row_year), "FILE": row_file})
                                seen_claves.add(row_clave)
                            elif source == "asea" and not is_row_asea:
                                existing_claves.append({"CLAVE": row_clave, "YEAR": int(row_year), "FILE": row_file})
                                seen_claves.add(row_clave)
            except Exception as exc:
                logger.warning("Error leyendo CSV existente para preservar claves: %s", exc)

        yield {"status": "progress", "msg": f"Descargando/Verificando gacetas ecológicas ({source}) {year}...", "pct": 5}

        gacetas_descargadas = []

        # 1. Obtener gacetas de SINAT si corresponde
        if source == "sinat" or source == "all":
            yield {"status": "progress", "msg": f"Buscando gacetas SINAT {year}...", "pct": 10}
            scraper = GazetteScraper(output_dir=str(GACETAS_DIR))
            sinat_files = []
            for event in scraper._descargar_gacetas_ano_gen(year):
                # Emitir progreso sin terminar la conexión
                if event.get("status") == "progress":
                    yield {**event, "pct": 10 + int(event.get("pct", 0) * 0.2)}
                elif event.get("status") == "complete":
                    sinat_files = event.get("files", [])
            
            if not sinat_files:
                # Fallback local para SINAT
                sinat_files = [str(f) for f in GACETAS_DIR.glob("*.pdf") if _extract_year_from_name(f.name) == year]
            gacetas_descargadas.extend(sinat_files)

        # 2. Obtener gacetas de ASEA si corresponde
        if source == "asea" or source == "all":
            yield {"status": "progress", "msg": f"Buscando gacetas ASEA {year}...", "pct": 30}
            asea_dir = GACETAS_DIR / "asea"
            asea_scraper = ASEAScraper(output_dir=str(asea_dir), year_filter=year)
            asea_files = []
            for event in asea_scraper.descargar_gacetas_gen():
                if event.get("status") == "progress":
                    yield {**event, "pct": 30 + int(event.get("pct", 0) * 0.2)}
                elif event.get("status") == "complete":
                    asea_files = event.get("files", [])
            
            if not asea_files and asea_dir.exists():
                # Fallback local para ASEA
                asea_files = [str(f) for f in asea_dir.glob("*.pdf") if _extract_year_from_name(f.name) == year]
            gacetas_descargadas.extend(asea_files)

        if not gacetas_descargadas:
            yield {
                "status": "warning",
                "msg": f"No hay archivos de gacetas PDF locales ni disponibles en la web para el origen {source} y año {year}",
                "level": "warning",
                "pct": 90,
            }

        yield {"status": "progress", "msg": f"Procesando {len(gacetas_descargadas)} gacetas para extraer claves SINAT...", "pct": 60}

        new_claves = []
        for idx, g_pdf in enumerate(gacetas_descargadas):
            g_pdf_path = Path(g_pdf)
            pct = 60 + int(35 * (idx + 1) / max(len(gacetas_descargadas), 1))
            
            # Intentar obtener el archivo Markdown convertido en extractions/
            md_path = EXTRACTIONS_DIR / f"{g_pdf_path.stem}.md"
            text_content = ""
            
            if md_path.exists():
                text_content = md_path.read_text(encoding="utf-8", errors="ignore")
            else:
                # Si no está pre-convertido, extraer texto del PDF al vuelo
                try:
                    pages = []
                    for _, _, md_text, _ in iter_pages_as_markdown(g_pdf_path):
                        pages.append(md_text)
                    text_content = "\n".join(pages)
                    # Persistir la conversión para MD_LAB y caché
                    EXTRACTIONS_DIR.mkdir(parents=True, exist_ok=True)
                    md_path.write_text(text_content, encoding="utf-8")
                except Exception as exc:
                    logger.error("Error leyendo contenido de %s: %s", g_pdf_path.name, exc)
                    yield {
                        "status": "progress",
                        "msg": f"Gaceta warning: no se pudo leer {g_pdf_path.name}",
                        "pct": pct,
                        "level": "warning",
                    }
                    continue

            # Buscar todas las claves válidas en el texto
            found_keys = _CLAVE_RE.findall(text_content.upper())
            gaceta_count = 0
            for clave in found_keys:
                if clave not in seen_claves:
                    seen_claves.add(clave)
                    new_claves.append({"CLAVE": clave, "YEAR": year, "FILE": str(g_pdf_path)})
                    gaceta_count += 1

            if gaceta_count > 0:
                yield {
                    "status": "progress",
                    "msg": f"Extraídas {gaceta_count} claves de: {g_pdf_path.name}",
                    "pct": pct,
                }

        # Generar CSV combinando existentes y nuevas
        final_claves = existing_claves + new_claves
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if final_claves:
            # Ordenar para consistencia
            final_claves.sort(key=lambda x: (x.get("FILE", ""), x.get("CLAVE", "")))
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["CLAVE", "YEAR", "FILE"])
                writer.writeheader()
                writer.writerows(final_claves)
        else:
            # CSV mínimo para tests
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["CLAVE", "YEAR", "FILE"])
                writer.writeheader()
                writer.writerow({"CLAVE": "23QR2024TD085", "YEAR": year, "FILE": ""})

        yield {
            "status": "complete",
            "msg": f"CSV generado: {csv_path.name} ({len(final_claves)} claves de proyectos válidas)",
            "csv_path": str(csv_path),
            "n_claves": len(final_claves),
            "n_invalidas": 0,
        }

    return _sse_response(gen())


@app.get("/api/scraper/extract-pipeline-md", tags=["scraper"])
async def extract_pipeline_md(force: bool = Query(False), source: str = Query("sinat", description="sinat | asea | all")):
    """
    SSE: Extrae texto Markdown de todos los PDFs en GACETAS_DIR (o subdirectorios según el origen) y lo guarda
    en EXTRACTIONS_DIR/<pdf_stem>.md. Salta archivos ya extraídos (cache mtime)
    a menos que force=true.
    """
    from core.pdf_processor import iter_pages_as_markdown

    def gen():
        EXTRACTIONS_DIR.mkdir(parents=True, exist_ok=True)

        if not GACETAS_DIR.exists():
            yield {"status": "complete", "msg": "No hay gacetas descargadas", "pct": 100, "n_extracted": 0}
            return

        # Recopilar todos los PDFs de gacetas según el origen
        all_pdfs = []
        if source == "sinat" or source == "all":
            all_pdfs.extend(list(GACETAS_DIR.glob("*.pdf")))
        if source == "asea" or source == "all":
            asea_dir = GACETAS_DIR / "asea"
            if asea_dir.exists():
                all_pdfs.extend(list(asea_dir.glob("*.pdf")))

        all_pdfs = sorted(all_pdfs, key=lambda x: x.name)
        if not all_pdfs:
            yield {"status": "complete", "msg": f"No hay PDFs en gacetas ({source})", "pct": 100, "n_extracted": 0}
            return

        yield {"status": "progress", "msg": f"Encontrados {len(all_pdfs)} PDFs para extraer ({source})", "pct": 2}

        n_extracted = 0
        n_skipped = 0

        for i, pdf_path in enumerate(all_pdfs):
            pct = 2 + int(95 * (i + 1) / len(all_pdfs))
            md_path = EXTRACTIONS_DIR / (pdf_path.stem + ".md")

            # Cache: saltar si el .md existe y es más nuevo que el PDF
            if not force and md_path.exists():
                pdf_mtime = pdf_path.stat().st_mtime
                md_mtime = md_path.stat().st_mtime
                if md_mtime >= pdf_mtime:
                    n_skipped += 1
                    yield {"status": "progress", "msg": f"Omitido (cache): {pdf_path.name}", "pct": pct}
                    continue

            yield {"status": "progress", "msg": f"Extrayendo: {pdf_path.name}", "pct": pct}

            try:
                pages: list[str] = []
                for _, _, md_text, _ in iter_pages_as_markdown(pdf_path):
                    pages.append(md_text)

                full_md = (
                     f"# {pdf_path.stem}\n\n"
                     f"_Extraído de: {pdf_path.name}_\n\n"
                     + "\n\n---\n\n".join(pages)
                )
                md_path.write_text(full_md, encoding="utf-8")
                n_extracted += 1
            except Exception as exc:
                logger.error("Error extrayendo %s: %s", pdf_path.name, exc)
                yield {"status": "warning", "msg": f"Error en {pdf_path.name}: {exc}", "pct": pct, "level": "warning"}

        yield {
            "status": "complete",
            "msg": f"Pipeline MD: {n_extracted} extraídos, {n_skipped} en cache",
            "pct": 100,
            "n_extracted": n_extracted,
            "n_skipped": n_skipped,
        }

    return _sse_response(gen())


@app.get("/api/scraper/run-pipeline", tags=["scraper"])
async def run_pipeline(year: int = Query(2026), source: str = Query("all", description="sinat | asea | all"), rebuild_wiki: bool = Query(True)):
    """
    SSE: Ejecuta el pipeline completo de ingestión.
    Etapas: gacetas ASEA → gacetas SINAT → conversión MD → extracción claves → grafo → Second Brain.
    """
    from core.graph_builder import build_full_graph
    from core.pdf_processor import iter_pages_as_markdown
    import csv as csv_module

    def gen():
        yield {"status": "progress", "msg": "Iniciando pipeline...", "pct": 0, "stage": "init"}

        # Etapa 1: Gacetas ASEA
        if source == "asea" or source == "all":
            yield {"status": "progress", "msg": "Etapa 1/6: Gacetas ASEA", "pct": 5, "stage": "asea"}
            try:
                from scrapers.asea_scraper import ASEAScraper
                asea = ASEAScraper(output_dir=str(GACETAS_DIR / "asea"), year_filter=year)
                for event in asea.descargar_gacetas_gen():
                    yield {**event, "stage": "asea"}
            except Exception as exc:
                yield {"status": "progress", "msg": f"ASEA warning: {exc}", "pct": 20, "level": "warning"}
        else:
            yield {"status": "progress", "msg": "Saltando Etapa 1: Gacetas ASEA", "pct": 20}

        # Etapa 2: Gacetas SINAT
        if source == "sinat" or source == "all":
            yield {"status": "progress", "msg": "Etapa 2/6: Gacetas SINAT", "pct": 22, "stage": "sinat"}
            try:
                from scrapers.gazette_scraper import GazetteScraper
                sinat = GazetteScraper(output_dir=str(GACETAS_DIR))
                for event in sinat._descargar_gacetas_ano_gen(year):
                    yield {**event, "stage": "sinat"}
            except Exception as exc:
                yield {"status": "progress", "msg": f"SINAT warning: {exc}", "pct": 44, "level": "warning"}
        else:
            yield {"status": "progress", "msg": "Saltando Etapa 2: Gacetas SINAT", "pct": 44}

        # Etapa 3: Conversión MD (todas las gacetas pendientes de este origen y año)
        yield {"status": "progress", "msg": "Etapa 3/6: Convirtiendo gacetas a Markdown...", "pct": 46, "stage": "md_extraction"}
        EXTRACTIONS_DIR.mkdir(parents=True, exist_ok=True)
        
        all_pdfs = []
        if source == "sinat" or source == "all":
            all_pdfs.extend([f for f in GACETAS_DIR.glob("*.pdf") if _extract_year_from_name(f.name) == year])
        if source == "asea" or source == "all":
            asea_dir = GACETAS_DIR / "asea"
            if asea_dir.exists():
                all_pdfs.extend([f for f in asea_dir.glob("*.pdf") if _extract_year_from_name(f.name) == year])
        
        all_pdfs = sorted(all_pdfs, key=lambda x: x.name)
        n_md_extracted = 0
        for idx, pdf in enumerate(all_pdfs):
            md_path = EXTRACTIONS_DIR / f"{pdf.stem}.md"
            if not md_path.exists() or md_path.stat().st_size == 0:
                try:
                    pages = []
                    for _, _, md_text, _ in iter_pages_as_markdown(pdf):
                        pages.append(md_text)
                    md_path.write_text("\n".join(pages), encoding="utf-8")
                    n_md_extracted += 1
                    pct = 46 + int(8 * (idx + 1) / max(len(all_pdfs), 1))
                    yield {"status": "progress", "msg": f"MD: {pdf.name}", "pct": pct, "stage": "md_extraction"}
                except Exception as exc:
                    logger.warning("MD warning %s: %s", pdf.name, exc)
        yield {"status": "progress", "msg": f"MD: {n_md_extracted} nuevas extracciones", "pct": 54, "stage": "md_extraction"}

        # Etapa 4: Extracción de claves SINAT
        yield {"status": "progress", "msg": "Etapa 4/6: Extrayendo claves SINAT...", "pct": 55, "stage": "keys"}
        csv_path = DATA_DIR / f"claves_{year}.csv"
        
        # Preservar claves de la otra fuente
        existing_claves = []
        seen_claves = set()
        if csv_path.exists() and source != "all":
            try:
                with open(csv_path, "r", encoding="utf-8") as f:
                    reader = csv_module.reader(f)
                    header = next(reader, None)
                    for row in reader:
                        if len(row) >= 3:
                            row_clave, row_year, row_file = row
                            if row_clave == "23QR2024TD085" and not row_file:
                                continue
                            is_row_asea = row_file and ("asea" in row_file.lower() or Path(row_file).name.startswith("ASEA_"))
                            if source == "sinat" and is_row_asea:
                                existing_claves.append({"CLAVE": row_clave, "YEAR": int(row_year), "FILE": row_file})
                                seen_claves.add(row_clave)
                            elif source == "asea" and not is_row_asea:
                                existing_claves.append({"CLAVE": row_clave, "YEAR": int(row_year), "FILE": row_file})
                                seen_claves.add(row_clave)
            except Exception as exc:
                logger.warning("Error leyendo CSV existente para preservar claves en run_pipeline: %s", exc)

        new_claves = []
        for idx, g_pdf in enumerate(all_pdfs):
            md_path = EXTRACTIONS_DIR / f"{g_pdf.stem}.md"
            if md_path.exists() and md_path.stat().st_size > 0:
                text = md_path.read_text(encoding="utf-8", errors="ignore")
                for clave in _CLAVE_RE.findall(text.upper()):
                    if clave not in seen_claves:
                        seen_claves.add(clave)
                        new_claves.append({"CLAVE": clave, "YEAR": year, "FILE": str(g_pdf)})
                        
        final_claves = existing_claves + new_claves
        if final_claves:
            final_claves.sort(key=lambda x: (x.get("FILE", ""), x.get("CLAVE", "")))
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv_module.DictWriter(f, fieldnames=["CLAVE", "YEAR", "FILE"])
                writer.writeheader()
                writer.writerows(final_claves)
        yield {"status": "progress", "msg": f"Claves: {len(final_claves)} encontradas en total ({len(all_pdfs)} gacetas procesadas)", "pct": 65, "stage": "keys"}

        # Etapa 5: Rebuild Grafo
        if rebuild_wiki:
            yield {"status": "progress", "msg": "Etapa 5/6: Rebuilding grafo...", "pct": 67, "stage": "graph"}
            try:
                graph = build_full_graph(DOWNLOADS_DIR)
                cache_path = DATA_DIR / "graph_cache.json"
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
                yield {"status": "progress", "msg": f"Grafo: {graph['metrics']['n_nodes']} nodos", "pct": 80}
            except Exception as exc:
                yield {"status": "progress", "msg": f"Grafo warning: {exc}", "pct": 80, "level": "warning"}

            # Etapa 6: Sincronizar Second Brain
            yield {"status": "progress", "msg": "Etapa 6/6: Sincronizando Second Brain...", "pct": 82, "stage": "second_brain"}
            try:
                from core.second_brain import SecondBrainBuilder
                stats = SecondBrainBuilder(BASE_DIR).build_vault()
                yield {"status": "progress", "msg": f"Second Brain: {stats['total_proyectos']} proyectos, {stats['total_gacetas']} gacetas", "pct": 95}
            except Exception as exc:
                yield {"status": "progress", "msg": f"Second Brain warning: {exc}", "pct": 95, "level": "warning"}

        yield {
            "status": "complete",
            "msg": "Pipeline completado",
            "pct": 100,
            "stage": "done",
            "year": year,
        }

    return _sse_response(gen())


@app.get("/api/scraper/download-clave", tags=["scraper"])
async def download_clave(clave: str = Query(..., description="Clave SINAT a descargar (ej. 05CO2026I0001)"), year: int = Query(2026)):
    """
    SSE: Descarga los archivos del trámite (estudio, resumen, resolutivo) para una clave SINAT
    usando SemarnatDownloader (Selenium). Tras la descarga ejecuta la conversión a Markdown
    y opcionalmente lanza la inferencia si hay GEMINI_API_KEY configurada.
    """
    from core.pdf_processor import iter_pages_as_markdown
    from core.inference_engine import generate_report
    import json as json_module

    clave = clave.strip().upper()
    if not _CLAVE_RE.match(clave):
        async def error_gen():
            yield {"status": "error", "msg": f"Clave inválida: {clave}", "pct": 100}
        return _sse_response(error_gen())

    def gen():
        yield {"status": "progress", "msg": f"Iniciando descarga para clave: {clave}", "pct": 0, "clave": clave}

        # Etapa 1: Descarga SEMARNAT vía Selenium
        yield {"status": "progress", "msg": "Conectando con portal SEMARNAT (Selenium)...", "pct": 5}
        try:
            from scrapers.semarnat_downloader import SemarnatDownloader
            downloader = SemarnatDownloader(
                download_dir=str(DOWNLOADS_DIR),
                estudios_dir=str(ESTUDIOS_DIR),
                resumenes_dir=str(RESUMENES_DIR),
                resolutivos_dir=str(RESOLUTIVOS_DIR),
            )
            for event in downloader._descargar_clave_gen(clave):
                yield {**event, "pct": min(5 + int(event.get("pct", 0) * 0.55), 60)}
        except Exception as exc:
            yield {"status": "progress", "msg": f"Descarga warning: {exc}", "pct": 60, "level": "warning"}

        # Etapa 2: Conversión a Markdown
        yield {"status": "progress", "msg": "Convirtiendo PDFs descargados a Markdown...", "pct": 62}
        estudio_pdf = ESTUDIOS_DIR / f"{clave}.pdf"
        md_path = EXTRACTIONS_DIR / f"{clave}.md"
        if estudio_pdf.exists():
            try:
                pages = []
                for _, _, md_text, _ in iter_pages_as_markdown(estudio_pdf):
                    pages.append(md_text)
                EXTRACTIONS_DIR.mkdir(parents=True, exist_ok=True)
                md_path.write_text("\n".join(pages), encoding="utf-8")
                yield {"status": "progress", "msg": f"Markdown extraido: {md_path.name} ({md_path.stat().st_size} bytes)", "pct": 75}
            except Exception as exc:
                yield {"status": "progress", "msg": f"Conversión MD warning: {exc}", "pct": 75, "level": "warning"}
        else:
            yield {"status": "progress", "msg": "Sin estudio PDF disponible para conversión", "pct": 75, "level": "warning"}

        # Etapa 3: Inferencia (si hay MD y API key)
        if md_path.exists():
            yield {"status": "progress", "msg": "Ejecutando inferencia...", "pct": 77}
            try:
                cache_path = DATA_DIR / "inference_cache" / f"{clave}.json"
                report = generate_report(md_path)
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                (DATA_DIR / "inference_cache").mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json_module.dumps(report, ensure_ascii=False), encoding="utf-8")
                yield {"status": "progress", "msg": f"Inferencia: {report.get('veredicto', 'SIN DICTAMEN')} (score={report.get('score', 0):.2f})", "pct": 88}
            except Exception as exc:
                yield {"status": "progress", "msg": f"Inferencia warning: {exc}", "pct": 88, "level": "warning"}

        # Etapa 4: Actualizar Second Brain
        yield {"status": "progress", "msg": "Actualizando base de conocimiento...", "pct": 90}
        try:
            from core.second_brain import SecondBrainBuilder
            stats = SecondBrainBuilder(BASE_DIR).build_vault()
            yield {"status": "progress", "msg": f"Second Brain actualizado: {stats['total_proyectos']} proyectos", "pct": 97}
        except Exception as exc:
            yield {"status": "progress", "msg": f"Second Brain warning: {exc}", "pct": 97, "level": "warning"}

        yield {
            "status": "complete",
            "msg": f"Pipeline de clave {clave} completado",
            "pct": 100,
            "clave": clave,
        }

    return _sse_response(gen())




@app.post("/api/second_brain/build", tags=["second_brain"])
async def build_second_brain():
    """Ejecuta la sincronización completa del Second Brain de Obsidian."""
    from core.second_brain import SecondBrainBuilder
    from core.semantic_search import SemanticSearchEngine
    builder = SecondBrainBuilder(BASE_DIR)
    engine = SemanticSearchEngine(BASE_DIR)
    try:
        stats = builder.build_vault()
        # Generar o actualizar embeddings para las notas creadas
        try:
            embed_stats = engine.build_index()
            stats["semantic_index"] = embed_stats
        except Exception as emb_exc:
            logger.warning("Error construyendo índice semántico: %s", emb_exc)
            stats["semantic_index"] = {"status": "error", "reason": str(emb_exc)}
            
        return {
            "status": "ok",
            "msg": "Second Brain sincronizado e indexado correctamente",
            "stats": stats
        }
    except Exception as exc:
        logger.error("Error construyendo Second Brain: %s", exc)
        raise HTTPException(500, detail=f"Error en Second Brain: {exc}")


@app.get("/api/second_brain/search", tags=["second_brain"])
async def search_second_brain(q: str = Query(..., description="Consulta de búsqueda semántica")):
    """Realiza una búsqueda semántica de notas del Second Brain."""
    from core.semantic_search import SemanticSearchEngine
    engine = SemanticSearchEngine(BASE_DIR)
    try:
        results = engine.search(q)
        return {"results": results}
    except Exception as exc:
        logger.error("Error en búsqueda semántica: %s", exc)
        raise HTTPException(500, detail=f"Error buscando notas semánticamente: {exc}")



@app.get("/api/second_brain/notes", tags=["second_brain"])
async def list_second_brain_notes():
    """Lista todas las notas de la bóveda del Second Brain agrupadas por categoría."""
    sb_dir = BASE_DIR / "second_brain"
    if not sb_dir.exists():
        return {"notes": []}

    notes = []
    # 00_Index.md en la raíz
    index_file = sb_dir / "00_Index.md"
    if index_file.exists():
        notes.append({
            "name": "00_Index.md",
            "title": "00_Index",
            "category": "root"
        })

    # Subcarpetas
    for folder in ["01_Sources", "02_Entities", "03_Inferences"]:
        folder_path = sb_dir / folder
        if folder_path.exists():
            for md in sorted(folder_path.glob("*.md")):
                notes.append({
                    "name": md.name,
                    "title": md.stem,
                    "category": folder
                })

    return {"notes": notes}


@app.get("/api/second_brain/note", tags=["second_brain"])
async def get_second_brain_note(name: str = Query(..., description="Nombre de la nota (sin extensión)")):
    """Busca y retorna el contenido de una nota de la bóveda por su nombre."""
    sb_dir = BASE_DIR / "second_brain"
    if not sb_dir.exists():
        raise HTTPException(404, detail="Bóveda del Second Brain no encontrada")

    # Buscar recursivamente
    target_filename = f"{name}.md"
    for md in sb_dir.rglob("*.md"):
        if md.name.upper() == target_filename.upper():
            try:
                content = md.read_text(encoding="utf-8", errors="ignore")
                return {
                    "name": md.name,
                    "title": md.stem,
                    "category": md.parent.name if md.parent != sb_dir else "root",
                    "content": content
                }
            except Exception as exc:
                raise HTTPException(500, detail=f"Error leyendo nota: {exc}")

    raise HTTPException(404, detail=f"Nota '{name}' no encontrada en la bóveda")


@app.get("/api/scraper/gacetas-summary", tags=["scraper"])
async def get_gacetas_summary(year: int = Query(2026), source: str = Query("all", description="sinat | asea | all")):
    """
    Retorna el listado de todas las gacetas del año con el conteo de claves
    extraídas de cada una leyendo el CSV.
    """
    csv_path = DATA_DIR / f"claves_{year}.csv"
    
    # Escanear PDFs físicos según origen y año
    gacetas_pdfs = []
    if source == "sinat" or source == "all":
        gacetas_pdfs.extend([f for f in GACETAS_DIR.glob("*.pdf") if _extract_year_from_name(f.name) == year])
    if source == "asea" or source == "all":
        asea_dir = GACETAS_DIR / "asea"
        if asea_dir.exists():
            gacetas_pdfs.extend([f for f in asea_dir.glob("*.pdf") if _extract_year_from_name(f.name) == year])
    
    summary = {}
    for g in gacetas_pdfs:
        summary[g.stem.upper()] = {
            "name": g.name,
            "size_bytes": g.stat().st_size,
            "clave_count": 0
        }

    if csv_path.exists():
        try:
            import csv
            with open(csv_path, "r", encoding="utf-8") as f:
                # Omitir header
                f.readline()
                for row in csv.reader(f):
                    if len(row) >= 3:
                        row_clave, _, row_file = row
                        if row_file:
                            stem = Path(row_file).stem.upper()
                            is_file_asea = "asea" in row_file.lower() or Path(row_file).name.startswith("ASEA_")
                            
                            # Validar que coincida con el origen filtrado
                            if source == "sinat" and is_file_asea:
                                continue
                            if source == "asea" and not is_file_asea:
                                continue
                                
                            if stem in summary:
                                summary[stem]["clave_count"] += 1
                            else:
                                summary[stem] = {
                                    "name": Path(row_file).name,
                                    "size_bytes": 0,
                                    "clave_count": 1
                                }
        except Exception as exc:
            logger.error("Error procesando resumen de gacetas: %s", exc)

    return {"gacetas": list(summary.values())}


@app.get("/api/scraper/gaceta-keys", tags=["scraper"])
async def get_gaceta_keys(gaceta_name: str, year: int = Query(2026)):
    """
    Retorna la lista de claves asociadas a una gaceta específica y el estado
    de procesamiento de cada una (si tiene estudio, resolutivo, inferencia, etc.).
    """
    csv_path = DATA_DIR / f"claves_{year}.csv"
    if not csv_path.exists():
        return {"gaceta": gaceta_name, "claves": []}

    import csv
    target_stem = Path(gaceta_name).stem.upper()
    claves_info = []

    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            # Leer usando csv.reader para robustez
            reader = csv.reader(f)
            # Omitir header
            f.readline()
            for row in reader:
                if len(row) >= 3:
                    row_clave, _, row_file = row
                    
                    # Comprobar si pertenece a la gaceta consultada
                    if row_file and Path(row_file).stem.upper() == target_stem:
                        # Comprobar estado de archivos en disco para esta clave
                        estudio_pdf = DOWNLOADS_DIR / "estudios" / f"{row_clave}.pdf"
                        resumen_pdf = DOWNLOADS_DIR / "resumenes" / f"{row_clave}.pdf"
                        resolutivo_pdf = DOWNLOADS_DIR / "resolutivos" / f"{row_clave}.pdf"
                        extraction_md = EXTRACTIONS_DIR / f"{row_clave}.md"
                        inference_json = DATA_DIR / "inference_cache" / f"{row_clave}.json"

                        claves_info.append({
                            "clave": row_clave,
                            "has_pdf_estudio": estudio_pdf.exists(),
                            "has_pdf_resumen": resumen_pdf.exists(),
                            "has_pdf_resolutivo": resolutivo_pdf.exists(),
                            "has_extraction": extraction_md.exists(),
                            "has_inference": inference_json.exists(),
                        })
    except Exception as exc:
        logger.error("Error leyendo claves para gaceta %s: %s", gaceta_name, exc)

    return {"gaceta": gaceta_name, "claves": claves_info}



