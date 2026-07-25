#!/usr/bin/env python3
"""
generar_por_hacer.py - Cruce de estado del pipeline Zohar v4.

Genera 'por_hacer.txt' con las claves de 'claves_pendientes.txt' que:
  (1) ya tienen un PDF descargado en downloads/  (cruce por nombre de archivo)
  (2) y carecen de extraccion en extractions/    (cruce por prefijo de clave)

Es decir: claves listas para FASE A (procesar lo ya descargado, sin scraping).
"""
import os
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
CLAVES_PEND = BASE / "claves_pendientes.txt"
DOWNLOADS = BASE / "downloads"
EXTRACTIONS = BASE / "extractions"
SALIDA = BASE / "por_hacer.txt"

# Patron DGIRA de 13 chars: NN LL NNNN L NNNN
CLAVE_RE = re.compile(r"^([0-9]{2}[A-Z]{2}[0-9]{4}[A-Z][0-9]{4})")


def sanar_clave_dgira(clave_sucia):
    """Mismo auto-sanador del orquestador run_dgira_batch_01.py."""
    clave = clave_sucia.strip().upper()
    if len(clave) != 13:
        return None
    fixes: dict[str, str] = {'O': '0', 'I': '1', 'L': '1', 'S': '5', 'Z': '2'}
    letra_fix: dict[str, str] = {'0': 'O', '1': 'I', '5': 'S'}
    mascara = "NNLLNNNNLNNNN"
    out: str = ""
    for i, ch in enumerate(clave):
        if mascara[i] == 'N':
            repl: str = fixes[ch] if ch in fixes else ch
        else:
            repl = letra_fix[ch] if ch in letra_fix else ch
        out += repl
    if CLAVE_RE.match(out):
        return out
    return None


def claves_desde_pdfs():
    """Claves unicas derivadas de los nombres de PDF en downloads/ (recursivo)."""
    encontradas = set()
    if not DOWNLOADS.exists():
        return encontradas
    for root, _, files in os.walk(DOWNLOADS):
        for fn in files:
            if fn.lower().endswith(".pdf"):
                m = CLAVE_RE.match(fn)
                if m:
                    encontradas.add(m.group(1))
    return encontradas


def claves_desde_extractions():
    """Claves unicas ya procesadas, derivadas del prefijo de archivos en extractions/."""
    encontradas = set()
    if not EXTRACTIONS.exists():
        return encontradas
    for fn in os.listdir(EXTRACTIONS):
        m = CLAVE_RE.match(fn)
        if m:
            encontradas.add(m.group(1))
    return encontradas


def main():
    # 1) Claves pendientes (sanitizadas)
    pendientes = []
    with open(CLAVES_PEND, encoding="utf-8") as f:
        for line in f:
            c = line.strip()
            if not c:
                continue
            s = sanar_clave_dgira(c) or c.upper()
            pendientes.append(s)
    pendientes_unicas = set(pendientes)

    # 2) PDFs ya en disco
    con_pdf = claves_desde_pdfs()

    # 3) Extracciones ya hechas
    con_ext = claves_desde_extractions()

    # 4) Cruces
    pend_con_pdf = pendientes_unicas & con_pdf
    pend_sin_pdf = pendientes_unicas - con_pdf
    pend_con_pdf_sin_ext = pend_con_pdf - con_ext

    # 5) por_hacer.txt: claves pendientes con PDF pero sin extraccion
    por_hacer = sorted(pend_con_pdf_sin_ext)
    with open(SALIDA, "w", encoding="utf-8") as f:
        for c in por_hacer:
            f.write(c + "\n")

    # Reporte
    print("=" * 60)
    print("CRUCE DE ESTADO - Zohar v4")
    print("=" * 60)
    print(f"claves_pendientes.txt (unicas)      : {len(pendientes_unicas)}")
    print(f"claves con PDF en downloads/         : {len(con_pdf)}")
    print(f"claves con extraccion en extractions/: {len(con_ext)}")
    print("-" * 60)
    print(f"pendientes CON pdf                  : {len(pend_con_pdf)}")
    print(f"pendientes SIN pdf (falta descargar): {len(pend_sin_pdf)}")
    print(f"CON pdf PERO SIN extraccion         : {len(pend_con_pdf_sin_ext)}  <- por_hacer.txt")
    print("=" * 60)
    print(f"Escrito: {SALIDA}  ({len(por_hacer)} claves)")


if __name__ == "__main__":
    main()
