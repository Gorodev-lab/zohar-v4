#!/usr/bin/env python3
"""Backfill resiliente de metadata (FASE A / Bloque 2) para claves DGIRA sin
descripcion, usando el llama-server local DIRECTO (endpoint /completion).

Diseno:
  - Solo procesa claves en /tmp/claves_backfill.txt (las SIN descripcion).
  - CHECKPOINT en logs/backfill_done.txt: no reprocesa lo ya escrito.
  - Llamada DIRECTA a http://localhost:8083/completion con httpx (evita el
    lock global y el chat-format gemma de core.llm_client, que compite con
    zohar_api por el unico slot y se cuelga).
  - Por clave: hasta 8 reintentos si el server cancela la tarea (slot ocupado
    por zohar_api) o devuelve respuesta vacia. Espera 8s entre reintentos.
  - Salta claves cuyo .md sea inservible (<200 bytes) -> no se puede extraer.
  - Solo upsert si el JSON es valido y pasa validate_extraction.
NO pisa las 33 claves ya pobladas ni las gacetas/ASEA.
"""
import sys, time, json, logging
from pathlib import Path

sys.path.insert(0, "/home/gorops/proyectos antigravity/zohar-v4-main")
BASE = Path("/home/gorops/proyectos antigravity/zohar-v4-main")
LOG = BASE / "logs" / "backfill_resiliente.log"
DONE = BASE / "logs" / "backfill_done.txt"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s",
                    handlers=[logging.FileHandler(LOG, encoding="utf-8"), logging.StreamHandler(sys.stdout)])
log = logging.getLogger("backfill-resiliente")

import httpx
from core.metadata_extractor import locate_metadata_snippets, build_extraction_prompt, validate_extraction, upsert_metadata

EXTRACTIONS = BASE / "extractions"
CLAVE_RE = __import__("re").compile(r"^([0-9]{2}[A-Z]{2}[0-9]{4}[A-Z][0-9]{4})")
LOCAL_URL = "http://localhost:8083"
MIN_MD_BYTES = 200

def cargar_md(clave):
    for cand in [EXTRACTIONS / f"{clave}.resumen.00.md", EXTRACTIONS / f"{clave}.md"]:
        if cand.exists() and cand.stat().st_size >= MIN_MD_BYTES:
            return cand.read_text(encoding="utf-8", errors="ignore")
    return None

def llamar_server(prompt, intentos=8, timeout=120.0):
    payload = {
        "prompt": prompt,
        "temperature": 0.0,
        "n_predict": 512,
        "stop": ["<end_of_turn>", "<eos>"],
    }
    for i in range(1, intentos + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(f"{LOCAL_URL}/completion", json=payload)
            if r.status_code == 200:
                data = r.json()
                content = (data.get("content") or "").strip()
                if content:
                    return content
            # respuesta vacia o no-200: el slot estaba ocupado -> reintentar
            log.warning("  intento %d/%d: status=%s content_len=%d -> reintenta",
                        i, intentos, r.status_code, len((r.json().get('content') or '')) if r.status_code == 200 else 0)
        except Exception as e:
            log.warning("  intento %d/%d excepcion: %s", i, intentos, str(e)[:80])
        time.sleep(8)
    return None

def main():
    claves = [l.strip() for l in open("/tmp/claves_backfill.txt") if l.strip()]
    done = set()
    if DONE.exists():
        done = set(l.strip() for l in DONE.read_text().splitlines() if l.strip())
    pend = [c for c in claves if c not in done]
    log.info("Total objetivo=%d | ya hechas=%d | pendientes=%d", len(claves), len(done), len(pend))

    ok = 0
    skip_md = 0
    for idx, clave in enumerate(pend, 1):
        log.info("[%d/%d] %s", idx, len(pend), clave)
        md = cargar_md(clave)
        if not md:
            log.warning("  .md inservible (<%dB) o ausente, skip", MIN_MD_BYTES)
            skip_md += 1
            continue
        snippets = locate_metadata_snippets(md)
        prompt = build_extraction_prompt(snippets)
        raw = llamar_server(prompt)
        if not raw:
            log.error("  server sin respuesta tras reintentos, skip %s", clave)
            continue
        txt = raw.strip().strip("`").replace("```json", "").replace("```", "").strip()
        try:
            data = json.loads(txt)
        except Exception:
            log.error("  JSON no parseable, skip: %s", txt[:120])
            continue
        val = validate_extraction(clave, data)
        if not val["ok"]:
            log.warning("  validacion CRITICAL, skip: %s", val["razon"])
            continue
        res = upsert_metadata(clave, data, snippet_fuente=snippets.get("datos_generales", ""))
        if res.get("escrito"):
            ok += 1
            with DONE.open("a", encoding="utf-8") as f:
                f.write(clave + "\n")
            log.info("  OK escrito (rev=%s)", res["requiere_revision"])
        else:
            log.warning("  no escrito: %s", res.get("razon"))
    log.info("BACKFILL FINALIZADO -> escritas=%d | skip_md=%d | pendientes=%d", ok, skip_md, len(pend))

if __name__ == "__main__":
    main()
