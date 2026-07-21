import subprocess
import os
import signal
from pathlib import Path
from fastapi import APIRouter
from core.config import PROJECT_ROOT, PYTHON_EXE, PID_FILE, LOG_FILE

router = APIRouter()

def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False

@router.get("/api/rsi/status")
def rsi_status():
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            if _is_running(pid):
                return {"running": True, "pid": pid}
        except (ValueError, OSError):
            pass
        PID_FILE.unlink(missing_ok=True)
    return {"running": False}

@router.post("/api/rsi/start")
def rsi_start(iterations: int = 2):
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            if _is_running(pid):
                return {"status": "already_running", "pid": pid}
        except (ValueError, OSError):
            pass
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG_FILE, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [PYTHON_EXE, "rsi_run_all.py", "--cycles-per-target", str(iterations)],
        cwd=str(PROJECT_ROOT), stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    return {"status": "started", "pid": proc.pid}

@router.post("/api/rsi/stop")
def rsi_stop():
    if not PID_FILE.exists():
        return {"status": "not_running"}
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, ValueError, OSError):
        pass
    PID_FILE.unlink(missing_ok=True)
    return {"status": "stopped"}
