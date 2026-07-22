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


def test_redis_session_uuid_prefix():
    """Verifica que el prefijo se construya dinámicamente usando el session_uuid si no se pasa prefix."""
    harness = RLMHarness(use_redis_if_available=False)
    assert harness.session_uuid is not None
    assert harness.storage_prefix == f"zohar:rlm:{harness.session_uuid}:"


def test_redis_ttl_enforcement():
    """Verifica que se le asigne la expiración (TTL) a las claves guardadas en Redis."""
    # Mockear el cliente de Redis y la comprobación de su disponibilidad
    with patch("redis.Redis") as mock_redis_class:
        mock_client = MagicMock()
        mock_redis_class.from_url.return_value = mock_client
        
        # Habilitar redis
        with patch("core.rlm_harness.REDIS_AVAILABLE", True):
            # 1. Usando el TTL por defecto
            harness = RLMHarness(redis_url="redis://localhost:6379/0", default_ttl=600)
            harness.offload_text("Texto largo de prueba", var_name="[VAR_TEST_01]")
            
            expected_key = f"{harness.storage_prefix}[VAR_TEST_01]"
            mock_client.set.assert_called_with(expected_key, "Texto largo de prueba", ex=600)

            # 2. Especificando un TTL personalizado
            harness.offload_text("Otro texto largo", var_name="[VAR_TEST_02]", ttl=120)
            expected_key_2 = f"{harness.storage_prefix}[VAR_TEST_02]"
            mock_client.set.assert_called_with(expected_key_2, "Otro texto largo", ex=120)

            # 3. Desactivando TTL (ttl=0)
            harness.offload_text("Texto persistente", var_name="[VAR_TEST_03]", ttl=0)
            expected_key_3 = f"{harness.storage_prefix}[VAR_TEST_03]"
            mock_client.set.assert_called_with(expected_key_3, "Texto persistente")


def test_dynamic_parameter_hydration():
    """Verifica que el orquestador resuelva e hidrate parámetros según la signatura del sub-agente."""
    harness = RLMHarness(use_redis_if_available=False)
    orchestrator = RSILoopOrchestrator(harness)

    # Variables de prueba
    text1 = "Contenido confidencial del estudio ambiental"
    var1 = harness.offload_text(text1)
    
    # Sub-agente que espera un texto hidratado (no está en blacklist de referencias)
    def my_subagent_with_text(text_content: str, harness_arg=None):
        return {
            "received_text": text_content,
            "has_harness": harness_arg is not None
        }

    # Registrar el sub-agente
    orchestrator.register_subagent("custom_sub", my_subagent_with_text)

    # Parámetros simulados enviados por el LLM
    raw_params = {
        "text_content": var1,
        "harness_arg": "harness"
    }
    
    # Probar inyección de harness por nombre exacto en signatura
    def my_subagent_with_harness(harness: RLMHarness, doc_id: str):
        return {
            "doc_id_received": doc_id,
            "has_harness_obj": harness is not None
        }
    orchestrator.register_subagent("harness_sub", my_subagent_with_harness)

    # 1. Resolver para my_subagent_with_text
    resolved_1 = orchestrator._resolve_and_hydrate_parameters(my_subagent_with_text, raw_params)
    assert resolved_1["text_content"] == text1  # Debería estar hidratado
    
    # 2. Resolver para my_subagent_with_harness
    raw_params_2 = {
        "doc_id": var1
    }
    resolved_2 = orchestrator._resolve_and_hydrate_parameters(my_subagent_with_harness, raw_params_2)
    assert resolved_2["doc_id"] == var1  # No debería estar hidratado porque doc_id está en ref_params
    assert resolved_2["harness"] is harness  # Debería inyectar la instancia de harness


def test_active_cleanup_of_unaccessed_keys():
    """Verifica que las variables no accedidas sean eliminadas de Redis y memoria local."""
    harness = RLMHarness(use_redis_if_available=False)
    
    # Crear variables
    var_accessed = harness.offload_text("Texto que sí será leído")
    var_dead = harness.offload_text("Texto huérfano que nadie leerá")
    
    # Acceder a una
    text = harness.get_offloaded_text(var_accessed)
    assert text == "Texto que sí será leído"
    
    # Limpieza
    cleaned = harness.cleanup_unaccessed_keys()
    
    assert var_dead in cleaned
    assert var_accessed not in cleaned
    
    # Verificar que el muerto ya no existe
    assert harness.get_offloaded_text(var_dead) is None
    # Verificar que el accedido sigue existiendo
    assert harness.get_offloaded_text(var_accessed) == "Texto que sí será leído"


def test_active_cleanup_redis():
    """Verifica la autolimpieza de variables muertas en Redis."""
    with patch("redis.Redis") as mock_redis_class:
        mock_client = MagicMock()
        mock_redis_class.from_url.return_value = mock_client
        
        with patch("core.rlm_harness.REDIS_AVAILABLE", True):
            harness = RLMHarness(redis_url="redis://localhost:6379/0")
            
            var_accessed = harness.offload_text("Accedido", var_name="[VAR_ACC]")
            var_dead = harness.offload_text("Muerto", var_name="[VAR_DEAD]")
            
            # Simulamos que get de Redis funciona
            mock_client.get.return_value = "Accedido"
            
            # Accedemos a uno
            harness.get_offloaded_text(var_accessed)
            
            # Limpiar
            cleaned = harness.cleanup_unaccessed_keys()
            
            assert "[VAR_DEAD]" in cleaned
            assert "[VAR_ACC]" not in cleaned
            
            # Verificar llamada a delete en redis
            expected_deleted_key = f"{harness.storage_prefix}[VAR_DEAD]"
            mock_client.delete.assert_any_call(expected_deleted_key)


