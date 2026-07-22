"""
tests/test_auto_improver_unit.py
================================
Pruebas unitarias para las funciones de estabilización del bucle auto-improver.
"""

import pytest
from pathlib import Path
from auto_improver import (
    validate_python_syntax_detailed,
    fix_llm_indentation,
    auto_fix_window_indentation,
    build_prompt,
)

def test_validate_python_syntax_detailed():
    # Código válido
    valid, err = validate_python_syntax_detailed("def foo():\n    pass")
    assert valid is True
    assert err is None

    # Código inválido
    valid, err = validate_python_syntax_detailed("def foo():\n  pass\n    print('error')")
    assert valid is False
    assert err is not None
    assert isinstance(err, SyntaxError)


def test_fix_llm_indentation():
    raw_code = "  def foo():\n    pass"
    fixed = fix_llm_indentation(raw_code, 4)
    # textwrap.dedent produce: "def foo():\n  pass"
    # Con indentación base 4:
    # "    def foo():\n      pass\n"
    assert "    def foo():" in fixed
    assert "      pass" in fixed


def test_auto_fix_window_indentation_surgical():
    full_source = (
        "def main_func():\n"
        "    print('start')\n"
        "    # TARGET_START\n"
        "    if True:\n"
        "        pass\n"
        "    # TARGET_END\n"
        "    print('end')\n"
    )
    # Simular una ventana con indentación rota (ej: 6 espacios en lugar de 8)
    broken_window = (
        "    if True:\n"
        "      print('broken')\n"
    )
    
    head = "def main_func():\n    print('start')\n"
    tail = "    print('end')\n"

    # La auto-reparación quirúrgica debe detectar el error sintáctico
    # e intentar alinear a los candidatos apropiados de 4 en 4
    repaired = auto_fix_window_indentation(
        broken_window,
        base_indent=4,
        head=head,
        tail=tail,
        full_source=full_source,
        func_name="main_func"
    )
    
    # Debería compilar exitosamente tras la auto-reparación quirúrgica
    repaired_source = head + repaired + tail
    valid, err = validate_python_syntax_detailed(repaired_source)
    assert valid is True, f"Fallo al reparar: {err}"
