#!/usr/bin/env python3
"""Backfill determinista de metadata (FASE A / Bloque 2, SIN LLM) para claves
DGIRA sin descripcion, usando regex sobre el texto de los .md ya extraidos.

Por que existe:
  El llama-server local (maritime_llama_cpp) esta congelado (health OK pero
  /completion no responde; 1 slot, CPU ~8 tok/s, tareas se cancelan). Eso
  bloquea la extraccion con LLM. Este script extrae lo determinista posible
  SIN el server, para no dejar las 117 claves vacias.

Que extrae (regex robustos sobre espanol de MIA):
  - estado: "estado de <X>" / "en <X>" tras municipio / lista cerrada 32 entidades
  - municipio: "municipio de <X>" / "en el municipio de <X>"
  - localidad: "localidad de <X>" / "en la localidad de <X>"
  - descripcion_proyecto: primeras ~3 oraciones de la seccion de descripcion
    (cap. II / DESCRIPCION DE LAS OBRAS...), recortado a 800 chars.
  - confianza_extraccion: 'baja' (determinista, no LLM)
  - requiere_revision: 1 (siempre, para reproceso LLM posterior)
  - campos_faltantes: los no capturados por regex.

Seguridad de datos (CRITICA):
  NO usa INSERT OR REPLACE ciego. Hace UPDATE SET campo=solo si el nuevo valor
  es no-vacio Y el existente es NULL (COALESCE). Asi NUNCA pisa promovente/
  municipio/estado ya poblados en otras claves, y no destruye datos previos.

Checkpoint en logs/backfill_det_done.txt para no reprocesar.
"""
import sys, os, re, json, sqlite3, logging
from pathlib import Path

sys.path.insert(0, "/home/gorops/proyectos antigravity/zohar-v4-main")
BASE = Path("/home/gorops/proyectos antigravity/zohar-v4-main")
DB = BASE / "data" / "metadata_proyecto.db"
EXTRACTIONS = BASE / "extractions"
LOG = BASE / "logs" / "backfill_determinista.log"
DONE = BASE / "logs" / "backfill_det_done.txt"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s",
                    handlers=[logging.FileHandler(LOG, encoding="utf-8"), logging.StreamHandler(sys.stdout)])
log = logging.getLogger("backfill-determinista")

from core.metadata_extractor import locate_metadata_snippets
import core.metadata_extractor as me

ESTADOS = me.ESTADOS_VALIDOS
RE_ESTADO = re.compile(r"\b(?:en el )?estado de ([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ]+)?)", re.IGNORECASE)
RE_MUN = re.compile(r"\b(?:en el )?municipio de ([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ]+)?)", re.IGNORECASE)
RE_LOC = re.compile(r"\b(?:en la )?localidad de ([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ]+)?)", re.IGNORECASE)

MIN_MD = 200

def cargar_md(clave):
    """Busca TODOS los .md cuyo nombre empieza por la clave y devuelve el de
    mayor tamaño >= MIN_MD (el que tiene contenido real de OCR)."""
    if not EXTRACTIONS.is_dir():
        return None
    mejor = None
    mejor_tam = 0
    for fn in os.listdir(EXTRACTIONS):
        if fn.endswith(".md") and fn.startswith(clave):
            p = EXTRACTIONS / fn
            try:
                tam = p.stat().st_size
            except OSError:
                continue
            if tam >= MIN_MD and tam > mejor_tam:
                mejor = p
                mejor_tam = tam
    if mejor is None:
        return None
    return mejor.read_text(encoding="utf-8", errors="ignore")

def extraer_campos(texto):
    out = {"estado": None, "municipio": None, "localidad": None, "descripcion_proyecto": None}
    # 1) Ubicacion: buscar primero patrones explicitos en todo el texto
    m = RE_MUN.search(texto)
    if m:
        out["municipio"] = m.group(1).strip().title()
    m = RE_LOC.search(texto)
    if m:
        out["localidad"] = m.group(1).strip().title()
    # estado: buscar "estado de X" y validar contra lista cerrada
    for m in RE_ESTADO.finditer(texto):
        cand = m.group(1).strip().upper()
        # normalizar acentos simples
        norm = cand.replace("Ñ", "Ñ")
        if norm in ESTADOS:
            out["estado"] = cand.title()
            break
        # tambien aceptar si el candidato (sin acentos) matchea
        if cand.replace("Á","A").replace("É","E").replace("Í","I").replace("Ó","O").replace("Ú","U") in ESTADOS:
            out["estado"] = cand.title()
            break
    # 2) Descripcion: de la seccion de descripcion (snippets), primeras oraciones
    sn = locate_metadata_snippets(texto)
    desc = sn.get("descripcion", "")
    if not desc or len(desc.strip()) < 100:
        desc = sn.get("prefijo", "")
    # limpiar ruido: markdown, lineas de indice (".... 4 I.1 NOMBRE..."), saltos
    desc = re.sub(r"\*\*", "", desc)
    desc = re.sub(r"\.{3,}\s*\d+\s*$", "", desc)          # ".... 4" final de indice
    desc = re.sub(r"\n{2,}", " ", desc)
    desc = re.sub(r"\s{2,}", " ", desc).strip()
    # recortar a ~3 oraciones / 800 chars
    fracs = re.split(r"(?<=[.])\s+", desc)
    extrac = " ".join(fracs[:3]).strip()
    if len(extrac) > 800:
        extrac = extrac[:797].rstrip() + "..."
    out["descripcion_proyecto"] = extrac or None
    return out

def upsert_seguro(clave, campos, snippet):
    conn = sqlite3.connect(DB)
    try:
        cur = conn.execute("SELECT promovente, municipio, estado, localidad, descripcion_proyecto, campos_faltantes, confianza_extraccion FROM metadata_proyecto WHERE clave=?", (clave,)).fetchone()
        if not cur:
            log.warning("  clave %s no existe en DB, skip", clave)
            return False
        prom, mun, est, loc, desc, cf, conf = cur
        nuevos = {
            "municipio": mun or campos["municipio"],
            "estado": est or campos["estado"],
            "localidad": loc or campos["localidad"],
            "descripcion_proyecto": desc or campos["descripcion_proyecto"],
        }
        falt = [c for c in ["promovente", "municipio", "estado", "localidad", "descripcion_proyecto"] if not nuevos.get(c)]
        conn.execute(
            """UPDATE metadata_proyecto SET
                 municipio=?, estado=?, localidad=?, descripcion_proyecto=?,
                 confianza_extraccion='baja', requiere_revision=1,
                 campos_faltantes=?, snippet_fuente=COALESCE(NULLIF(snippet_fuente,''), ?),
                 fecha_extraccion=?, version_prompt='v1-det-determinista'
               WHERE clave=?""",
            (nuevos["municipio"], nuevos["estado"], nuevos["localidad"], nuevos["descripcion_proyecto"],
             json.dumps(falt, ensure_ascii=False), (snippet or "")[:4000],
             __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
             clave),
        )
        conn.commit()
        return True
    finally:
        conn.close()

def main():
    claves = [l.strip() for l in open("/tmp/claves_backfill.txt") if l.strip()]
    done = set()
    if DONE.exists():
        done = set(l.strip() for l in DONE.read_text().splitlines() if l.strip())
    pend = [c for c in claves if c not in done]
    log.info("Total objetivo=%d | ya hechas=%d | pendientes=%d", len(claves), len(done), len(pend))
    ok = 0
    for idx, clave in enumerate(pend, 1):
        md = cargar_md(clave)
        if not md:
            log.warning("[%d/%d] %s .md inservible, skip", idx, len(pend), clave)
            continue
        campos = extraer_campos(md)
        sn = locate_metadata_snippets(md)
        if upsert_seguro(clave, campos, sn.get("datos_generales", "")):
            ok += 1
            with DONE.open("a", encoding="utf-8") as f:
                f.write(clave + "\n")
            log.info("[%d/%d] %s OK mun=%s est=%s loc=%s desc=%dch", idx, len(pend), clave,
                     campos["municipio"], campos["estado"], campos["localidad"],
                     len(campos["descripcion_proyecto"] or ""))
    log.info("BACKFILL DETERMINISTA FINALIZADO -> escritas=%d / pendientes=%d", ok, len(pend))

if __name__ == "__main__":
    main()
