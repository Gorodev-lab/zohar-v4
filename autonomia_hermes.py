#!/usr/bin/env python3
"""
autonomia_hermes.py - Modo autonomo desatendido para Zohar v4.

Implementa el protocolo de operacion autonoma:
  * Bucle de monitoreo cada CYCLE_MIN minutos del job de OCR (PID hijo real).
  * Si el OCR cae a 0% CPU > 5 min o se cuelga -> kill -15, registrar ultimo
    PDF en ocr_bloqueados.txt, y relanzar extraer_corpus_faltante.py.
  * Monitor de disco: si libre < DISK_MIN_GB -> frenar OCR, truncar logs,
    relanzar en lote pequeño.
  * Reintento de descargas (fallidas.txt, 117 claves) con throttle 5-10s
    cuando la CPU baje lo suficiente y no haya OCR corriendo.
  * Log acumulativo en logs/autonomia_hermes.log.

Diseno defensivo: no pide confirmacion; toma decisiones y continua.
El propio monitor es quien relanza los jobs, asi que NO debe auto-matarse.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

from datetime import datetime

# --- Configuracion del protocolo ---
CYCLE_MIN = 12              # intervalo de chequeo (minutos)
CPU_RUN_THRESHOLD = 50.0  # % CPU: si esta por encima, el OCR "trabaja"
ZERO_CPU_GRACE = 5 * 60    # segundos a 0% CPU antes de declarar colgado
DISK_MIN_GB = 1.5          # umbral critico de disco libre
OCR_BATCH = 20             # tamano de lote al relanzar OCR
DOWNLOAD_THROTTLE = (5.0, 10.0)  # segundos entre claves en reintento

VENV_PY = PROJECT / ".venv" / "bin" / "python"
EXTRACTER = PROJECT / "extraer_corpus_faltante.py"
DOWNLOADER = PROJECT / "run_descargas_faltantes.py"
FALLIDAS = PROJECT / "fallidas.txt"
OCR_BLOCKED = PROJECT / "ocr_bloqueados.txt"
AUTO_LOG = PROJECT / "logs" / "autonomia_hermes.log"

LOGS_DIR = PROJECT / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# --- Logging acumulativo ---
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(AUTO_LOG, encoding="utf-8"),
              logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("AUTONOMIA")


def ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run(cmd, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def find_ocr_child() -> int | None:
    """Devuelve el PID del proceso python de extraccion OCR vivo, o None.

    pgrep -f matchea tanto el wrapper bash como el python hijo (y hasta el
    propio comando que contiene la cadena). El proceso REAL es el python,
    no el bash -lic wrapper. Filtramos por comm=='python' para evitar
    confundir el wrapper (siempre a 0% CPU) con el worker de OCR.
    """
    try:
        out = run(["pgrep", "-f", "extraer_corpus_faltante.py"])
        pids = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
        pids = [p for p in pids if p != os.getpid()]
        for p in pids:
            try:
                comm = (Path(f"/proc/{p}/comm").read_text(errors="ignore")).strip()
            except Exception:
                comm = ""
            if comm == "python":  # el worker real, no el bash wrapper
                return p
        return None
    except Exception:
        return None


def get_cpu(pid: int) -> float:
    """CPU% del proceso (muestra unica). 0.0 si no existe."""
    try:
        # ps -o %cpu= -p PID  (una muestra instantanea)
        out = run(["ps", "-o", "%cpu=", "-p", str(pid)])
        v = out.stdout.strip()
        return float(v) if v else 0.0
    except Exception:
        return 0.0


def disk_free_gb() -> float:
    try:
        st = os.statvfs("/")
        return st.f_bavail * st.f_bsize / 1e9
    except Exception:
        return -1.0


def last_pdf_in_log() -> str:
    """Ultimo PDF que el OCR intento procesar (linea [n/250] ...pdf)."""
    p = LOGS_DIR / "extraer_corpus.log"
    if not p.exists():
        return ""
    last = ""
    m = re.compile(r"\[(\d+)/\d+\]\s+(\S+\.pdf)")
    for line in p.read_text(errors="ignore").splitlines():
        mm = m.search(line)
        if mm:
            last = mm.group(2)
    return last


def count_md() -> int:
    return len(list((PROJECT / "extractions").glob("*.md")))


def truncate_logs_safe():
    """Trunca logs pesados sin borrar descriptores de jobs vivos (solo los que
    podemos). debug.log de neo4j requiere sudo; lo intentamos sin sudo y seguimos."""
    targets = [
        LOGS_DIR / "extraer_corpus.run.out",
        LOGS_DIR / "descargas_faltantes.run.out",
        LOGS_DIR / "descargas_faltantes.log",
        PROJECT / "dw" / "neo4j_logs" / "debug.log",
    ]
    for t in targets:
        try:
            if t.exists():
                with open(t, "r+") as f:
                    f.truncate(0)
                log.info("Log truncado: %s", t.name)
        except Exception as exc:
            log.warning("No se pudo truncar %s: %s", t, exc)


def kill_ocr(pid: int) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)  # kill -15
        log.info("SIGTERM enviado a OCR PID %d", pid)
        # esperar a que muera
        for _ in range(30):
            if get_cpu(pid) == 0.0 and not Path(f"/proc/{pid}").exists():
                break
            time.sleep(1)
        return True
    except Exception as exc:
        log.error("Fallo al matar OCR %d: %s", pid, exc)
        return False


def launch_ocr(batch: int = OCR_BATCH) -> int | None:
    cmd = [str(VENV_PY), str(EXTRACTER), f"--lote-max={batch}",
           f"--throttle-min=0.2", f"--throttle-max=1.0"]
    p = subprocess.Popen(cmd, stdout=(LOGS_DIR / "extraer_corpus.run.out").open("a"),
                         stderr=subprocess.STDOUT, cwd=str(PROJECT))
    log.info("OCR relanzado (PID %d, lote=%d): %s", p.pid, batch, " ".join(cmd))
    return p.pid


def launch_downloads(input_file: Path, tmin: float, tmax: float) -> int | None:
    cmd = [str(VENV_PY), str(DOWNLOADER), "-i", str(input_file),
           "--throttle-min", str(tmin), "--throttle-max", str(tmax)]
    p = subprocess.Popen(cmd, stdout=(LOGS_DIR / "descargas_faltantes.run.out").open("a"),
                         stderr=subprocess.STDOUT, cwd=str(PROJECT))
    log.info("Reintento descargas relanzado (PID %d): %s", p.pid, " ".join(cmd))
    return p.pid


def downloads_running() -> bool:
    out = run(["pgrep", "-f", "run_descargas_faltantes.py"])
    return bool(out.stdout.strip()) and os.getpid() not in [int(x) for x in out.stdout.split() if x.strip().isdigit()]


def main_loop():
    log.info("=== AUTONOMIA HERMES INICIADA (PID %d) ===", os.getpid())
    log.info("Protocolo: ciclo=%dmin, cpu_run>=%.0f, disco_min=%.1fGB, ocr_batch=%d",
             CYCLE_MIN, CPU_RUN_THRESHOLD, DISK_MIN_GB, OCR_BATCH)
    zero_cpu_since = None
    download_attempted = False

    while True:
        try:
            # 1) Estado de disco (critico primero)
            free = disk_free_gb()
            log.info("[ciclo] disco libre=%.2f GB | .md en extractions=%d", free, count_md())

            ocr_pid = find_ocr_child()

            # 2) Monitor de OCR
            if ocr_pid is None:
                log.info("[ocr] no hay proceso OCR vivo. Relanzando lote...")
                launch_ocr()
                zero_cpu_since = None
            else:
                cpu = get_cpu(ocr_pid)
                log.info("[ocr] PID %d CPU=%.1f%%", ocr_pid, cpu)
                if cpu < CPU_RUN_THRESHOLD:
                    if zero_cpu_since is None:
                        zero_cpu_since = time.time()
                    idle = time.time() - zero_cpu_since
                    if idle >= ZERO_CPU_GRACE:
                        log.warning("[ocr] CPU 0%% por %.0fs -> declarado colgado", idle)
                        last = last_pdf_in_log()
                        with OCR_BLOCKED.open("a", encoding="utf-8") as f:
                            f.write(f"{ts()}\t{last}\n")
                        log.info("[ocr] ultimo PDF registrado en ocr_bloqueados.txt: %s", last)
                        kill_ocr(ocr_pid)
                        # Relanzar en lote pequeño
                        launch_ocr(batch=OCR_BATCH)
                        zero_cpu_since = None
                else:
                    zero_cpu_since = None  # actividad detectada, reset

            # 3) Defensa de disco: si libre < umbral, frenar OCR y truncar
            if free > 0 and free < DISK_MIN_GB:
                log.warning("[disco] LIBRE %.2f GB < %.1f GB -> defensa", free, DISK_MIN_GB)
                pid2 = find_ocr_child()
                if pid2:
                    kill_ocr(pid2)
                truncate_logs_safe()
                # Relanzar en lote mas pequeño para aliviar escritura
                launch_ocr(batch=max(5, OCR_BATCH // 4))
                # pequeña pausa para liberar espacio
                time.sleep(30)

            # 4) Reintento de descargas cuando la CPU general este baja y no haya OCR
            if not download_attempted and not downloads_running():
                # Muestrear carga global rapida (1 muestra de carga a 1 min)
                try:
                    load1 = os.getloadavg()[0]
                except Exception:
                    load1 = 99.0
                if load1 < 8.0 and ocr_pid is None:
                    if FALLIDAS.exists() and FALLIDAS.read_text().strip():
                        log.info("[descargas] CPU/libre, reintentando 117 fallidas (throttle 5-10s)")
                        launch_downloads(FALLIDAS, DOWNLOAD_THROTTLE[0], DOWNLOAD_THROTTLE[1])
                        download_attempted = True
                    else:
                        log.info("[descargas] fallidas.txt vacio; nada que reintentar")
                        download_attempted = True
                else:
                    log.info("[descargas] carga alta (load=%.1f) o OCR activo; difiriendo", load1)

            # 5) ¿Terminamos? Si no hay OCR y no quedan PDFs sin .md (aprox) y descargas hechas
            if ocr_pid is None and downloads_running() is False and download_attempted:
                md = count_md()
                # Criterio de cierre aproximado: extras ya poblado y sin PDFs pendientes
                if md >= 390:  # ya tenemos ~397; considerar completado
                    log.info("[cierre] extractions=%d, OCR y descargas completos. Deteniendo bucle.", md)
                    break

        except Exception as exc:
            log.exception("[bucle] excepcion no fatal: %s", exc)

        time.sleep(CYCLE_MIN * 60)


if __name__ == "__main__":
    main_loop()
