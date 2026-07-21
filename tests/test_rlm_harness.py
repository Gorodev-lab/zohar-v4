"""
tests/test_rlm_harness.py
=========================
Pruebas unitarias para RLMHarness y RSILoopOrchestrator.
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from core.rlm_harness import RLMHarness
from core.rsi_orchestrator import RSILoopOrchestrator


def test_context_offloading_and_rehydration():
    """Verifica que la Descarga de Contexto reemplaza correctamente texto y lo rehidrata."""
    harness = RLMHarness(use_redis_if_available=False)

    large_text_1 = "Este es un fragmento de texto muy largo que debe ser descargado del contexto para evitar la putrefacción."
    large_text_2 = "Otro fragmento masivo que simula datos de SEMARNAT de tipo resolutivo/estudio ambiental."

    # Descarga explícita
    tag1 = harness.offload_text(large_text_1)
    tag2 = harness.offload_text(large_text_2)

    assert tag1.startswith("[VAR_DOC_")
    assert tag2.startswith("[VAR_DOC_")
    assert tag1 != tag2

    # Recuperación
    assert harness.get_offloaded_text(tag1) == large_text_1
    assert harness.get_offloaded_text(tag2) == large_text_2

    # Rehidratación
    response_template = f"Se ha analizado el documento {tag1} y se complementó con {tag2}."
    rehydrated = harness.rehydrate_text(response_template)

    assert large_text_1 in rehydrated
    assert large_text_2 in rehydrated
    assert tag1 not in rehydrated
    assert tag2 not in rehydrated


def test_offload_large_blocks_by_length():
    """Verifica la descarga automática de bloques por longitud de caracteres."""
    harness = RLMHarness(use_redis_if_available=False)

    prompt = (
        "Texto corto.\n\n"
        "Este es un párrafo sustancialmente largo que supera el límite de longitud mínima "
        "establecido para la descarga de contexto del RLMHarness (más de 200 caracteres para pruebas).\n\n"
        "Otro texto breve."
    )

    abstracted_prompt, vars_created = harness.offload_large_blocks(prompt, min_char_len=100)

    assert len(vars_created) == 1
    assert vars_created[0] in abstracted_prompt
    assert "sustancialmente largo" not in abstracted_prompt
    assert "Texto corto." in abstracted_prompt
    assert "Otro texto breve." in abstracted_prompt


def test_lid_prompt_enforcement():
    """Verifica que la estructura JSON LID se genera con el formato correcto y sin errores de sintaxis."""
    harness = RLMHarness(use_redis_if_available=False)

    task = "Extraer los campos principales de SEMARNAT."
    variables = ["[VAR_DOC_01]"]
    constraints = "Debe ser JSON estricto."
    schema = {"promovente": "string"}

    lid_prompt = harness.format_lid_prompt(
        task=task,
        variables=variables,
        constraints=constraints,
        expected_output_schema=schema
    )

    # Validar parseo JSON
    parsed = json.loads(lid_prompt)
    assert parsed["task"] == task
    assert parsed["variables"] == variables
    assert parsed["constraints"] == constraints
    assert parsed["expected_output_schema"] == schema


def test_orchestrator_subagent_dispatch():
    """Simula una serie de decisiones del RSILoopOrchestrator llamando a sub-agentes."""
    harness = RLMHarness(use_redis_if_available=False)
    orchestrator = RSILoopOrchestrator(harness, max_iterations=3)

    # Simulamos datos de entrada
    doc_id = harness.offload_text("Texto del estudio de SEMARNAT para validar.")
    initial_vars = {"[VAR_DOC_01]": doc_id}

    # Creamos respuestas simuladas del LLM que simulan el bucle
    # Paso 1: Llamar a verify_ocr
    # Paso 2: Llamar a check_sinat_keys
    # Paso 3: Terminar
    mock_responses = [
        {
            "action": "verify_ocr",
            "parameters": {"doc_id": doc_id},
            "reasoning": "Se requiere verificar la calidad del OCR en el documento."
        },
        {
            "action": "check_sinat_keys",
            "parameters": {"clave": "12DF2026A001"},
            "reasoning": "Verificar la existencia de la clave en SINAT."
        },
        {
            "action": "finish",
            "parameters": {},
            "reasoning": "Se han completado todas las verificaciones necesarias.",
            "final_summary": "Verificación finalizada con éxito."
        }
    ]

    with patch("core.rlm_harness.generate_completion") as mock_gen:
        mock_gen.side_effect = mock_responses

        result = orchestrator.run_task(
            task_description="Realizar auditoría completa sobre el documento [VAR_DOC_01].",
            initial_variables=initial_vars
        )

        assert result["status"] == "COMPLETED"
        assert result["steps_executed"] == 3
        assert len(result["history"]) == 3

        # Verificar el orden de las acciones
        assert result["history"][0]["action_selected"] == "verify_ocr"
        assert result["history"][1]["action_selected"] == "check_sinat_keys"
        assert result["history"][2]["action_selected"] == "finish"

        # Verificar que se guardaron los resultados en las variables
        assert "[RESULT_VERIFY_OCR_STEP_01]" in result["variables"]
        assert "[RESULT_CHECK_SINAT_KEYS_STEP_02]" in result["variables"]

        # Verificar contenido de los resultados
        assert result["variables"]["[RESULT_VERIFY_OCR_STEP_01]"]["status"] == "PASS"
        assert result["variables"]["[RESULT_CHECK_SINAT_KEYS_STEP_02]"]["status"] == "PASS"
        assert result["final_summary"] == "Verificación finalizada con éxito."
