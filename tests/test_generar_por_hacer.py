"""Prueba de contrato para generar_por_hacer.py.

Verifica la invariante del cruce:
  por_hacer.txt solo contiene claves pendientes que (1) ya tienen PDF en
  downloads/ y (2) carecen de extraccion en extractions/, todas con el
  formato DGIRA de 13 caracteres.
"""
import os
import re
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPT = BASE / "generar_por_hacer.py"
# Mismo patron de PREFIJO que usa generar_por_hacer.py (sin $): los nombres de
# archivo llevan sufijo (.resumen.00.pdf, .md), asi que el match debe tomar el
# prefijo de 13 chars, no exigir fin de cadena.
CLAVE_RE = re.compile(r"^([0-9]{2}[A-Z]{2}[0-9]{4}[A-Z][0-9]{4})")


def _claves_pdfs():
    s = set()
    for root, _, files in os.walk(BASE / "downloads"):
        for fn in files:
            if fn.lower().endswith(".pdf"):
                m = CLAVE_RE.match(fn)
                if m:
                    s.add(m.group(1))
    return s


def _claves_extraidas():
    s = set()
    for fn in os.listdir(BASE / "extractions"):
        m = CLAVE_RE.match(fn)
        if m:
            s.add(m.group(1))
    return s


def test_generar_por_hacer_pasa_contrato():
    # Ejecuta el script en su propio proceso
    res = subprocess.run(
        ["python3", str(SCRIPT)], cwd=BASE, capture_output=True, text=True
    )
    assert res.returncode == 0, f"script fallo: {res.stderr}"

    salida = BASE / "por_hacer.txt"
    assert salida.exists()
    lineas = [l.strip() for l in salida.read_text().splitlines() if l.strip()]

    pdfs = _claves_pdfs()
    ext = _claves_extraidas()

    # Saneo de ruta para depurar (no afecta aserciones)
    assert (BASE / "downloads").exists(), f"no existe {BASE/'downloads'}"
    print(f"[debug] pdfs={len(pdfs)} ext={len(ext)} por_hacer={len(lineas)}")

    # 1) todas tienen PDF
    sin_pdf = [c for c in lineas if c not in pdfs]
    assert not sin_pdf, f"claves sin PDF en por_hacer.txt: {sin_pdf[:5]}"

    # 2) ninguna ya extraida
    ya_ext = [c for c in lineas if c in ext]
    assert not ya_ext, f"claves ya extraidas en por_hacer.txt: {ya_ext[:5]}"

    # 3) formato 13 chars valido
    malas = [c for c in lineas if not CLAVE_RE.match(c)]
    assert not malas, f"formato invalido: {malas[:5]}"

    # 4) unicas
    assert len(lineas) == len(set(lineas)), "hay duplicados"


def test_idempotente():
    subprocess.run(["python3", str(SCRIPT)], cwd=BASE, capture_output=True, text=True)
    a = [l.strip() for l in (BASE / "por_hacer.txt").read_text().splitlines() if l.strip()]
    subprocess.run(["python3", str(SCRIPT)], cwd=BASE, capture_output=True, text=True)
    b = [l.strip() for l in (BASE / "por_hacer.txt").read_text().splitlines() if l.strip()]
    assert set(a) == set(b)


if __name__ == "__main__":
    import unittest

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(
        loader.loadTestsFromNames(
            ["test_generar_por_hacer.test_generar_por_hacer_pasa_contrato",
             "test_generar_por_hacer.test_idempotente"]
        )
    )
    res = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if res.wasSuccessful() else 1)
