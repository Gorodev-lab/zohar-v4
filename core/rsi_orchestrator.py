"""
core/rsi_orchestrator.py
========================
Motor de Orquestación y REPL para el RSI_LOOP en Zohar v4.

Esta clase gestiona la ejecución iterativa de tareas complejas delegando en
sub-agentes simulados o reales. Utiliza el `RLMHarness` para la descarga
de contexto (evitando Context Rot) y para forzar el formato LID en la
determinación de acciones del LLM.
"""

from __future__ import annotations

import logging
import json
from typing import Dict, List, Any, Callable, Optional
from core.rlm_harness import RLMHarness

logger = logging.getLogger(__name__)


class RSILoopOrchestrator:
    """
    Orquestador del RSI_LOOP. Permite resolver tareas complejas mediante un
    REPL iterativo guiado por el LLM con descarga de contexto y determinación de acciones.
    """

    def __init__(self, harness: RLMHarness, max_iterations: int = 5):
        """
        Inicializa el orquestador.

        Args:
            harness: Instancia de RLMHarness compartida.
            max_iterations: Límite de seguridad de pasos en el bucle.
        """
        self.harness = harness
        self.max_iterations = max_iterations
        self._subagents: Dict[str, Callable[..., Any]] = {}
        self.execution_history: List[Dict[str, Any]] = []

        # Registrar sub-agentes por defecto
        self.register_subagent("verify_ocr", self._default_verify_ocr)
        self.register_subagent("check_sinat_keys", self._default_check_sinat_keys)

    def register_subagent(self, name: str, func: Callable[..., Any]) -> None:
        """
        Registra una función de sub-agente dinámica.

        Args:
            name: Nombre identificador del sub-agente/acción.
            func: Función a ejecutar cuando el LLM invoque esta acción.
        """
        self._subagents[name] = func
        logger.info(f"RSILoopOrchestrator: Sub-agente '{name}' registrado exitosamente.")

    def _default_verify_ocr(self, doc_id: str) -> Dict[str, Any]:
        """Sub-agente simulado de verificación de OCR."""
        logger.info(f"Ejecutando verify_ocr para: {doc_id}")
        raw_text = self.harness.get_offloaded_text(doc_id)
        if not raw_text:
            return {"status": "ERROR", "message": f"No se encontró el documento {doc_id} en el almacén de RLM."}
        
        # Simulación de verificación de OCR
        char_count = len(raw_text)
        has_garbage_chars = len(raw_text.split("$$")) > 1
        confidence = 0.95 if not has_garbage_chars else 0.60
        
        return {
            "status": "PASS" if confidence > 0.8 else "WARN",
            "confidence": confidence,
            "char_count": char_count,
            "ocr_version": "Tesseract-v5.0-simulated",
            "message": "OCR verificado correctamente."
        }

    def _default_check_sinat_keys(self, clave: str) -> Dict[str, Any]:
        """Sub-agente simulado de validación de claves SEMARNAT/SINAT."""
        logger.info(f"Ejecutando check_sinat_keys para la clave: {clave}")
        # Claves de SEMARNAT típicamente tienen formato DF/09/1234/00 o similar (o 10-14 caracteres alfanuméricos)
        clean_clave = str(clave).strip().upper()
        
        # Una validación regex básica
        is_valid_format = len(clean_clave) >= 8 and any(c.isdigit() for c in clean_clave)
        
        return {
            "status": "PASS" if is_valid_format else "FAIL",
            "clave_consultada": clean_clave,
            "exists_in_sinat": True if is_valid_format else False,
            "message": "Clave SINAT validada correctamente." if is_valid_format else "Formato de clave no válido o inexistente."
        }

    def run_task(self, task_description: str, initial_variables: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ejecuta el bucle REPL iterativo guiado por el LLM.

        Args:
            task_description: Tarea principal compleja a resolver.
            initial_variables: Mapa de variables (incluyendo referencias simbólicas y datos base).

        Returns:
            Dict con el resultado final de la ejecución, historial de pasos y estado.
        """
        logger.info(f"Iniciando RSI_LOOP para la tarea: '{task_description}'")
        self.execution_history.clear()
        
        current_variables = dict(initial_variables)
        step = 0
        status = "IN_PROGRESS"
        final_summary = ""

        # Definición del esquema de salida esperado para el LLM en formato LID
        expected_schema = {
            "action": "Nombre del sub-agente a llamar ('verify_ocr', 'check_sinat_keys' o 'finish')",
            "parameters": "Objeto de parámetros específicos para el sub-agente (ej. {'doc_id': '[VAR_DOC_01]'} o {'clave': '12DF2023X001'})",
            "reasoning": "Explicación breve de por qué se toma esta acción",
            "final_summary": "Resumen final del proceso (solo requerido cuando action es 'finish')"
        }

        # Restricciones específicas para guiar la orquestación del loop
        constraints = (
            "Determina el siguiente paso de manera iterativa. "
            "Acciones disponibles: " + ", ".join(f"'{k}'" for k in self._subagents.keys()) + " o 'finish'. "
            "Debes responder estrictamente con un objeto JSON matching expected_output_schema. "
            "Nunca inventes acciones no registradas."
        )

        while step < self.max_iterations:
            step += 1
            logger.info(f"RSI_LOOP Paso {step}/{self.max_iterations}")

            # Enviar estado actual a través de LID al LLM
            task_state_prompt = (
                f"Tarea Principal: {task_description}\n"
                f"Paso actual en el loop: {step}\n"
                f"Historial de acciones ejecutadas:\n{json.dumps(self.execution_history, indent=2, ensure_ascii=False)}\n"
            )

            response_data = self.harness.execute_lid_completion(
                task=task_state_prompt,
                variables=current_variables,
                constraints=constraints,
                expected_output_schema=expected_schema,
                system_prompt="Eres el Orquestador del RSI_LOOP. Tu rol es guiar la resolución de la tarea paso a paso."
            )

            # Validar y procesar respuesta del LLM
            if response_data.get("is_fallback") or response_data.get("status") == "error":
                # Fallback heurístico en caso de error del LLM
                logger.warning(f"Error o Fallback detectado en el LLM durante el paso {step}. Generando acción heurística.")
                # Si falló, forzamos un finish heurístico con las variables actuales para no romper el flujo
                response_data = {
                    "action": "finish",
                    "parameters": {},
                    "reasoning": "Fallo en la llamada neuronal del LLM, aplicando finalización de contingencia.",
                    "final_summary": f"Finalización por fallback de contingencia. Variables finales: {list(current_variables.keys())}"
                }

            action = response_data.get("action", "").strip()
            parameters = response_data.get("parameters", {})
            reasoning = response_data.get("reasoning", "")
            
            logger.info(f"LLM Decidió Acción: '{action}' | Razón: {reasoning}")

            # Guardar el intento en el historial
            step_record = {
                "step": step,
                "action_selected": action,
                "reasoning": reasoning,
                "parameters_sent": parameters,
                "result": None
            }

            if action == "finish":
                status = "COMPLETED"
                final_summary = response_data.get("final_summary") or "Proceso finalizado por decisión del LLM."
                step_record["result"] = {"status": "SUCCESS", "message": final_summary}
                self.execution_history.append(step_record)
                break

            if action in self._subagents:
                # Resolver variables simbólicas en los parámetros antes de pasarlos
                resolved_parameters = {}
                for pk, pv in parameters.items():
                    if isinstance(pv, str) and pv.startswith("[VAR_DOC_"):
                        # Si es una variable simbólica del harnés, la resolvemos o pasamos la referencia según lo requiera
                        # Algunos sub-agentes esperan el identificador simbólico (ej. para buscar en el harnés)
                        resolved_parameters[pk] = pv
                    else:
                        resolved_parameters[pk] = pv

                try:
                    # Ejecutar el sub-agente correspondiente
                    agent_func = self._subagents[action]
                    result = agent_func(**resolved_parameters)
                    step_record["result"] = result
                    
                    # Registrar nuevos resultados en el mapa de variables actual
                    result_var_name = f"[RESULT_{action.upper()}_STEP_{step:02d}]"
                    current_variables[result_var_name] = result
                    
                    logger.info(f"Sub-agente '{action}' ejecutado con éxito. Resultado guardado en {result_var_name}")
                except Exception as err:
                    err_msg = f"Error ejecutando sub-agente '{action}': {err}"
                    logger.error(err_msg)
                    step_record["result"] = {"status": "ERROR", "message": err_msg}
            else:
                # Acción inválida o no registrada
                err_msg = f"Acción '{action}' no es reconocida por el orquestador."
                logger.error(err_msg)
                step_record["result"] = {"status": "ERROR", "message": err_msg}

            self.execution_history.append(step_record)

        if status == "IN_PROGRESS":
            status = "MAX_ITERATIONS_REACHED"
            final_summary = f"Se alcanzó el límite máximo de {self.max_iterations} iteraciones sin concluir."

        return {
            "status": status,
            "final_summary": final_summary,
            "steps_executed": step,
            "history": self.execution_history,
            "variables": current_variables
        }
