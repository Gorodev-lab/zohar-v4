#!/usr/bin/env python3
"""
medir_recuperacion_a.py - Al terminar FASE A (OCR), mide cuantas del grupo (a)
se recuperaron (ganaron .md en extractions/) y actualiza fallidas_razon.json.

NO lanza nada. Solo mide y reporta. Se ejecuta UNA vez cuando FASE A termina.
Toda la logica esta bajo main()/if __name__ para NO ejecutarse al importar.
"""
import os, re, json
from pathlib import Path

BASE = Path("/home/gorops/proyectos antigravity/zohar-v4-main")
CLAVE_RE = re.compile(r"^([0-9]{2}[A-Z]{2}[0-9]{4}[A-Z][0-9]{4})")


def main():
    a = [l.strip() for l in open(BASE / ".reintento_a.txt") if l.strip()]

    # .md en extractions
    ext = set()
    for fn in os.listdir(BASE / "extractions"):
        m = CLAVE_RE.match(fn)
        if m:
            ext.add(m.group(1))

    recuperadas = [k for k in a if k in ext]
    pendientes = [k for k in a if k not in ext]

    # Actualizar registro
    reg = json.load(open(BASE / "fallidas_razon.json"))
    for k in recuperadas:
        if k in reg:
            reg[k]["recuperada_ocr"] = True
            reg[k]["grupo"] = "recuperada"
    for k in pendientes:
        if k in reg:
            reg[k]["recuperada_ocr"] = False
    json.dump(reg, open(BASE / "fallidas_razon.json", "w"), indent=2, ensure_ascii=False)

    print("=" * 50)
    print("RESULTADO FINAL REINTENTO OCR - GRUPO (a)")
    print("=" * 50)
    print(f"grupo (a) total           : {len(a)}")
    print(f"RECUPERADAS (tienen .md)  : {len(recuperadas)}")
    print(f"SIGUEN SIN .md (fallo)    : {len(pendientes)}")
    print(f"tasa recup.               : {100*len(recuperadas)/len(a):.1f}%")
    print("=" * 50)
    if pendientes:
        print("Claves que SIGUEN en fallidas (no recuperadas):")
        for k in sorted(pendientes):
            print("  ", k)
    print("=" * 50)
    print("fallidas_razon.json actualizado.")


if __name__ == "__main__":
    main()
