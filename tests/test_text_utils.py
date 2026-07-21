import pytest
from core.text_utils import build_targeted_snippet

def test_build_targeted_snippet_basic():
    # Caso simple: texto vacío
    assert build_targeted_snippet("") == ""
    assert build_targeted_snippet(None) == ""

    # Caso simple: texto corto menor que prefix_chars
    text = "Proyecto ambiental en Colima promovido por Goro."
    assert build_targeted_snippet(text, prefix_chars=100) == text

def test_build_targeted_snippet_keywords():
    # Texto con palabras clave fuera del prefijo
    prefix = "X" * 100
    keyword_part = "El promovente es Empresa_Ambiente ubicados en el municipio de Colima."
    full_text = prefix + " " + keyword_part

    # Si prefix_chars es 100, se incluye el prefix y se buscan las palabras clave
    snippet = build_targeted_snippet(full_text, prefix_chars=100, window_chars=50, max_total_chars=1000)
    
    # Debe contener el prefijo
    assert "X" * 100 in snippet
    # Debe contener la ventana del promovente
    assert "promovente" in snippet.lower()
    # Debe contener "municipio de Colima"
    assert "colima" in snippet.lower()
    assert "municipio" in snippet.lower()
