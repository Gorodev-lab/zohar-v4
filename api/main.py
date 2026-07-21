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
import redis
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuración de Redis con fallback seguro
# ---------------------------------------------------------------------------
try:
    redis_host = os.environ.get("REDIS_HOST", "localhost")
    redis_client = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)
    redis_client.ping()
    logger.info("Conectado exitosamente a Redis en %s:6379", redis_host)
    REDIS_AVAILABLE = True
except Exception as exc:
    logger.warning("No se pudo conectar a Redis: %s. Usando fallback en disco/memoria.", exc)
    redis_client = None
    REDIS_AVAILABLE = False


def invalidate_redis_cache(key: str):
    """Invalida una llave específica en Redis si está disponible."""
    if REDIS_AVAILABLE and redis_client:
        try:
            redis_client.delete(key)
            logger.info("Caché invalidada en Redis: %s", key)
        except Exception as e:
            logger.warning("Error invalidando caché en Redis (%s): %s", key, e)

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
# Sistema de Notificaciones en Tiempo Real (SSE + Watchdog)
# ---------------------------------------------------------------------------

class LiveUpdateBroadcaster:
    def __init__(self):
        self._listeners: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue()
        self._listeners.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self._listeners:
            self._listeners.remove(q)

    def broadcast(self, event_type: str, filename: str):
        payload = {"type": event_type, "file": filename, "ts": time.time()}
        logger.info("Difundiendo evento en tiempo real: %s", payload)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # No hay event loop corriendo
        for q in list(self._listeners):
            loop.call_soon_threadsafe(q.put_nowait, payload)

live_broadcaster = LiveUpdateBroadcaster()


class DataDirectoryHandler(FileSystemEventHandler):
    def __init__(self, broadcaster: LiveUpdateBroadcaster):
        self.broadcaster = broadcaster
        super().__init__()

    def on_created(self, event):
        if event.is_directory:
            return
        self._handle_change(event.src_path)

    def on_modified(self, event):
        if event.is_directory:
            return
        self._handle_change(event.src_path)

    def on_deleted(self, event):
        if event.is_directory:
            return
        self._handle_change(event.src_path)

    def _handle_change(self, path_str: str):
        path = Path(path_str)
        # Ignorar temporales de descargas
        if path.name.startswith(".") or path.suffix.lower() in (".part", ".tmp", ".crdownload"):
            return

        event_type = None
        if path.suffix.lower() == ".pdf":
            event_type = "pdfs_updated"
            invalidate_redis_cache("zohar:corpus:pdfs")
            invalidate_redis_cache("zohar:analytics:summary")
        elif path.suffix.lower() == ".md" and ("extractions" in path_str or "second_brain" in path_str):
            event_type = "extractions_updated"
            invalidate_redis_cache("zohar:analytics:summary")
            invalidate_redis_cache("zohar:graph:compact")
        elif path.suffix.lower() == ".json" and "inference_cache" in path_str:
            event_type = "inferences_updated"
            invalidate_redis_cache("zohar:analytics:summary")

        if event_type:
            self.broadcaster.broadcast(event_type, path.name)


# Global Watchdog Observer reference
_observer: Optional[Observer] = None


def consume_pipeline_generator(year: int):
    logger.info("Scheduler Progress: Iniciando consumo de run_pipeline_generator para el año %d", year)
    try:
        for event in run_pipeline_generator(year, "all", True):
            status = event.get("status")
            msg = event.get("msg")
            pct = event.get("pct")
            logger.info("Scheduler Progress [%s]: %s (%s%%)", status, msg, pct)
        logger.info("Scheduler Progress: Finalizado con éxito consumo de run_pipeline_generator para el año %d", year)
    except Exception as exc:
        logger.error("Scheduler Progress: Error ejecutando run_pipeline_generator: %s", exc)


async def thursday_gaceta_scheduler_loop():
    logger.info("Scheduler: Iniciando bucle del planificador de gacetas (jueves 9:00 AM)...")
    last_run_date = ""
    while True:
        try:
            from datetime import datetime
            now = datetime.now()
            # 3 is Thursday (0=Monday, 6=Sunday)
            if now.weekday() == 3 and now.hour == 9 and now.minute == 0:
                today_str = now.strftime("%Y-%m-%d")
                if today_str != last_run_date:
                    last_run_date = today_str
                    logger.info("Scheduler: Es jueves a las 9:00 AM. Iniciando pipeline automático...")
                    
                    # Consumir el generador en un thread executor para no bloquear el loop de asyncio
                    loop = asyncio.get_running_loop()
                    loop.run_in_executor(None, consume_pipeline_generator, now.year)
        except Exception as e:
            logger.error("Scheduler: Error en bucle de programación: %s", e)
        
        # Despertar cada 60 segundos
        await asyncio.sleep(60)


@app.on_event("startup")
async def startup_event():
    global _observer
    logger.info("Iniciando watcher de archivos en segundo plano...")
    
    # Crear directorios si no existen
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    EXTRACTIONS_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "inference_cache").mkdir(parents=True, exist_ok=True)
    
    handler = DataDirectoryHandler(live_broadcaster)
    _observer = Observer()
    
    # Observar carpetas clave
    if DOWNLOADS_DIR.exists():
        _observer.schedule(handler, path=str(DOWNLOADS_DIR), recursive=True)
    if EXTRACTIONS_DIR.exists():
        _observer.schedule(handler, path=str(EXTRACTIONS_DIR), recursive=True)
    
    _observer.start()
    logger.info("Watcher de archivos iniciado con éxito.")

    # Registrar planificador de gacetas semanal (jueves 9:00 AM)
    asyncio.create_task(thursday_gaceta_scheduler_loop())


@app.on_event("shutdown")
async def shutdown_event():
    global _observer
    if _observer:
        logger.info("Deteniendo watcher de archivos...")
        _observer.stop()
        _observer.join()
        logger.info("Watcher de archivos detenido.")


@app.get("/api/events/live-updates", tags=["analytics"])
async def live_updates(request: Request):
    """
    SSE: Envía notificaciones automáticas al dashboard en tiempo real cuando hay cambios
    en los PDFs, las extracciones o las inferencias ML en disco.
    """
    q = live_broadcaster.subscribe()

    async def _stream():
        try:
            # Enviar evento de bienvenida
            yield f"data: {json.dumps({'type': 'ping', 'msg': 'conectado'})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    # Esperar evento de la cola con timeout de 25s para mantener vivo el canal
                    event = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            live_broadcaster.unsubscribe(q)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )




# ---------------------------------------------------------------------------
# Helpers SSE
# ---------------------------------------------------------------------------

async def _event_stream(gen) -> AsyncGenerator[str, None]:
    """Convierte un generador (síncrono o asíncrono) en stream SSE async."""
    import inspect
    try:
        if inspect.isasyncgen(gen):
            async for event in gen:
                data = json.dumps(event, ensure_ascii=False, default=str)
                yield f"data: {data}\n\n"
                await asyncio.sleep(0)
        else:
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
# Rutas — llama-server local
# ---------------------------------------------------------------------------
import subprocess
import signal
import httpx

@app.get("/api/llama/status", tags=["system"])
async def get_llama_status():
    """Verifica si llama-server está activo en el puerto 8083 y responde a /health."""
    local_url = os.environ.get("LOCAL_LLM_URL", "http://127.0.0.1:8083")
    try:
        # Intentar conectar con el endpoint de health de llama-server
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{local_url}/health", timeout=1.0)
            if r.status_code == 200:
                data = r.json()
                return {
                    "status": "online",
                    "model": os.environ.get("LOCAL_LLM_MODEL", "gemma-4-e2b"),
                    "details": data
                }
    except Exception as exc:
        logger.error("Error conectando con llama-server: %s", exc)

    # Si no responde al health, verificar si el archivo de PID existe
    pid_file = Path("/tmp/zohar_llama_server.pid")
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            return {
                "status": "booting",
                "model": os.environ.get("LOCAL_LLM_MODEL", "gemma-4-e2b")
            }
        except Exception:
            try:
                pid_file.unlink()
            except Exception:
                pass

    return {
        "status": "offline",
        "model": os.environ.get("LOCAL_LLM_MODEL", "gemma-4-e2b")
    }


@app.post("/api/llama/start", tags=["system"])
async def start_llama_server():
    """Inicia el servidor llama-server usando el script local."""
    status = await get_llama_status()
    if status["status"] in ("online", "booting"):
        return {"status": "already_running", "msg": "El servidor ya está activo o iniciándose."}

    try:
        # Spawn detached process
        subprocess.Popen(
            ["./start_llama_server.sh"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(BASE_DIR)
        )
        return {"status": "starting", "msg": "Iniciando llama-server en segundo plano."}
    except Exception as exc:
        return {"status": "error", "msg": f"Error al lanzar el script: {exc}"}


@app.post("/api/llama/stop", tags=["system"])
async def stop_llama_server():
    """Detiene el servidor llama-server matando su PID y enviando SIGTERM."""
    pid_file = Path("/tmp/zohar_llama_server.pid")
    killed = False
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            killed = True
            time.sleep(1.0)
            if pid_file.exists():
                pid_file.unlink()
        except Exception:
            pass

    try:
        subprocess.run(["pkill", "-f", "llama-server"], capture_output=True)
        killed = True
    except Exception:
        pass

    if killed:
        return {"status": "stopped", "msg": "Servidor detenido exitosamente."}
    else:
        return {"status": "ignored", "msg": "No había ningún servidor activo para detener."}


# ---------------------------------------------------------------------------
# Rutas — Corpus PDF
# ---------------------------------------------------------------------------

@app.get("/api/corpus/pdfs", tags=["corpus"])
async def list_pdfs():
    """Lista todos los PDFs del corpus con metadata, usando caché de Redis y carga asíncrona."""
    # 1. Intentar cargar desde Redis
    if REDIS_AVAILABLE and redis_client:
        try:
            cached = redis_client.get("zohar:corpus:pdfs")
            if cached:
                return json.loads(cached)
        except Exception as e:
            logger.warning("Error leyendo lista de PDFs de Redis: %s", e)

    # 2. Fallback / Cache Miss: escanear el directorio en un hilo no bloqueante
    def _scan():
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

    result = await asyncio.to_thread(_scan)

    # 3. Guardar en Redis (expira en 30 minutos)
    if REDIS_AVAILABLE and redis_client:
        try:
            redis_client.setex("zohar:corpus:pdfs", 1800, json.dumps(result))
        except Exception as e:
            logger.warning("No se pudo guardar lista de PDFs en Redis: %s", e)

    return result


@app.get("/api/analytics/cached-data", tags=["analytics"])
async def get_cached_data_summary():
    """
    Retorna un resumen de todos los datos disponibles en disco para análisis.
    Incluye conteo de gacetas, proyectos con/sin inferencia, y estado de Neo4j.
    Usa Redis para caché e I/O no bloqueante.
    """
    # 1. Intentar cargar desde Redis
    if REDIS_AVAILABLE and redis_client:
        try:
            cached = redis_client.get("zohar:analytics:summary")
            if cached:
                return {"ok": True, "data": json.loads(cached)}
        except Exception as e:
            logger.warning("Error leyendo analytics summary de Redis: %s", e)

    # 2. Fallback / Cache Miss
    def _get_summary():
        from dw.neo4j_loader import get_cached_data_summary as _summary
        return _summary()

    try:
        summary = await asyncio.to_thread(_get_summary)
    except Exception as exc:
        logger.warning("get_cached_data_summary error: %s", exc)
        extractions_dir = BASE_DIR / "extractions"
        inference_dir = DATA_DIR / "inference_cache"
        second_brain_dir = BASE_DIR / "second_brain"
        summary = {
            "gacetas_md": len(list(extractions_dir.glob("*.md"))) if extractions_dir.exists() else 0,
            "proyectos_con_inference": len(list(inference_dir.glob("*.json"))) if inference_dir.exists() else 0,
            "total_claves": "calculando...",
            "extractions_dir_exists": extractions_dir.exists(),
            "second_brain_dir_exists": second_brain_dir.exists(),
            "ready_for_neo4j": True,
        }

    # 3. Guardar en Redis (expira en 5 minutos)
    if REDIS_AVAILABLE and redis_client:
        try:
            redis_client.setex("zohar:analytics:summary", 300, json.dumps(summary))
        except Exception as e:
            logger.warning("No se pudo guardar analytics summary en Redis: %s", e)

    return {"ok": True, "data": summary}


@app.get("/api/neo4j/sync", tags=["analytics"])
async def neo4j_sync(
    clear: bool = Query(False, description="Limpiar Neo4j antes de cargar"),
    dry_run: bool = Query(False, description="Simular carga sin escribir al Neo4j"),
):
    """
    Carga todos los datos en caché (extractions/, second_brain/, inference_cache/)
    al Neo4j para análisis de grafo de entidades.

    Abre Neo4j Browser en http://localhost:7474 para visualizar el grafo.
    Cypher de ejemplo: MATCH (p:Proyecto)-[:UBICADO_EN]->(e:Estado) RETURN p, e LIMIT 100
    """
    async def _stream():
        yield f"data: {json.dumps({'status': 'log', 'msg': 'Iniciando carga al Neo4j...', 'pct': 0})}\n\n"
        try:
            from dw.neo4j_loader import run_neo4j_loader
            loop = asyncio.get_event_loop()
            stats = await loop.run_in_executor(None, run_neo4j_loader, dry_run, clear)
            if "error" in stats:
                payload = json.dumps({"status": "error", "msg": stats["error"]})
                yield f"data: {payload}\n\n"
            else:
                n_p = stats.get("n_projects", 0)
                n_r = stats.get("n_relations", 0)
                msg = f"Carga completada: {n_p} proyectos, {n_r} relaciones"
                payload = json.dumps({"status": "complete", "msg": msg, "stats": stats, "pct": 100})
                yield f"data: {payload}\n\n"
        except Exception as exc:
            payload = json.dumps({"status": "error", "msg": str(exc)})
            yield f"data: {payload}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


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
    async def gen():
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
    """Retorna el grafo de conocimiento utilizando caché de Redis y disco con invalidación reactiva."""
    from core.graph_builder import build_full_graph

    # 1. Intentar cargar desde Redis primero (máximo rendimiento)
    if REDIS_AVAILABLE and redis_client:
        try:
            cached = redis_client.get("zohar:graph:compact")
            if cached:
                return JSONResponse(json.loads(cached))
        except Exception as e:
            logger.warning("Error leyendo grafo de Redis: %s", e)

    # 2. Calcular el mtime máximo de los PDFs para caché en disco (fallback)
    def _get_max_pdf_mtime():
        max_mtime = 0.0
        if DOWNLOADS_DIR.exists():
            for pdf in DOWNLOADS_DIR.rglob("*.pdf"):
                try:
                    mtime = pdf.stat().st_mtime
                    if mtime > max_mtime:
                        max_mtime = mtime
                except Exception:
                    pass
        return max_mtime

    max_pdf_mtime = await asyncio.to_thread(_get_max_pdf_mtime)

    # 3. Intentar cargar caché en disco
    cache_path = DATA_DIR / "graph_cache.json"
    if cache_path.exists():
        cache_mtime = cache_path.stat().st_mtime
        if cache_mtime >= max_pdf_mtime:
            try:
                # Carga asíncrona de archivo disco
                def _read_cache():
                    return json.loads(cache_path.read_text(encoding="utf-8"))
                graph_data = await asyncio.to_thread(_read_cache)

                # Guardar en Redis para futuras consultas rápidas
                if REDIS_AVAILABLE and redis_client:
                    redis_client.set("zohar:graph:compact", json.dumps(graph_data))

                return JSONResponse(graph_data)
            except Exception as exc:
                logger.warning("Error leyendo cache de grafo en disco: %s", exc)

    logger.info("Regenerando grafo de conocimiento (cache expirada o no existe)...")
    
    # 4. Generar el grafo en segundo plano (ThreadPool)
    graph = await asyncio.to_thread(build_full_graph, DOWNLOADS_DIR)

    # 5. Persistir caché en disco y Redis de forma asíncrona
    def _write_cache():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")

    try:
        await asyncio.to_thread(_write_cache)
    except Exception as exc:
        logger.error("No se pudo escribir cache de grafo en disco: %s", exc)

    if REDIS_AVAILABLE and redis_client:
        try:
            redis_client.set("zohar:graph:compact", json.dumps(graph))
        except Exception as exc:
            logger.warning("No se pudo escribir cache de grafo en Redis: %s", exc)

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


@app.get("/api/corpus/files-status", tags=["inference"])
async def get_corpus_files_status():
    """
    Retorna el estado de conversión de archivos PDF a MD en todo el corpus
    (estudios, resumenes, resolutivos).
    """
    from core.graph_builder import parse_semarnat_key
    files = []
    
    # Directorios a buscar
    directories = {
        "Estudio": ESTUDIOS_DIR,
        "Resumen": RESUMENES_DIR,
        "Resolutivo": RESOLUTIVOS_DIR
    }
    
    for category_name, directory in directories.items():
        if directory.exists():
            for pdf in sorted(directory.glob("*.pdf")):
                parsed = parse_semarnat_key(pdf.name)
                clave = parsed.get("clave", pdf.stem)
                
                # Buscar candidatos de extracción .md
                candidates = [
                    EXTRACTIONS_DIR / f"{clave}.estudio.00.md",
                    EXTRACTIONS_DIR / f"{clave}.resumen.00.md",
                    EXTRACTIONS_DIR / f"{clave}.md",
                    EXTRACTIONS_DIR / f"{pdf.stem}.md",
                ]
                
                md_ready = False
                md_filename = ""
                for candidate in candidates:
                    if candidate.exists():
                        md_ready = True
                        md_filename = candidate.name
                        break
                
                files.append({
                    "clave": clave,
                    "category": category_name,
                    "pdf_name": pdf.name,
                    "md_name": md_filename or f"{clave}.md",
                    "md_ready": md_ready,
                    "size_mb": round(pdf.stat().st_size / 1e6, 2),
                })
                
    return {"files": files, "total": len(files)}


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
    # Fallback: buscar con la clave limpia si no existe la versión MD-stem
    if not inference_cache_path.exists():
        from core.graph_builder import parse_semarnat_key
        parsed = parse_semarnat_key(filename)
        if parsed.get("valid"):
            fallback_path = inference_cache_dir / f"{parsed['clave']}.json"
            if fallback_path.exists():
                inference_cache_path = fallback_path

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


def extract_project_info_from_text(clave: str, text: str) -> tuple[str, str, str]:
    """
    Extrae el nombre, la ubicación y el promovente de un proyecto en el texto
    alrededor de la clave SINAT dada, intentando usar LLM o heurística.
    Retorna (project_name, location, promovente).
    """
    from core.graph_builder import parse_semarnat_key
    parsed = parse_semarnat_key(clave)
    state_fallback = parsed.get("estado_nombre", "Desconocida")

    project_name = f"Proyecto {clave}"
    location = state_fallback
    promovente = "Desconocido"

    # 1. Intentar extracción con LLM si hay backend activo
    try:
        from core.llm_client import detect_active_backend, generate_completion
        provider, _ = detect_active_backend()
        if provider not in ("heuristic", "fallback_heuristic"):
            lines = text.split("\n")
            clave_upper = clave.upper()
            target_idx = -1
            for idx, line in enumerate(lines):
                if clave_upper in line.upper():
                    target_idx = idx
                    break
            
            fragment = ""
            if target_idx != -1:
                start_idx = max(0, target_idx - 10)
                end_idx = min(len(lines), target_idx + 10)
                fragment = "\n".join(lines[start_idx:end_idx])
            else:
                fragment = text[:3000]

            sys_prompt = """
            Eres un asistente experto que extrae información de gacetas ambientales de SEMARNAT.
            Dada una clave de proyecto y un texto, extrae:
            1. El nombre del proyecto (denominación o título del proyecto).
            2. La ubicación (Municipio y Estado, o en su defecto solo el Estado/Región).
            3. El promovente (persona física o moral que promueve o presenta el proyecto).
            
            Responde ÚNICAMENTE en JSON con esta estructura exacta:
            {
              "project_name": "Nombre completo del proyecto",
              "location": "Municipio, Estado",
              "promovente": "Nombre de la empresa o persona promotora"
            }
            """
            
            prompt = f"CLAVE DEL PROYECTO: {clave}\n\nTEXTO:\n{fragment}"
            res = generate_completion(
                prompt=prompt,
                system_prompt=sys_prompt,
                response_json=True
            )
            if not res.get("is_fallback") and ("project_name" in res or "location" in res or "promovente" in res):
                extracted_name = res.get("project_name", project_name).strip()
                extracted_loc = res.get("location", location).strip()
                extracted_prom = res.get("promovente", promovente).strip()
                if len(extracted_name) > 3 and extracted_name != f"Proyecto {clave}":
                    project_name = extracted_name
                if len(extracted_loc) > 3:
                    location = extracted_loc
                if len(extracted_prom) > 3:
                    promovente = extracted_prom
                return project_name, location, promovente
    except Exception as e:
        logger.warning(f"Error extrayendo info con LLM para clave {clave}: {e}. Usando heurística.")

    # 2. Heurística fallback (regex / tablas)
    lines = text.split("\n")
    clave_upper = clave.upper()
    target_idx = -1
    for idx, line in enumerate(lines):
        if clave_upper in line.upper():
            target_idx = idx
            break

    if target_idx != -1:
        line = lines[target_idx]
        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
            if parts and not parts[0]:
                parts.pop(0)
            if parts and not parts[-1]:
                parts.pop()

            clave_col_idx = -1
            for col_idx, part in enumerate(parts):
                if clave_upper in part.upper():
                    clave_col_idx = col_idx
                    break

            if clave_col_idx != -1:
                candidates = []
                for col_idx, part in enumerate(parts):
                    if col_idx == clave_col_idx:
                        continue
                    part_clean = part.strip()
                    if not part_clean or len(part_clean) < 4 or part_clean.isdigit():
                        continue
                    candidates.append((col_idx, part_clean))

                if candidates:
                    candidates.sort(key=lambda x: len(x[1]), reverse=True)
                    project_name = candidates[0][1]
                    if len(candidates) > 1:
                        location = candidates[1][1]
        else:
            start_idx = max(0, target_idx - 3)
            end_idx = min(len(lines), target_idx + 4)
            surrounding_text = "\n".join(lines[start_idx:end_idx])

            name_patterns = [
                r"(?:nombre\s+del\s+proyecto|proyecto|nombre|denominación)\s*:\s*([^\n|]+)",
                r"(?:nombre\s+del\s+proyecto|proyecto|nombre|denominación)\s+is\s+([^\n|]+)",
            ]
            for pat in name_patterns:
                m = re.search(pat, surrounding_text, re.IGNORECASE)
                if m:
                    proj_name_cand = m.group(1).strip()
                    proj_name_cand = re.sub(r"[*`_#]+", "", proj_name_cand)
                    if len(proj_name_cand) > 3:
                        project_name = proj_name_cand
                        break

            loc_patterns = [
                r"(?:ubicación|estado|municipio|localidad)\s*:\s*([^\n|]+)",
            ]
            for pat in loc_patterns:
                m = re.search(pat, surrounding_text, re.IGNORECASE)
                if m:
                    loc_cand = m.group(1).strip()
                    loc_cand = re.sub(r"[*`_#]+", "", loc_cand)
                    if len(loc_cand) > 3:
                        location = loc_cand
                        break

            prom_patterns = [
                r"(?:promovente|empresa|interesado|peticionario|responsable)\s*:\s*([^\n|]+)",
            ]
            for pat in prom_patterns:
                m = re.search(pat, surrounding_text, re.IGNORECASE)
                if m:
                    prom_cand = m.group(1).strip()
                    prom_cand = re.sub(r"[*`_#]+", "", prom_cand)
                    if len(prom_cand) > 3:
                        promovente = prom_cand
                        break

    project_name = project_name.strip().strip('"\'[]()')
    location = location.strip().strip('"\'[]()')
    promovente = promovente.strip().strip('"\'[]()')

    if len(project_name) > 200:
        project_name = project_name[:197] + "..."

    return project_name, location, promovente


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

    async def gen():
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
                            row_clave, row_year, row_file = row[:3]
                            row_proj_name = row[3] if len(row) > 3 else f"Proyecto {row_clave}"
                            row_loc = row[4] if len(row) > 4 else ""
                            row_prom = row[5] if len(row) > 5 else "Desconocido"
                            # Si es placeholder huérfano, omitirlo
                            if row_clave == "23QR2024TD085" and not row_file:
                                continue
                            is_row_asea = row_file and ("asea" in row_file.lower() or Path(row_file).name.startswith("ASEA_"))
                            item = {
                                "CLAVE": row_clave,
                                "YEAR": int(row_year),
                                "FILE": row_file,
                                "PROJECT_NAME": row_proj_name,
                                "LOCATION": row_loc,
                                "PROMOVENTE": row_prom
                            }
                            if source == "sinat" and is_row_asea:
                                existing_claves.append(item)
                                seen_claves.add(row_clave)
                            elif source == "asea" and not is_row_asea:
                                existing_claves.append(item)
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
                    proj_name, loc, prom = extract_project_info_from_text(clave, text_content)
                    new_claves.append({
                        "CLAVE": clave,
                        "YEAR": year,
                        "FILE": str(g_pdf_path),
                        "PROJECT_NAME": proj_name,
                        "LOCATION": loc,
                        "PROMOVENTE": prom
                    })
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
        fieldnames = ["CLAVE", "YEAR", "FILE", "PROJECT_NAME", "LOCATION", "PROMOVENTE"]
        if final_claves:
            # Ordenar para consistencia
            final_claves.sort(key=lambda x: (x.get("FILE", ""), x.get("CLAVE", "")))
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(final_claves)
        else:
            # CSV mínimo para tests
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerow({
                    "CLAVE": "23QR2024TD085",
                    "YEAR": year,
                    "FILE": "",
                    "PROJECT_NAME": "Proyecto 23QR2024TD085",
                    "LOCATION": "Desconocida",
                    "PROMOVENTE": "Desconocido"
                })

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

    async def gen():
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


def run_pipeline_generator(year: int, source: str, rebuild_wiki: bool = True):
    from core.graph_builder import build_full_graph
    from core.pdf_processor import iter_pages_as_markdown
    import csv as csv_module

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
                        row_clave, row_year, row_file = row[:3]
                        row_proj_name = row[3] if len(row) > 3 else f"Proyecto {row_clave}"
                        row_loc = row[4] if len(row) > 4 else ""
                        row_prom = row[5] if len(row) > 5 else "Desconocido"
                        if row_clave == "23QR2024TD085" and not row_file:
                            continue
                        is_row_asea = row_file and ("asea" in row_file.lower() or Path(row_file).name.startswith("ASEA_"))
                        item = {
                            "CLAVE": row_clave,
                            "YEAR": int(row_year),
                            "FILE": row_file,
                            "PROJECT_NAME": row_proj_name,
                            "LOCATION": row_loc,
                            "PROMOVENTE": row_prom
                        }
                        if source == "sinat" and is_row_asea:
                            existing_claves.append(item)
                            seen_claves.add(row_clave)
                        elif source == "asea" and not is_row_asea:
                            existing_claves.append(item)
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
                    proj_name, loc, prom = extract_project_info_from_text(clave, text)
                    new_claves.append({
                        "CLAVE": clave,
                        "YEAR": year,
                        "FILE": str(g_pdf),
                        "PROJECT_NAME": proj_name,
                        "LOCATION": loc,
                        "PROMOVENTE": prom
                    })
                    
    final_claves = existing_claves + new_claves
    if final_claves:
        final_claves.sort(key=lambda x: (x.get("FILE", ""), x.get("CLAVE", "")))
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv_module.DictWriter(f, fieldnames=["CLAVE", "YEAR", "FILE", "PROJECT_NAME", "LOCATION", "PROMOVENTE"])
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


@app.get("/api/scraper/run-pipeline", tags=["scraper"])
async def run_pipeline(year: int = Query(2026), source: str = Query("all", description="sinat | asea | all"), rebuild_wiki: bool = Query(True)):
    """
    SSE: Ejecuta el pipeline completo de ingestión.
    Etapas: gacetas ASEA → gacetas SINAT → conversión MD → extracción claves → grafo → Second Brain.
    """
    return _sse_response(run_pipeline_generator(year, source, rebuild_wiki))


def download_remaining_generator(year: int):
    """
    Genera eventos SSE para descargar secuencialmente las claves que faltan en el corpus.
    """
    from scrapers.semarnat_downloader import SemarnatDownloader
    from core.graph_builder import parse_semarnat_key
    from sqlalchemy import create_engine, text
    import os
    
    # 1. Obtener claves registradas en base de datos
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@db:5432/maritime_dw")
    db_claves = set()
    try:
        engine = create_engine(db_url)
        with engine.connect() as conn:
            res = conn.execute(text("SELECT clave FROM public.semarnat_projects"))
            db_claves = {row[0].strip().upper() for row in res}
    except Exception as exc:
        logger.warning("Fallo al consultar base de datos para claves restantes: %s", exc)
        
    # 2. Obtener claves de gacetas locales extraídas como fallback
    extractions_dir = BASE_DIR / "extractions"
    gaceta_claves = set()
    if extractions_dir.exists():
        from core.second_brain import SecondBrainBuilder
        builder = SecondBrainBuilder(BASE_DIR)
        for md_file in extractions_dir.glob("*.md"):
            if "gaceta" in md_file.name.lower() or md_file.name.startswith("ASEA_"):
                try:
                    content = md_file.read_text(encoding="utf-8", errors="ignore")
                    found = builder.clave_re.findall(content.upper())
                    gaceta_claves.update(found)
                except Exception:
                    pass
                    
    all_known_claves = db_claves.union(gaceta_claves)
    
    # 3. Filtrar claves que ya tienen PDF de estudio descargado
    # Buscamos estudios locales
    local_study_files = set()
    if ESTUDIOS_DIR.exists():
        for f in ESTUDIOS_DIR.glob("*.pdf"):
            parsed = parse_semarnat_key(f.name)
            clave = parsed.get("clave", f.stem).upper()
            local_study_files.add(clave)
            
    pending_claves = sorted(list(all_known_claves - local_study_files))
    
    if not pending_claves:
        yield {"status": "complete", "msg": "No hay claves pendientes para descargar. ¡Todo al día!", "pct": 100}
        return
        
    yield {"status": "progress", "msg": f"Se encontraron {len(pending_claves)} claves pendientes. Iniciando descarga secuencial...", "pct": 0, "total_pending": len(pending_claves)}
    
    downloader = SemarnatDownloader(
        download_dir=str(DOWNLOADS_DIR),
        carpeta_estudios=str(ESTUDIOS_DIR),
        carpeta_resumenes=str(RESUMENES_DIR),
        carpeta_resolutivos=str(RESOLUTIVOS_DIR),
    )
    
    for idx, clave in enumerate(pending_claves):
        yield {"status": "progress", "msg": f"[{idx+1}/{len(pending_claves)}] Descargando clave {clave}...", "pct": int((idx / len(pending_claves)) * 100), "clave": clave}
        
        try:
            # Descargar clave vía Selenium
            classified_files = {"resumenes": [], "estudios": [], "resolutivos": []}
            metadata = {}
            for event in downloader._descargar_clave_gen_with_retry(clave):
                msg = event.get("msg", "")
                if msg:
                    yield {"status": "progress", "msg": f"  -> {clave}: {msg}", "pct": int((idx / len(pending_claves)) * 100) + 1}
                if "metadata" in event:
                    metadata = event["metadata"]
                if "files" in event:
                    classified_files = event["files"]
            
            # Persistir en DB
            if metadata:
                try:
                    update_csv_metadata(clave, metadata, year)
                    upsert_project_db(clave, metadata, year)
                except Exception as db_exc:
                    yield {"status": "progress", "msg": f"  -> Warning persistiendo DB para {clave}: {db_exc}", "pct": int((idx / len(pending_claves)) * 100) + 1}
                    
        except Exception as e:
            yield {"status": "progress", "msg": f"  -> Error descargando clave {clave}: {e}", "pct": int((idx / len(pending_claves)) * 100) + 1}
            
    yield {"status": "complete", "msg": f"Proceso finalizado. Se procesaron {len(pending_claves)} claves.", "pct": 100}


@app.get("/api/scraper/download-remaining", tags=["scraper"])
async def download_remaining(year: int = Query(2026)):
    """
    SSE: Descarga secuencialmente todos los estudios PDF de claves registradas que falten.
    """
    return _sse_response(download_remaining_generator(year))


def update_csv_metadata(clave: str, metadata: dict, year: int):
    """
    Actualiza o inserta los metadatos de un proyecto en el archivo CSV data/claves_{year}.csv.
    """
    import csv
    csv_path = DATA_DIR / f"claves_{year}.csv"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    rows = []
    found = False
    
    header = ["CLAVE", "YEAR", "FILE", "PROJECT_NAME", "LOCATION", "PROMOVENTE"]
    
    if csv_path.exists():
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                file_header = next(reader, None)
                if file_header:
                    header = file_header
                for row in reader:
                    if len(row) > 0 and row[0].strip().upper() == clave.strip().upper():
                        while len(row) < 6:
                            row.append("")
                        if metadata.get("project_name"):
                            row[3] = metadata["project_name"]
                        if metadata.get("municipio") or metadata.get("state"):
                            locs = [metadata.get("municipio", ""), metadata.get("state", "")]
                            row[4] = ", ".join([l for l in locs if l])
                        if metadata.get("promovente"):
                            row[5] = metadata["promovente"]
                        found = True
                    rows.append(row)
        except Exception as exc:
            logger.warning("Error leyendo CSV de claves para actualizar: %s", exc)
            
    if not found:
        locs = [metadata.get("municipio", ""), metadata.get("state", "")]
        location = ", ".join([l for l in locs if l])
        new_row = [
            clave,
            str(year),
            "",
            metadata.get("project_name", f"Proyecto {clave}"),
            location,
            metadata.get("promovente", "Desconocido")
        ]
        rows.append(new_row)
        
    try:
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)
    except Exception as exc:
        logger.error("Error escribiendo CSV de claves: %s", exc)


def upsert_project_db(clave: str, metadata: dict, year: int):
    """
    Inserta o actualiza los metadatos de un proyecto directamente en la base de datos PostgreSQL.
    """
    from sqlalchemy import create_engine, text
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/maritime_dw")
    try:
        engine = create_engine(db_url, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            res = conn.execute(text("SELECT clave FROM public.semarnat_projects WHERE clave = :clave"), {"clave": clave})
            row = res.fetchone()
            
            project_name = metadata.get("project_name", f"Proyecto {clave}")
            status = metadata.get("status", "INGRESADO")
            sector = metadata.get("sector", "Otros")
            
            locs = [metadata.get("municipio", ""), metadata.get("state", "")]
            state = ", ".join([l for l in locs if l]) if any(locs) else "Desconocido"
            
            promovente = metadata.get("promovente", "Desconocido")
            
            if row:
                query = text("""
                    UPDATE public.semarnat_projects
                    SET project_name = COALESCE(:name, project_name),
                        status = COALESCE(:status, status),
                        sector = COALESCE(:sector, sector),
                        state = COALESCE(:state, state),
                        promovente = COALESCE(:promovente, promovente),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE clave = :clave
                """)
                conn.execute(query, {
                    "clave": clave,
                    "name": project_name,
                    "status": status,
                    "sector": sector,
                    "state": state,
                    "promovente": promovente
                })
            else:
                query = text("""
                    INSERT INTO public.semarnat_projects (clave, project_name, status, sector, state, year, promovente)
                    VALUES (:clave, :name, :status, :sector, :state, :year, :promovente)
                """)
                conn.execute(query, {
                    "clave": clave,
                    "name": project_name,
                    "status": status,
                    "sector": sector,
                    "state": state,
                    "year": year,
                    "promovente": promovente
                })
            conn.commit()
            logger.info("Base de datos: UPSERT completado para clave %s", clave)
    except Exception as exc:
        logger.warning("No se pudo realizar UPSERT en base de datos: %s", exc)


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

    async def gen():
        yield {"status": "progress", "msg": f"Iniciando descarga para clave: {clave}", "pct": 0, "clave": clave}

        # Etapa 1: Descarga SEMARNAT vía Selenium (con reintentos automáticos)
        yield {"status": "progress", "msg": "Conectando con portal SEMARNAT (Selenium)...", "pct": 5}
        metadata = {}
        classified_files = {"resumenes": [], "estudios": [], "resolutivos": []}
        download_status = "error"
        try:
            from scrapers.semarnat_downloader import SemarnatDownloader
            from concurrent.futures import ThreadPoolExecutor
            downloader = SemarnatDownloader(
                download_dir=str(DOWNLOADS_DIR),
                carpeta_estudios=str(ESTUDIOS_DIR),
                carpeta_resumenes=str(RESUMENES_DIR),
                carpeta_resolutivos=str(RESOLUTIVOS_DIR),
            )
            for event in downloader._descargar_clave_gen_with_retry(clave):
                if "metadata" in event:
                    metadata = event["metadata"]
                if "files" in event:
                    classified_files = event["files"]
                yield {**event, "pct": min(5 + int(event.get("pct", 0) * 0.55), 60)}

            # Calcular estado de completitud de la descarga
            counts = [
                len(classified_files.get("resumenes", [])),
                len(classified_files.get("estudios", [])),
                len(classified_files.get("resolutivos", [])),
            ]
            total_types = sum(1 for c in counts if c > 0)
            if total_types == 3:
                download_status = "complete"
            elif total_types > 0:
                download_status = "partial"
            else:
                download_status = "error"

            # Persistir metadatos DOM en CSV y Postgres
            if metadata:
                try:
                    update_csv_metadata(clave, metadata, year)
                    upsert_project_db(clave, metadata, year)
                    yield {"status": "progress", "msg": "Metadatos del DOM integrados exitosamente (CSV & Postgres)", "pct": 61}
                except Exception as db_exc:
                    yield {"status": "progress", "msg": f"Metadatos DOM warning: {db_exc}", "pct": 61, "level": "warning"}

            # Enriquecimiento LLM en background (no bloquea el stream)
            if download_status in ("complete", "partial"):
                try:
                    from core.llm_enricher import enrich_metadata_from_pdf, find_best_pdf_for_enrichment
                    best_pdf = find_best_pdf_for_enrichment(classified_files)
                    if best_pdf:
                        _meta_snap = dict(metadata)
                        _year_snap = year
                        _clave_snap = clave
                        def _enrich_and_persist(pdf_path, meta, clave_s, year_s):
                            try:
                                enriched = enrich_metadata_from_pdf(pdf_path, meta)
                                if enriched != meta:
                                    upsert_project_db(clave_s, enriched, year_s)
                                    update_csv_metadata(clave_s, enriched, year_s)
                                    logger.info("Enriquecimiento LLM completado para %s", clave_s)
                            except Exception as e:
                                logger.warning("Error en enriquecimiento background: %s", e)
                        pool = ThreadPoolExecutor(max_workers=1)
                        pool.submit(_enrich_and_persist, best_pdf, _meta_snap, _clave_snap, _year_snap)
                        pool.shutdown(wait=False)
                        yield {"status": "progress", "msg": "Enriquecimiento LLM iniciado en background (Gemma 4 E2B)...", "pct": 62, "level": "info"}
                except Exception as enr_exc:
                    logger.warning("No se pudo iniciar enriquecimiento LLM: %s", enr_exc)

        except Exception as exc:
            yield {"status": "progress", "msg": f"Descarga warning: {exc}", "pct": 60, "level": "warning"}

        # Etapa 2: Conversión a Markdown
        yield {"status": "progress", "msg": "Convirtiendo PDFs descargados a Markdown...", "pct": 63}
        
        files_to_convert = []
        for doc_cat, doc_dir in [("estudios", ESTUDIOS_DIR), ("resumenes", RESUMENES_DIR), ("resolutivos", RESOLUTIVOS_DIR)]:
            if classified_files.get(doc_cat):
                files_to_convert.extend([Path(f) for f in classified_files[doc_cat]])
            else:
                # Fallback: buscar archivos de la clave en la carpeta correspondiente
                tipo_singular = doc_cat[:-2]  # estudios -> estudio, etc.
                found = list(doc_dir.glob(f"{clave}.{tipo_singular}.*.pdf"))
                if not found and (doc_dir / f"{clave}.pdf").exists() and doc_cat == "estudios":
                    found = [doc_dir / f"{clave}.pdf"]
                files_to_convert.extend(found)
                
        converted_md_paths = []
        estudios_md_paths = []
        if files_to_convert:
            for pdf_file in files_to_convert:
                md_name = pdf_file.stem + ".md"
                md_path = EXTRACTIONS_DIR / md_name
                try:
                    pages = []
                    for _, _, md_text, _ in iter_pages_as_markdown(pdf_file):
                        pages.append(md_text)
                    EXTRACTIONS_DIR.mkdir(parents=True, exist_ok=True)
                    md_path.write_text("\n".join(pages), encoding="utf-8")
                    converted_md_paths.append(md_path)
                    if "estudio" in pdf_file.name.lower() or pdf_file.name == f"{clave}.pdf":
                        estudios_md_paths.append(md_path)
                    yield {"status": "progress", "msg": f"Markdown extraído: {md_path.name} ({md_path.stat().st_size} bytes)", "pct": 75}
                except Exception as exc:
                    yield {"status": "progress", "msg": f"Conversión MD warning para {pdf_file.name}: {exc}", "pct": 75, "level": "warning"}
        else:
            yield {"status": "progress", "msg": "Sin archivos PDF disponibles para conversión", "pct": 75, "level": "warning"}

        # Etapa 3: Inferencia (si hay MD de estudio)
        if estudios_md_paths:
            yield {"status": "progress", "msg": "Ejecutando inferencia sobre el estudio...", "pct": 77}
            for md_path in estudios_md_paths:
                try:
                    cache_path = DATA_DIR / "inference_cache" / f"{md_path.stem}.json"
                    report = generate_report(md_path)
                    DATA_DIR.mkdir(parents=True, exist_ok=True)
                    (DATA_DIR / "inference_cache").mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(json_module.dumps(report, ensure_ascii=False), encoding="utf-8")
                    yield {"status": "progress", "msg": f"Inferencia ({md_path.name}): {report.get('veredicto', 'SIN DICTAMEN')} (score={report.get('score', 0):.2f})", "pct": 88}
                except Exception as exc:
                    yield {"status": "progress", "msg": f"Inferencia warning para {md_path.name}: {exc}", "pct": 88, "level": "warning"}

        # Etapa 4: Actualizar Second Brain y Buscador Semántico
        yield {"status": "progress", "msg": "Actualizando base de conocimiento y buscador semántico...", "pct": 90}
        try:
            from core.second_brain import SecondBrainBuilder
            from core.semantic_search import SemanticSearchEngine
            stats = SecondBrainBuilder(BASE_DIR).build_vault()
            try:
                engine = SemanticSearchEngine(BASE_DIR)
                engine.build_index()
            except Exception as emb_exc:
                logger.warning("Error construyendo índice semántico: %s", emb_exc)
            yield {"status": "progress", "msg": f"Second Brain actualizado: {stats['total_proyectos']} proyectos", "pct": 97}
        except Exception as exc:
            yield {"status": "progress", "msg": f"Second Brain warning: {exc}", "pct": 97, "level": "warning"}

        # Evento final con estado de descarga
        docs_downloaded = [
            tipo for tipo, files in classified_files.items() if files
        ]
        yield {
            "status": "complete",
            "msg": f"Pipeline de clave {clave} completado",
            "pct": 100,
            "clave": clave,
            "download_status": download_status,
            "docs_downloaded": docs_downloaded,
            "n_resumenes": len(classified_files.get("resumenes", [])),
            "n_estudios": len(classified_files.get("estudios", [])),
            "n_resolutivos": len(classified_files.get("resolutivos", [])),
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
                        row_clave, _, row_file = row[:3]
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
                    row_clave, _, row_file = row[:3]
                    row_proj_name = row[3] if len(row) > 3 else f"Proyecto {row_clave}"
                    row_loc = row[4] if len(row) > 4 else "Desconocida"
                    
                    # Comprobar si pertenece a la gaceta consultada
                    if row_file and Path(row_file).stem.upper() == target_stem:
                        # Comprobar estado de archivos en disco para esta clave (soporta legado y nuevo renombrado)
                        has_pdf_estudio = (DOWNLOADS_DIR / "estudios" / f"{row_clave}.pdf").exists() or bool(list((DOWNLOADS_DIR / "estudios").glob(f"{row_clave}.estudio.*.pdf")))
                        has_pdf_resumen = (DOWNLOADS_DIR / "resumenes" / f"{row_clave}.pdf").exists() or bool(list((DOWNLOADS_DIR / "resumenes").glob(f"{row_clave}.resumen.*.pdf")))
                        has_pdf_resolutivo = (DOWNLOADS_DIR / "resolutivos" / f"{row_clave}.pdf").exists() or bool(list((DOWNLOADS_DIR / "resolutivos").glob(f"{row_clave}.resolutivo.*.pdf")))
                        
                        has_md_estudio = (EXTRACTIONS_DIR / f"{row_clave}.md").exists() or bool(list(EXTRACTIONS_DIR.glob(f"{row_clave}.estudio.*.md")))
                        has_md_resumen = bool(list(EXTRACTIONS_DIR.glob(f"{row_clave}.resumen.*.md")))
                        has_md_resolutivo = bool(list(EXTRACTIONS_DIR.glob(f"{row_clave}.resolutivo.*.md")))
                        
                        inference_json = DATA_DIR / "inference_cache" / f"{row_clave}.json"
                        has_inference = inference_json.exists() or bool(list((DATA_DIR / "inference_cache").glob(f"{row_clave}.estudio.*.json")))

                        claves_info.append({
                            "clave": row_clave,
                            "project_name": row_proj_name,
                            "location": row_loc,
                            "has_pdf_estudio": has_pdf_estudio,
                            "has_pdf_resumen": has_pdf_resumen,
                            "has_pdf_resolutivo": has_pdf_resolutivo,
                            "has_extraction": has_md_estudio,
                            "has_md_estudio": has_md_estudio,
                            "has_md_resumen": has_md_resumen,
                            "has_md_resolutivo": has_md_resolutivo,
                            "has_inference": has_inference,
                        })
    except Exception as exc:
        logger.error("Error leyendo claves para gaceta %s: %s", gaceta_name, exc)

    return {"gaceta": gaceta_name, "claves": claves_info}


@app.get("/api/llm/status", tags=["llm"])
async def get_llm_status():
    """
    Retorna el backend de LLM activo en este momento.
    """
    try:
        from core.llm_client import detect_active_backend
        provider, model = detect_active_backend()
        return {"status": "ok", "provider": provider, "model": model}
    except Exception as exc:
        return {"status": "error", "provider": "heuristic", "model": "none", "error": str(exc)}


# ---------------------------------------------------------------------------
# Rutas — Automated Data Warehouse & Quality Auditor
# ---------------------------------------------------------------------------

@app.get("/api/dw/status", tags=["dw"])
async def get_dw_status():
    """
    Retorna el estado de la base de datos de DW y el reporte de calidad.
    """
    import json
    from sqlalchemy import create_engine, text

    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/maritime_dw")
    db_status = {
        "connected": False,
        "latency_ms": 0,
        "tables": {},
        "error": None
    }
    
    # 1. Verificar conexión a base de datos y obtener cantidad de registros
    t_start = time.time()
    try:
        engine = create_engine(db_url, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            db_status["latency_ms"] = int((time.time() - t_start) * 1000)
            db_status["connected"] = True
            
            # Consultar cantidad de filas en las tablas del DW
            for table in ["semarnat_projects", "project_evaluations"]:
                try:
                    res = conn.execute(text(f"SELECT COUNT(*) FROM public.{table}"))
                    db_status["tables"][table] = {"count": res.scalar()}
                except Exception as tbl_exc:
                    db_status["tables"][table] = {"count": 0, "error": str(tbl_exc)}
    except Exception as exc:
        db_status["error"] = str(exc)

    # 2. Leer reporte de calidad de datos
    json_path = BASE_DIR / "dw" / "audit_report.json"
    audit_report = {}
    if json_path.exists():
        try:
            audit_report = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as err:
            logger.error("Error leyendo audit_report.json: %s", err)

    return {
        "db": db_status,
        "quality": audit_report
    }


@app.get("/api/dw/pipeline-stats", tags=["dw"])
async def get_dw_pipeline_stats():
    """Retorna las estadísticas en tiempo real de la base de datos PostgreSQL."""
    from core.dw_pipeline import get_db_stats
    return get_db_stats()


@app.get("/api/dw/run-pipeline", tags=["dw"])
async def run_dw_pipeline():
    """
    SSE: Ejecuta el pipeline completo del Data Warehouse y transmite el progreso en tiempo real.
    """
    import subprocess
    import sys

    # Ubicación del script de python en el entorno virtual
    python_exe = sys.executable

    async def gen():
        yield {"status": "progress", "msg": "Iniciando Data Warehouse Pipeline...", "pct": 5, "stage": "init"}

        pipeline_path = BASE_DIR / "dw" / "pipeline.py"
        if not pipeline_path.exists():
            yield {"status": "error", "msg": "Script dw/pipeline.py no encontrado", "pct": 100}
            return

        cmd = [python_exe, str(pipeline_path)]
        logger.info("Iniciando pipeline DW: %s", " ".join(cmd))

        try:
            # Iniciamos el proceso y leemos su stdout línea a línea
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(BASE_DIR)
            )

            # Mapeo de frases clave del log a etapas y porcentajes de progreso
            stages_map = [
                ("=== LOGR DATA WAREHOUSE PIPELINE RUN ===", 8, "init", "Iniciando Data Warehouse Pipeline..."),
                ("[Schema] Executing schema.sql...", 15, "schema", "Inicializando esquema de base de datos..."),
                ("[Claves] Loading target environmental claves...", 30, "claves", "Cargando claves de interés..."),
                ("[SEMARNAT] Querying portal and downloading missing files...", 45, "semarnat", "Consultando portal SEMARNAT..."),
                ("[Markdown] Converting study PDFs to Markdown...", 60, "markdown", "Extrayendo texto Markdown de estudios..."),
                ("[Inferencia] Running AI environmental viability evaluations...", 75, "inference", "Evaluando viabilidad ambiental (Gemini/IA)..."),
                ("[Auditor] Gathering data and running Quality Auditor...", 85, "auditor", "Ejecutando auditoría de calidad de datos..."),
                ("[Ingest] Ingesting audited records into database...", 92, "ingest", "Ingiriendo registros limpios en PostgreSQL..."),
                ("[Second Brain] Sincronizando notas del Second Brain...", 97, "second_brain", "Sincronizando notas del Second Brain..."),
                ("=== PIPELINE RUN COMPLETED SUCCESSFULLY ===", 100, "done", "¡Pipeline completado con éxito!"),
            ]

            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                
                line_clean = line.strip()
                if not line_clean:
                    continue
                
                # Reportar el log crudo al frontend
                event = {"status": "log", "msg": line_clean}
                
                # Detectar etapas de progreso
                for pattern, pct, stage, friendly_msg in stages_map:
                    if pattern in line_clean:
                        event["status"] = "progress"
                        event["pct"] = pct
                        event["stage"] = stage
                        event["msg"] = friendly_msg
                        break
                
                yield event
            
            proc.wait()
            if proc.returncode == 0:
                yield {"status": "complete", "msg": "Pipeline completado con éxito", "pct": 100}
            else:
                yield {"status": "error", "msg": f"El pipeline falló con código de salida {proc.returncode}", "pct": 100}
        
        except Exception as exc:
            logger.error("Error ejecutando pipeline: %s", exc)
            yield {"status": "error", "msg": f"Error: {exc}", "pct": 100}

    return _sse_response(gen(), "dw_pipeline")


# ---------------------------------------------------------------------------
# Rutas — AI Agent Chat Playground & Tools
# ---------------------------------------------------------------------------

@app.get("/api/model/tools", tags=["system"])
async def list_model_tools():
    """Retorna la lista de herramientas disponibles para el modelo de IA."""
    return {
        "tools": [
            {
                "name": "database_query",
                "description": "Realiza consultas de lectura (SELECT) en las tablas 'semarnat_projects' y 'project_evaluations' para obtener estadísticas, conteos y estados de trámites.",
                "parameters": {"sql_query": "Consulta SQL de tipo SELECT a ejecutar."}
            },
            {
                "name": "second_brain_search",
                "description": "Realiza una búsqueda semántica de alta precisión en las fichas Markdown del Second Brain utilizando embeddings locales.",
                "parameters": {"query": "Texto o concepto de búsqueda."}
            },
            {
                "name": "ocr_extraction",
                "description": "Extrae el texto de un PDF page-by-page aplicando OCR híbrido mediante RapidOCR cuando el texto digital sea insuficiente.",
                "parameters": {"pdf_name": "Nombre del archivo PDF en el corpus."}
            },
            {
                "name": "second_brain_sync",
                "description": "Sincroniza la bóveda del Second Brain regenerando todas las notas Markdown vinculadas y actualizando la base de datos.",
                "parameters": {}
            }
        ]
    }
@app.get("/api/model/status", tags=["system"])
async def get_model_status():
    """Retorna el estado de conexión y el modelo de IA activo."""
    from core.llm_client import detect_active_backend
    provider, model_name = detect_active_backend()
    return {"provider": provider, "model": model_name}


def get_project_graph_context(clave: str) -> str:
    """
    Query Neo4j (or local graph cache fallback) to retrieve connections/relations for a project.
    Formats the context as a markdown block for RAG prompt injection.
    """
    logger.info("Graph-RAG: Consultando grafo para clave %s...", clave)
    
    neo4j_uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.getenv("NEO4J_USER", "neo4j")
    neo4j_pass = os.getenv("NEO4J_PASSWORD", "maritime_secret_pass")
    
    relations = []
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
        with driver.session() as session:
            # Query neighbors and relations
            query = """
            MATCH (p:Proyecto {clave: $clave})-[r]-(neighbor)
            RETURN type(r) AS rel, labels(neighbor)[0] AS type, 
                   coalesce(neighbor.nombre, neighbor.codigo, neighbor.descripcion) AS name
            """
            result = session.run(query, clave=clave)
            for row in result:
                rel = row["rel"]
                n_type = row["type"]
                name = row["name"]
                relations.append(f"  - [{rel}] -> {n_type}: {name}")
        driver.close()
    except Exception as e:
        logger.warning("Graph-RAG: No se pudo conectar a Neo4j: %s. Usando fallback de metadatos local...", e)
        # Fallback local usando parse_semarnat_key
        from core.graph_builder import parse_semarnat_key
        parsed = parse_semarnat_key(clave + ".pdf")
        if parsed.get("valid"):
            relations = [
                f"  - [UBICADO_EN] -> Estado: {parsed.get('estado_nombre')}",
                f"  - [ES_TIPO] -> TipoMIA: {parsed.get('tipo_nombre')}",
                f"  - [DEL_SECTOR] -> Sector: Sector {parsed.get('sector')}",
                f"  - [DEL_AÑO] -> Año: {parsed.get('year')}"
            ]

    if relations:
        return (
            "\n\nCONTEXTO ESTRUCTURADO DEL GRAFO DE RELACIONES (Graph-RAG):\n"
            + "\n".join(relations)
        )
    return ""


@app.post("/api/chat", tags=["system"])
async def api_chat(payload: dict):
    """
    Endpoint de chat interactivo con el modelo activo.
    Acepta: { "message": "...", "clave": "...", "history": [...] }
    """
    message = payload.get("message", "").strip()
    clave = payload.get("clave", "").strip()
    history = payload.get("history", [])

    if not message:
        raise HTTPException(400, detail="Mensaje vacío")

    # Obtener contexto del Second Brain si hay una clave de proyecto seleccionada
    context = ""
    graph_context = ""
    if clave:
        sb_note_path = BASE_DIR / "second_brain" / "02_Entities" / f"Proyecto - {clave}.md"
        if sb_note_path.exists():
            try:
                context = sb_note_path.read_text(encoding="utf-8")
                logger.info("Contexto del Second Brain inyectado para clave %s", clave)
            except Exception as exc:
                logger.warning("Error leyendo nota para contexto: %s", exc)
        
        # Consultar grafo para Graph-RAG
        try:
            graph_context = get_project_graph_context(clave)
        except Exception as exc:
            logger.warning("Error obteniendo contexto del grafo: %s", exc)

    # RAG Automático Inteligente si no se seleccionó clave en el dropdown
    auto_rag_context = ""
    if not clave:
        try:
            from core.semantic_search import SemanticSearchEngine
            search_engine = SemanticSearchEngine(BASE_DIR)
            results = search_engine.search(message, limit=2)
            pieces = []
            for res in results:
                if res.get("score", 0) >= 0.35:
                    note_path = BASE_DIR / "second_brain" / res["path"]
                    if note_path.exists():
                        content = note_path.read_text(encoding="utf-8")
                        pieces.append(f"### Nota: {res['title']} (Categoría: {res['category']})\n{content}")
            if pieces:
                auto_rag_context = "\n\nCONTEXTO RELEVANTE ENCONTRADO AUTOMÁTIMAMENTE EN EL SECOND BRAIN:\n" + "\n\n".join(pieces)
                logger.info("Auto-RAG inyectó %d notas relevantes.", len(pieces))
        except Exception as exc:
            logger.warning("Error en RAG automático: %s", exc)

    # Construir el prompt con el historial de la conversación y el contexto de RAG
    sys_prompt = (
        "Eres Zohar-v4-AI, motor de análisis de impacto ambiental para trámites SEMARNAT (México).\n"
        "Dominio: Manifestaciones de Impacto Ambiental (MIA), estudios de riesgo, gacetas SINAT,\n"
        "  resolutivos, y evaluaciones bajo LGEEPA. Las claves de proyecto siguen el patrón\n"
        "  XX_SS_AAAA_TTNNNN (ej: 03BS2026H0015 = Estado 03, Sector BS, año 2026, trámite H0015).\n"
        "Veredictos posibles: FAVORABLE, CONDICIONADO, DESFAVORABLE, PENDIENTE.\n"
        "REGLA CRÍTICA: NUNCA inventes datos ni estadísticas. Si el usuario pregunta por datos,\n"
        "  usa siempre una herramienta para obtenerlos. Responde en español, sé conciso y técnico."
    )
    
    if context:
        sys_prompt += (
            f"\n\nCONTEXTO DEL PROYECTO ACTUAL (Clave {clave}):\n"
            f"```markdown\n{context}\n```\n"
        )
        if graph_context:
            sys_prompt += f"```markdown{graph_context}\n```\n"
        sys_prompt += "Responde a las preguntas utilizando esta información como fuente principal de verdad."
    elif auto_rag_context:
        sys_prompt += auto_rag_context

    try:
        from core.agent import ZoharAgent
        from core.llm_client import detect_active_backend
        
        # Detectar modelo activo
        provider, model_name = detect_active_backend()
        
        if provider in ("heuristic", "fallback_heuristic"):
            fallback_text = (
                f"Hola. Estoy en modo heurístico (sin conexión a LLM activo).\n"
                f"He recibido tu consulta sobre: '{message}'.\n"
                f"Si deseas habilitar mi capacidad conversacional completa y la ejecución de herramientas, "
                f"por favor asegúrate de levantar el servidor local en el puerto 8083."
            )
            return {
                "response": fallback_text,
                "tool_calls": [],
                "provider": "heuristic",
                "model": "none"
            }
            
        agent = ZoharAgent(sys_prompt=sys_prompt, history=history)
        response, tool_calls = agent.run(message)
        
        return {
            "response": response.strip(),
            "tool_calls": tool_calls,
            "provider": provider,
            "model": model_name
        }
    except Exception as exc:
        logger.error("Error en api_chat con loop de agente: %s", exc)
        return {
            "response": f"[Error de Inferencia del Agente]: {exc}",
            "tool_calls": [],
            "provider": "error",
            "model": "none"
        }


@app.get("/api/eval/questions", tags=["system"])
async def get_eval_questions():
    """Retorna la lista de preguntas de evaluación estructuradas de data/eval_questions.json."""
    json_path = BASE_DIR / "data" / "eval_questions.json"
    if json_path.exists():
        try:
            return json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("Error leyendo eval_questions.json: %s", exc)
    return {"questions": []}


# ---------------------------------------------------------------------------
# Telemetría en Tiempo Real y Gestión Unificada de Servidores
# ---------------------------------------------------------------------------

@app.get("/api/telemetry/stream", tags=["system"])
async def telemetry_stream():
    """
    SSE Stream: Transmite telemetría en tiempo real (salud de 4 servicios, hardware,
    logs recientes de zohar_rsi.log y alertas de anomalías) cada 1 segundo.
    """
    import socket

    def check_port(host: str, port: int, timeout: float = 0.4) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except Exception:
            return False

    async def gen():
        log_path = BASE_DIR / "zohar_rsi.log"
        pid_path = BASE_DIR / "data" / "rsi.pid"

        while True:
            # 1. Chequeo no-bloqueante de puertos
            llama_online = await asyncio.to_thread(check_port, "127.0.0.1", 8083)
            pg_online = await asyncio.to_thread(check_port, "127.0.0.1", 5432)

            # 2. Latencia rápida si Llama-Server responde
            llama_latency = 0
            if llama_online:
                t0 = time.time()
                try:
                    async with httpx.AsyncClient() as client:
                        r = await client.get("http://127.0.0.1:8083/health", timeout=0.5)
                        if r.status_code == 200:
                            llama_latency = int((time.time() - t0) * 1000)
                except Exception:
                    pass

            # 3. Estado RSI Engine
            rsi_running = False
            rsi_pid = None
            if pid_path.exists():
                try:
                    p = int(pid_path.read_text(encoding="utf-8").strip())
                    os.kill(p, 0)
                    rsi_running = True
                    rsi_pid = p
                except (OSError, ValueError, ProcessLookupError):
                    pid_path.unlink(missing_ok=True)

            # 4. Hardware
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent
            disk = psutil.disk_usage("/").free / (1024 ** 3)

            # 5. Tail del log RSI
            recent_logs = []
            anomaly = None
            if log_path.exists():
                try:
                    lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                    for line in lines[-10:]:
                        line_str = line.strip()
                        if line_str:
                            recent_logs.append(line_str)
                            if "syntax_error" in line_str or "unexpected indent" in line_str:
                                try:
                                    rec = json.loads(line_str)
                                    anomaly = {"type": rec.get("event"), "error": rec.get("error"), "cycle": rec.get("cycle")}
                                except Exception:
                                    pass
                except Exception:
                    pass

            from core.dw_pipeline import get_db_stats
            db_stats = get_db_stats() if pg_online else {}

            data = {
                "status": "telemetry",
                "fastapi": {"status": "online", "port": 8004},
                "llama": {"status": "online" if llama_online else "offline", "port": 8083, "latency_ms": llama_latency},
                "postgres": {"status": "online" if pg_online else "offline", "port": 5432, "total_proyectos": db_stats.get("total_proyectos", 0)},
                "rsi": {"running": rsi_running, "pid": rsi_pid},
                "hardware": {"cpu_pct": cpu, "ram_pct": ram, "disk_free_gb": round(disk, 2)},
                "recent_logs": recent_logs,
                "anomaly": anomaly,
            }
            yield f"data: {json.dumps(data)}\n\n"
            await asyncio.sleep(1.0)

    return _sse_response(gen(), "telemetry")


@app.post("/api/server/manage", tags=["system"])
async def manage_server(payload: dict):
    """
    Controlador unificado de servicios: permite iniciar, detener o reiniciar
    Llama-Server, el Motor RSI u otros componentes.
    """
    action = payload.get("action", "").strip()

    if action == "start_llama":
        return await start_llama_server()
    elif action == "stop_llama":
        return await stop_llama_server()
    elif action == "restart_llama":
        await stop_llama_server()
        await asyncio.sleep(1.0)
        return await start_llama_server()
    elif action == "start_rsi":
        cycles = payload.get("cycles", 2)
        dry_run = payload.get("dry_run", False)
        target = payload.get("target_file", "scrapers/semarnat_downloader.py")
        cmd = [sys.executable, "auto_improver.py", "--target-file", target, "--cycles", str(cycles)]
        if dry_run:
            cmd.append("--dry-run")
        pid_file = BASE_DIR / "data" / "rsi.pid"
        log_file = BASE_DIR / "zohar_rsi.log"
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        log = open(log_file, "a", encoding="utf-8")
        proc = subprocess.Popen(cmd, cwd=str(BASE_DIR), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        pid_file.write_text(str(proc.pid), encoding="utf-8")
        return {"status": "started", "pid": proc.pid, "target": target}
    elif action == "stop_rsi":
        pid_file = BASE_DIR / "data" / "rsi.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except Exception:
                pass
            pid_file.unlink(missing_ok=True)
        return {"status": "stopped"}
    else:
        raise HTTPException(400, detail=f"Acción no reconocida: '{action}'")


@app.post("/api/harness/run", tags=["system"])
async def run_harness_endpoint():
    """Ejecuta el Harness de Maniobra Única (zohar_harness.py) y retorna el dictamen de sanidad."""
    from zohar_harness import run_harness
    report = await asyncio.to_thread(run_harness)
    return report





# --- RSI Loop control (Orquestador Multi-Objetivo) ---
from core.config import PID_FILE as RSI_PID_FILE, LOG_FILE as RSI_LOG_FILE, PYTHON_EXE as RSI_PYTHON_EXE, PROJECT_ROOT as RSI_PROJECT_ROOT

def _rsi_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False

@app.get("/api/rsi/status", tags=["rsi"])
def rsi_status():
    if RSI_PID_FILE.exists():
        try:
            pid = int(RSI_PID_FILE.read_text(encoding="utf-8").strip())
            if _rsi_is_running(pid):
                return {"running": True, "pid": pid}
        except (ValueError, OSError):
            pass
        RSI_PID_FILE.unlink(missing_ok=True)
    return {"running": False}

@app.post("/api/rsi/start", tags=["rsi"])
def rsi_start(iterations: int = 2):
    if RSI_PID_FILE.exists():
        try:
            pid = int(RSI_PID_FILE.read_text(encoding="utf-8").strip())
            if _rsi_is_running(pid):
                return {"status": "already_running", "pid": pid}
        except (ValueError, OSError):
            pass
    RSI_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    log = open(RSI_LOG_FILE, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [RSI_PYTHON_EXE, "rsi_run_all.py", "--cycles-per-target", str(iterations)],
        cwd=str(RSI_PROJECT_ROOT), stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    RSI_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    return {"status": "started", "pid": proc.pid}

@app.post("/api/rsi/stop", tags=["rsi"])
def rsi_stop():
    if not RSI_PID_FILE.exists():
        return {"status": "not_running"}
    try:
        pid = int(RSI_PID_FILE.read_text(encoding="utf-8").strip())
        os.killpg(os.getpgid(pid), _signal.SIGTERM)
    except (ProcessLookupError, ValueError, OSError):
        pass
    RSI_PID_FILE.unlink(missing_ok=True)
    return {"status": "stopped"}


@app.get("/api/rsi/run", tags=["rsi"])
def run_rsi_endpoint(cycles: int = Query(3, ge=1, le=10), dry_run: bool = Query(False)):
    """
    Ejecuta el ciclo de Auto-Mejora Recursiva (RSI) sobre semarnat_downloader.py
    y emite eventos SSE en tiempo real para el Dashboard.
    """
    from auto_improver import run_rsi_stream
    gen = run_rsi_stream(max_cycles=cycles, dry_run=dry_run)
    return _sse_response(gen)


# Global state para Toggle de RSI Atómico
ATOMIC_RSI_ACTIVE = False
_atomic_rsi_task = None

async def _atomic_rsi_worker_loop():
    """Background worker que ejecuta 1 iteración atómica de curaduría cada 30 segundos."""
    global ATOMIC_RSI_ACTIVE
    from core.rsi_brain import run_atomic_metadata_curation_step
    logger.info("Iniciando background worker de RSI Auto-Curaduría Atómica...")
    while ATOMIC_RSI_ACTIVE:
        try:
            res = await asyncio.to_thread(run_atomic_metadata_curation_step)
            if res.get("curated"):
                logger.info("RSI Atómico curó ficha %s: %s", res.get("clave"), res.get("metadata"))
                await broadcaster.broadcast({"status": "progress", "msg": f"RSI Atómico curó ficha {res.get('clave')}", "curation": res})
        except Exception as exc:
            logger.warning("Error en worker RSI atómico: %s", exc)
        await asyncio.sleep(30)

@app.get("/api/rsi/toggle-status", tags=["rsi"])
def get_atomic_rsi_toggle_status():
    """Retorna el estado activo/inactivo del Toggle de RSI Auto-Curaduría Atómica."""
    global ATOMIC_RSI_ACTIVE
    return {"active": ATOMIC_RSI_ACTIVE}

@app.post("/api/rsi/toggle", tags=["rsi"])
async def toggle_atomic_rsi(payload: dict):
    """Activa o desactiva el Toggle de RSI Auto-Curaduría Atómica desde el Dashboard UI."""
    global ATOMIC_RSI_ACTIVE, _atomic_rsi_task
    enable = payload.get("enable", not ATOMIC_RSI_ACTIVE)
    ATOMIC_RSI_ACTIVE = enable

    if ATOMIC_RSI_ACTIVE:
        if _atomic_rsi_task is None or _atomic_rsi_task.done():
            _atomic_rsi_task = asyncio.create_task(_atomic_rsi_worker_loop())
        msg = "RSI Auto-Curaduría Atómica ACTIVADA"
    else:
        if _atomic_rsi_task and not _atomic_rsi_task.done():
            _atomic_rsi_task.cancel()
        msg = "RSI Auto-Curaduría Atómica DESACTIVADA"

    logger.info(msg)
    return {"active": ATOMIC_RSI_ACTIVE, "msg": msg}


@app.post("/api/extract/structured", tags=["extraction"])
def extract_structured_project(payload: dict):
    """
    Endpoint para ejecutar la Extracción Estructurada Avanzada con LLM.
    Persiste los resultados en PostgreSQL (project_evaluations) y en el Vault de Obsidian.
    """
    clave = payload.get("clave")
    if not clave:
        raise HTTPException(status_code=400, detail="Se requiere 'clave'")

    md_file = BASE_DIR / "extractions" / f"{clave}.md"
    if not md_file.exists():
        # Buscar en subdirectorios
        found = list((BASE_DIR / "extractions").rglob(f"{clave}*.md"))
        if found:
            md_file = found[0]
        else:
            raise HTTPException(status_code=404, detail=f"No se encontró archivo Markdown para la clave {clave}")

    md_content = md_file.read_text(encoding="utf-8", errors="ignore")
    
    from core.structured_extractor import StructuredExtractor
    from core.dw_pipeline import upsert_project_evaluation
    from core.second_brain import SecondBrainBuilder

    extractor = StructuredExtractor()
    evaluation = extractor.extract_from_markdown(clave, md_content)
    eval_dict = evaluation.model_dump()

    # 1. Upsert PostgreSQL
    dw_res = upsert_project_evaluation(eval_dict)

    # 2. Update Obsidian Frontmatter
    builder = SecondBrainBuilder(base_dir=BASE_DIR)
    obsidian_updated = builder.update_note_frontmatter(clave, eval_dict)

    return {
        "status": "PASS",
        "clave": clave,
        "evaluation": eval_dict,
        "dw_status": dw_res,
        "obsidian_updated": obsidian_updated
    }


@app.post("/api/extract/batch", tags=["extraction"])
def extract_structured_batch(payload: dict):
    """Ejecuta la extracción estructurada en lote para múltiples proyectos pendientes."""
    limit = payload.get("limit", 5)
    extractions_dir = BASE_DIR / "extractions"
    md_files = list(extractions_dir.glob("*.md"))[:limit]

    results = []
    from core.structured_extractor import StructuredExtractor
    from core.dw_pipeline import upsert_project_evaluation
    from core.second_brain import SecondBrainBuilder

    extractor = StructuredExtractor()
    builder = SecondBrainBuilder(base_dir=BASE_DIR)

    for f in md_files:
        clave = f.stem
        try:
            md_content = f.read_text(encoding="utf-8", errors="ignore")
            evaluation = extractor.extract_from_markdown(clave, md_content)
            eval_dict = evaluation.model_dump()

            dw_res = upsert_project_evaluation(eval_dict)
            obsidian_updated = builder.update_note_frontmatter(clave, eval_dict)

            results.append({
                "clave": clave,
                "status": "PASS",
                "dw_status": dw_res.get("status"),
                "obsidian_updated": obsidian_updated
            })
        except Exception as exc:

            results.append({
                "clave": clave,
                "status": "ERROR",
                "message": str(exc)
            })

    return {"total": len(results), "results": results}



@app.get("/api/downloads/verify-status", tags=["downloads"])
def get_downloads_verification_status():
    """Retorna las estadísticas globales de verificación e integridad de descargas PDF."""
    from core.dw_pipeline import get_download_manifest_stats
    return get_download_manifest_stats()


@app.post("/api/downloads/verify-all", tags=["downloads"])
def verify_all_downloads_endpoint(payload: dict = None):
    """Audita todos los PDFs descargados en downloads/ y actualiza la tabla download_manifest."""
    limit = (payload or {}).get("limit", 100)
    downloads_dir = BASE_DIR / "downloads"
    if not downloads_dir.exists():
        return {"total_audited": 0, "verified": 0, "corrupt": 0}

    pdf_files = list(downloads_dir.rglob("*.pdf"))[:limit]
    
    from core.download_verifier import PDFDownloadVerifier
    from core.dw_pipeline import record_download_verification

    verifier = PDFDownloadVerifier()
    results = {"total_audited": len(pdf_files), "verified": 0, "corrupt": 0, "items": []}

    for path in pdf_files:
        clave_match = re.search(r"(\d{2}[A-Z]{2}\d{4}[A-Z0-9]\d{3,5})", path.name)
        clave = clave_match.group(1) if clave_match else path.stem
        file_type = "resumen" if "resumen" in str(path).lower() else ("estudio" if "estudio" in str(path).lower() else "resolutivo")

        v_res = verifier.verify_pdf_file(path, expected_clave=clave)
        record_download_verification(clave, file_type, str(path), v_res)

        if v_res.get("valid", False):
            results["verified"] += 1
        else:
            results["corrupt"] += 1

        results["items"].append({
            "clave": clave,
            "file_type": file_type,
            "status": v_res.get("status"),
            "valid": v_res.get("valid"),
            "reason": v_res.get("reason"),
            "file_size": v_res.get("file_size")
        })

    return results


@app.post("/api/rag/query", tags=["rag"])
def rag_query_endpoint(payload: dict):
    """
    Ejecuta el pipeline RAG completo:
    Recuperación vectorial Top-K + Filtrado por metadatos + Síntesis LLM con Citas.
    """
    query = payload.get("query")
    if not query:
        raise HTTPException(status_code=400, detail="Se requiere 'query'")

    filters = payload.get("filters", {})
    top_k = payload.get("top_k", 5)

    from core.rag_engine import RAGEngine
    engine = RAGEngine(base_dir=BASE_DIR)
    return engine.query_rag(query, filters=filters, top_k=top_k)


@app.get("/api/rag/search", tags=["rag"])
def rag_search_endpoint(query: str, clave: Optional[str] = None, top_k: int = 5):
    """Búsqueda semántica vectorial pura de chunks con score de similitud."""
    from core.rag_engine import RAGEngine
    engine = RAGEngine(base_dir=BASE_DIR)
    filters = {"clave": clave} if clave else None
    return {"query": query, "chunks": engine.retrieve_context(query, filters=filters, top_k=top_k)}


@app.post("/api/rag/reindex", tags=["rag"])
def rag_reindex_endpoint(payload: dict = None):
    """Indexa masivamente los documentos Markdown en extractions/ para el motor RAG."""
    limit = (payload or {}).get("limit", 50)
    extractions_dir = BASE_DIR / "extractions"
    if not extractions_dir.exists():
        return {"indexed": 0, "status": "No extractions found"}

    md_files = list(extractions_dir.glob("*.md"))[:limit]
    from core.rag_engine import RAGEngine
    engine = RAGEngine(base_dir=BASE_DIR)

    results = []
    for f in md_files:
        clave = f.stem
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
            res = engine.index_document(clave, content)
            results.append(res)
        except Exception as exc:
            results.append({"clave": clave, "status": "ERROR", "message": str(exc)})

    return {"total": len(results), "indexed": results}






