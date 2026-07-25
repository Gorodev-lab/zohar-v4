"""Smoke tests for organizar_y_procesar.py (pure logic, no LLM).

Verifica:
- extraer_clave devuelve tupla (resumen_md, full_md) y omite estudio/resolutivo
  salvo --full.
- analizar_clave acota resumen >6k chars a head+tail (~6k) antes de inferencia,
  y NO reescribe si ya es pequeno.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import organizar_y_procesar as m


def _fake_pdfs(clave):
    return [
        Path(f"downloads/{clave}.resumen.00.pdf"),
        Path(f"downloads/{clave}.estudio.01.pdf"),
        Path(f"downloads/{clave}.resolutivo.01.pdf"),
    ]


def test_extraer_clave_resumen_only_no_full(tmp_path, monkeypatch):
    # Redirigir dirs a tmp para no tocar el corpus real.
    monkeypatch.setattr(m, "EXTRACTIONS", tmp_path)
    monkeypatch.setattr(m, "DOWNLOADS", tmp_path)
    # Evitar import pesado de core.pdf_processor: stub de _extraer_paginas.
    monkeypatch.setattr(m, "_extraer_paginas", lambda pdfs, clave, dry: ["# x"])

    resumen_md, full_md = m.extraer_clave("TEST123456789", _fake_pdfs("TEST123456789"), dry=False, full=False)
    assert resumen_md.name == "TEST123456789.resumen.md"
    assert full_md is None  # sin --full no extrae estudio/resolutivo


def test_extraer_clave_full_extrae_completo(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "EXTRACTIONS", tmp_path)
    monkeypatch.setattr(m, "DOWNLOADS", tmp_path)
    monkeypatch.setattr(m, "_extraer_paginas", lambda pdfs, clave, dry: ["# x"])

    resumen_md, full_md = m.extraer_clave("TEST123456789", _fake_pdfs("TEST123456789"), dry=False, full=True)
    assert resumen_md.name == "TEST123456789.resumen.md"
    assert full_md is not None and full_md.name == "TEST123456789.md"


def test_analizar_acota_resumen_grande(tmp_path, monkeypatch):
    # Preparar un resumen grande en EXTRACTIONS simulado.
    monkeypatch.setattr(m, "EXTRACTIONS", tmp_path)
    monkeypatch.setattr(m, "INFERENCE", tmp_path / "inf")
    big = "A" * 2000 + "B" * 2000 + "C" * 2000 + "D" * 2000 + "E" * 2000 + "F" * 2000  # 12k
    rp = tmp_path / "K.resumen.md"
    rp.write_text(big, encoding="utf-8")

    captured = {}

    import core.inference_engine as ie

    def fake_generate_report(path, **kwargs):
        captured["path"] = path
        captured["text"] = path.read_text(encoding="utf-8")
        return {"veredicto": "X"}

    monkeypatch.setattr(ie, "generate_report", fake_generate_report)

    rep = m.analizar_clave("K", rp, dry=False, force=True)
    assert rep == {"veredicto": "X"}
    txt = captured["text"]
    # head (2500) + sep + tail (3500) ≈ 6000, no los 12k originales.
    assert len(txt) <= 6100, len(txt)
    assert "medio omitido" in txt
    assert txt.startswith("A")
    assert txt.rstrip().endswith("F")


def test_analizar_no_acota_resumen_pequeno(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "EXTRACTIONS", tmp_path)
    monkeypatch.setattr(m, "INFERENCE", tmp_path / "inf")
    small = "corto"
    rp = tmp_path / "K.resumen.md"
    rp.write_text(small, encoding="utf-8")

    captured = {}

    import core.inference_engine as ie

    def fake_generate_report(path, **kwargs):
        captured["path"] = path
        return {"veredicto": "Y"}

    monkeypatch.setattr(ie, "generate_report", fake_generate_report)
    m.analizar_clave("K", rp, dry=False, force=True)
    # No debe crear archivo .infer.md si cabe.
    assert not (tmp_path / "K.resumen.infer.md").exists()
    assert captured["path"] == rp
