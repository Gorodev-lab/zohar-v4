import pytest
from auto_improver import estimate_tokens, sanitize_pytest_output

def test_estimate_tokens():
    # 1 token ≈ 4 caracteres
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcdefgh") == 2
    assert estimate_tokens("") == 0

def test_sanitize_pytest_output_short():
    short_log = "============================= test session starts ==============================\n1 passed in 0.01s"
    # Límite amplio de 1000 tokens (~4000 caracteres), debe pasar intacto
    assert sanitize_pytest_output(short_log, max_tokens=1000) == short_log

def test_sanitize_pytest_output_long_structured():
    # Log simulado muy largo con warnings y fallos
    long_log = (
        "============================= test session starts ==============================\n"
        "platform linux -- Python 3.10.0\n"
        "UserWarning: Ignore this warning\n"
        "DeprecationWarning: Ignore this deprecated warning\n"
        "=================================== FAILURES ===================================\n"
        "___________________________ test_something ___________________________\n"
        "def test_something():\n"
        ">       assert False\n"
        "E       AssertionError: assert False\n"
        "tests/test_foo.py:12: AssertionError\n"
        "=========================== short test summary info ============================\n"
        "FAILED tests/test_foo.py::test_something - AssertionError\n"
        "========================= 1 failed, 1 passed in 0.5s =========================="
    )
    
    # Con 200 tokens (~800 caracteres), cabe completo y no se trunca, pero filtra warnings/platform
    sanitized = sanitize_pytest_output(long_log, max_tokens=200)
    
    # Debería contener las secciones clave
    assert "FAILURES" in sanitized
    assert "short test summary info" in sanitized
    assert "AssertionError" in sanitized
    # Debería omitir la cabecera inicial de pytest (warnings, platform, etc.)
    assert "platform linux" not in sanitized
    assert "UserWarning" not in sanitized


def test_sanitize_pytest_output_long_structured_truncated():
    # Mismo log largo
    long_log = (
        "============================= test session starts ==============================\n"
        "platform linux -- Python 3.10.0\n"
        "UserWarning: Ignore this warning\n"
        "DeprecationWarning: Ignore this deprecated warning\n"
        "=================================== FAILURES ===================================\n"
        "___________________________ test_something ___________________________\n"
        "def test_something():\n"
        ">       assert False\n"
        "E       AssertionError: assert False\n"
        "tests/test_foo.py:12: AssertionError\n"
        "=========================== short test summary info ============================\n"
        "FAILED tests/test_foo.py::test_something - AssertionError\n"
        "========================= 1 failed, 1 passed in 0.5s =========================="
    )
    
    # Si limitamos a 80 tokens (~320 caracteres), debería ocurrir el truncado de mitades
    sanitized = sanitize_pytest_output(long_log, max_tokens=80)
    
    assert len(sanitized) <= 320
    assert "...[Trazas intermedias omitidas por límite de tokens]..." in sanitized

def test_sanitize_pytest_output_fallback():
    # Log sin separadores clásicos pero muy largo
    unstructured_log = "Línea de log irrelevante número {}\n" * 150
    # Esto genera alrededor de 5000 caracteres. Si limitamos a 100 tokens (~400 caracteres),
    # el fallback tomará las últimas líneas y las recortará en mitades.
    sanitized = sanitize_pytest_output(unstructured_log, max_tokens=100)
    
    assert len(sanitized) <= 400
    assert "...[Truncado]..." in sanitized or "...[Trazas intermedias omitidas por límite de tokens]..." in sanitized
