#!/usr/bin/env python3
"""
zohar_harness.py — Harness de Maniobra Única para Zohar v4.
Comprueba todos los servicios (FastAPI 8004, Llama-Server 8083, Postgres 5432, Redis 6379, Neo4j 7474),
auto-restaura contenedores/procesos caídos y ejecuta un ping de sanidad en tiempo real
al modelo local Gemma 4 E2B.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent
LOCAL_LLM_URL = os.environ.get("LOCAL_LLM_URL", "http://127.0.0.1:8083")

SERVICES = {
    "FastAPI API": {"host": "127.0.0.1", "port": 8004, "critical": True},
    "Llama-Server (Gemma 4 E2B)": {"host": "127.0.0.1", "port": 8083, "critical": True},
    "PostgreSQL": {"host": "127.0.0.1", "port": 5432, "critical": True},
    "Redis": {"host": "127.0.0.1", "port": 6379, "critical": False},
    "Neo4j": {"host": "127.0.0.1", "port": 7474, "critical": False},
}


def check_port(host: str, port: int, timeout: float = 0.5) -> bool:
    """Comprueba si un puerto TCP está respondiendo."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def run_cmd(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    """Ejecuta un comando en shell y devuelve (exit_code, output)."""
    try:
        p = subprocess.run(cmd, cwd=str(cwd or PROJECT_ROOT), capture_output=True, text=True, timeout=60)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as exc:
        return -1, str(exc)


def restore_docker_stack() -> bool:
    """Intenta restaurar los contenedores Docker caídos."""
    print("  [AUTO-RESTAURACIÓN] Levantando contenedores Docker...")
    code, out = run_cmd(["docker", "compose", "-f", "dw/docker-compose.yml", "up", "-d"])
    if code == 0:
        print("  [PASS] Contenedores iniciados correctamente.")
        time.sleep(3.0)
        return True
    else:
        print(f"  [FAIL] Error iniciando Docker: {out}")
        return False


def test_local_llm_ping() -> dict:
    """Ejecuta un ping de sanidad en tiempo real al modelo local Gemma 4 E2B."""
    print("  [SANITY CHECK] Probando inferencia con el modelo local Gemma 4 E2B...")
    t0 = time.time()
    payload = {
        "prompt": "<start_of_turn>user\nPing de diagnóstico Zohar v4: confirma estado OK.<end_of_turn>\n<start_of_turn>model\n",
        "n_predict": 32,
        "temperature": 0.1,
        "stop": ["<end_of_turn>", "<eos>"]
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            res = client.post(f"{LOCAL_LLM_URL}/completion", json=payload)
            elapsed_ms = int((time.time() - t0) * 1000)
            if res.status_code == 200:
                data = res.json()
                content = data.get("content", "").strip()
                print(f"  [PASS] Inferencia exitosa en {elapsed_ms}ms! Respuesta: \"{content[:60]}...\"")
                return {
                    "status": "PASS",
                    "latency_ms": elapsed_ms,
                    "response_preview": content[:100],
                }
            else:
                print(f"  [WARN] Respuesta HTTP {res.status_code} de Llama-Server: {res.text[:100]}")
                return {"status": "FAIL", "error": f"HTTP {res.status_code}"}
    except Exception as exc:
        print(f"  [FAIL] Error conectando con Llama-Server: {exc}")
        return {"status": "FAIL", "error": str(exc)}


def run_harness() -> dict:
    """Ejecuta la maniobra completa de verificación, restauración y sanidad."""
    print("==========================================================")
    print("  ZOHAR V4 — HARNESS DE MANIOBRA ÚNICA & DIAGNÓSTICO")
    print("==========================================================")

    results = {}
    down_found = False

    # 1. Comprobar todos los puertos
    for name, spec in SERVICES.items():
        ok = check_port(spec["host"], spec["port"])
        results[name] = {"port": spec["port"], "status": "ONLINE" if ok else "OFFLINE"}
        if ok:
            print(f"  [ONLINE]  {name:<28} (Puerto {spec['port']})")
        else:
            print(f"  [OFFLINE] {name:<28} (Puerto {spec['port']})")
            if spec["critical"]:
                down_found = True

    # 2. Auto-restauración si hay fallos críticos
    if down_found:
        print("\n  [WARN] Servicios críticos no detectados. Intentando auto-restauración...")
        restore_docker_stack()
        # Re-comprobar
        for name, spec in SERVICES.items():
            ok = check_port(spec["host"], spec["port"])
            results[name]["status"] = "ONLINE" if ok else "OFFLINE"

    # 3. Sanity check de inferencia local
    llm_test = test_local_llm_ping()
    results["LLM_Sanity_Test"] = llm_test

    # 4. Dictamen global (Green Light Status)
    all_critical_online = all(
        results[name]["status"] == "ONLINE"
        for name, spec in SERVICES.items() if spec["critical"]
    )
    green_light = all_critical_online and (llm_test["status"] == "PASS")

    report = {
        "green_light": green_light,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "services": results,
    }

    print("\n----------------------------------------------------------")
    if green_light:
        print("  [GREEN LIGHT STATUS]: TODO EL STACK ESTÁ DISPONIBLE Y OPERATIVO.")
    else:
        print("  [RED LIGHT STATUS]: ALGUNOS SERVICIOS REQUIEREN ATENCIÓN.")
    print("----------------------------------------------------------\n")

    return report


if __name__ == "__main__":
    rep = run_harness()
    if not rep["green_light"]:
        sys.exit(1)
