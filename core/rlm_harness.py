"""
core/rlm_harness.py
===================
Módulo del Arnés del Modelo de Lenguaje Recursivo (RLM Harness) para Zohar v4.

Responsabilidades principales:
1. Descarga de Contexto (Context Offloading):
   - Almacena el texto crudo de documentos y chunks en memoria (con fallback a Redis si está disponible).
   - Reemplaza texto masivo por variables simbólicas (ej. [VAR_DOC_01]) en los prompts enviados al LLM.
   - Permite rehidratar respuestas del LLM sustituyendo variables por el texto original.
2. Enforcement de LID (Locally In-Distribution):
   - Formatea estrictamente las peticiones al LLM en una estructura JSON canónica y predecible.
   - Evita la "putrefacción del contexto" (Context Rot) reduciendo la variabilidad sintáctica del prompt.
"""

from __future__ import annotations

import re
import json
import os
import logging
import uuid
from typing import Dict, List, Any, Optional, Union, Tuple
from core.llm_client import generate_completion

logger = logging.getLogger(__name__)

# Intentar importar redis opcionalmente
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


class RLMHarness:
    """
    Arnés inteligente RLM (Recursive Language Model) para abstracción de contexto y control LID.
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        storage_prefix: Optional[str] = None,
        use_redis_if_available: bool = True,
        default_ttl: int = 3600
    ):
        """
        Inicializa el Arnés RLM.

        Args:
            redis_url: URL de conexión a Redis (ej. redis://localhost:6379/0).
            storage_prefix: Prefijo para claves almacenadas en Redis. Si es None, se autogenera con uuid de sesión.
            use_redis_if_available: Si es True y Redis está configurado/instalado, se usa como backend.
            default_ttl: Tiempo de vida por defecto en segundos para las variables en Redis.
        """
        self.session_uuid = str(uuid.uuid4())
        self.default_ttl = default_ttl
        
        if storage_prefix is None:
            self.storage_prefix = f"zohar:rlm:{self.session_uuid}:"
        else:
            self.storage_prefix = storage_prefix
            
        self._memory_store: Dict[str, str] = {}
        self._created_keys: set[str] = set()
        self._accessed_keys: set[str] = set()
        self._redis_client = None
        self._doc_counter: int = 0

        # Configuración de Redis
        target_redis_url = redis_url or os.getenv("REDIS_URL")
        if use_redis_if_available and REDIS_AVAILABLE and target_redis_url:
            try:
                client = redis.Redis.from_url(target_redis_url, decode_responses=True)
                client.ping()
                self._redis_client = client
                logger.info(f"RLMHarness: Conectado exitosamente a Redis ({target_redis_url}) con prefijo {self.storage_prefix}")
            except Exception as e:
                logger.warning(f"RLMHarness: No se pudo conectar a Redis ({e}). Usando memoria local.")
                self._redis_client = None
        else:
            logger.info("RLMHarness: Usando almacenamiento en memoria local.")

    def _generate_var_name(self) -> str:
        """Genera un nombre simbólico secuencial para documentos offloaded."""
        self._doc_counter += 1
        return f"[VAR_DOC_{self._doc_counter:02d}]"

    def offload_text(self, text: str, var_name: Optional[str] = None, ttl: Optional[int] = None) -> str:
        """
        Almacena texto crudo y retorna su referencia simbólica.

        Args:
            text: Contenido de texto largo a descargar del contexto.
            var_name: Etiqueta simbólica personalizada (ej. '[VAR_DOC_01]'). Si es None, se auto-genera.
            ttl: Tiempo de expiración de la clave en segundos. Si es None, usa default_ttl.

        Returns:
            Nombre de la variable simbólica asignada.
        """
        if not text:
            raise ValueError("El texto a descargar no puede estar vacío.")

        tag = var_name or self._generate_var_name()
        # Asegurar formato estándar [VAR_...]
        if not tag.startswith("[") or not tag.endswith("]"):
            tag = f"[{tag}]"

        clean_tag = tag
        effective_ttl = ttl if ttl is not None else self.default_ttl

        if self._redis_client:
            try:
                redis_key = f"{self.storage_prefix}{clean_tag}"
                if effective_ttl > 0:
                    self._redis_client.set(redis_key, text, ex=effective_ttl)
                else:
                    self._redis_client.set(redis_key, text)
            except Exception as e:
                logger.error(f"Error guardando en Redis ({e}), cayendo a almacenamiento en memoria.")
                self._memory_store[clean_tag] = text
        else:
            self._memory_store[clean_tag] = text

        self._created_keys.add(clean_tag)
        logger.debug(f"Offloaded text ({len(text)} chars) -> {clean_tag} (TTL={effective_ttl})")
        return clean_tag

    def get_offloaded_text(self, var_name: str) -> Optional[str]:
        """
        Obtiene el texto crudo almacenado correspondiente a una variable simbólica.

        Args:
            var_name: Nombre de la variable (ej. '[VAR_DOC_01]' o 'VAR_DOC_01').

        Returns:
            El texto crudo original o None si no existe.
        """
        tag = var_name if var_name.startswith("[") and var_name.endswith("]") else f"[{var_name}]"
        val = None

        if self._redis_client:
            try:
                redis_key = f"{self.storage_prefix}{tag}"
                redis_val = self._redis_client.get(redis_key)
                if redis_val is not None:
                    val = str(redis_val)
            except Exception as e:
                logger.error(f"Error leyendo de Redis ({e}). Consultando almacenamiento en memoria.")

        if val is None:
            val = self._memory_store.get(tag)

        if val is not None:
            self._accessed_keys.add(tag)

        return val

    def cleanup_unaccessed_keys(self) -> List[str]:
        """
        Identifica variables que fueron creadas pero nunca accedidas durante la sesión.
        Las elimina de Redis y de la memoria local.

        Returns:
            Lista de etiquetas simbólicas eliminadas.
        """
        dead_keys = self._created_keys - self._accessed_keys
        deleted = []

        for tag in dead_keys:
            # Eliminar de la memoria local
            if tag in self._memory_store:
                del self._memory_store[tag]
                deleted.append(tag)

            # Eliminar de Redis
            if self._redis_client:
                try:
                    redis_key = f"{self.storage_prefix}{tag}"
                    self._redis_client.delete(redis_key)
                    if tag not in deleted:
                        deleted.append(tag)
                except Exception as e:
                    logger.error(f"RLMHarness: Error al eliminar variable muerta {tag} de Redis: {e}")

        logger.info(f"RLMHarness: Limpieza activa completada. Se eliminaron {len(deleted)} variables muertas: {deleted}")
        # Remover las claves eliminadas de created_keys
        self._created_keys.difference_update(deleted)
        return deleted

    def offload_large_blocks(
        self,
        prompt: str,
        min_char_len: int = 200,
        custom_block_pattern: Optional[str] = None
    ) -> Tuple[str, List[str]]:
        """
        Analiza un prompt, extrae bloques de texto masivos (mayores a min_char_len)
        o delimitados, los almacena en el harnés y los reemplaza por etiquetas simbólicas.

        Args:
            prompt: Prompt o texto original con posibles bloques masivos.
            min_char_len: Umbral de longitud de caracteres para considerar descarga.
            custom_block_pattern: Regex opcional para identificar bloques específicos.

        Returns:
            Tuple de (prompt_abstraído, lista_de_variables_generadas)
        """
        created_vars: List[str] = []

        if custom_block_pattern:
            matches = list(re.finditer(custom_block_pattern, prompt, re.DOTALL))
            abstracted_prompt = prompt
            for m in reversed(matches):
                block = m.group(0)
                var_tag = self.offload_text(block)
                created_vars.append(var_tag)
                abstracted_prompt = (
                    abstracted_prompt[:m.start()] + f" {var_tag} " + abstracted_prompt[m.end():]
                )
            return abstracted_prompt.strip(), created_vars

        # Estrategia por párrafos/bloques separados por salto de línea doble
        paragraphs = prompt.split("\n\n")
        new_paragraphs = []

        for p in paragraphs:
            if len(p.strip()) >= min_char_len:
                var_tag = self.offload_text(p.strip())
                created_vars.append(var_tag)
                new_paragraphs.append(var_tag)
            else:
                new_paragraphs.append(p)

        return "\n\n".join(new_paragraphs), created_vars

    def rehydrate_text(self, text: str) -> str:
        """
        Sustituye todas las etiquetas simbólicas [VAR_DOC_XX] presentes en un texto
        por su contenido crudo original almacenado.

        Args:
            text: Texto con variables simbólicas.

        Returns:
            Texto rehidratado con contenido original.
        """
        pattern = r"\[VAR_DOC_\d+\]"
        matches = set(re.findall(pattern, text))

        rehydrated = text
        for tag in matches:
            raw = self.get_offloaded_text(tag)
            if raw is not None:
                rehydrated = rehydrated.replace(tag, raw)

        return rehydrated

    def format_lid_prompt(
        self,
        task: str,
        variables: Union[List[str], Dict[str, Any]],
        constraints: Optional[str] = None,
        expected_output_schema: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Enforcement de LID (Locally In-Distribution):
        Formatea cualquier petición al modelo en una estructura JSON canónica y predecible.

        Template Estándar JSON:
        {
          "task": "...",
          "variables": [...],
          "constraints": "...",
          "expected_output_schema": {...}
        }

        Args:
            task: Descripción clara de la tarea a realizar por el LLM.
            variables: Lista de etiquetas simbólicas o mapa de variables contextualmente abstraídas.
            constraints: Restricciones de ejecución o formato.
            expected_output_schema: Diccionario describiendo los campos esperados en la respuesta.

        Returns:
            String JSON formateado estrictamente para el prompt.
        """
        default_constraints = (
            "Responder ÚNICAMENTE en JSON válido sin envoltorios ni texto adicional. "
            "Referirse a los documentos únicamente mediante sus variables simbólicas [VAR_DOC_XX]."
        )

        lid_payload = {
            "task": task.strip(),
            "variables": variables,
            "constraints": (constraints or default_constraints).strip(),
            "expected_output_schema": expected_output_schema or {}
        }

        try:
            return json.dumps(lid_payload, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error serializando estructura LID: {e}")
            raise ValueError(f"Fallo en formateador LID: {e}")

    def execute_lid_completion(
        self,
        task: str,
        variables: Union[List[str], Dict[str, Any]],
        constraints: Optional[str] = None,
        expected_output_schema: Optional[Dict[str, Any]] = None,
        system_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Construye la petición LID y la envía al cliente LLM unificado (`generate_completion`).

        Returns:
            Dict de respuesta devuelto por el LLM o estructura de fallback.
        """
        lid_prompt_str = self.format_lid_prompt(
            task=task,
            variables=variables,
            constraints=constraints,
            expected_output_schema=expected_output_schema
        )

        effective_system_prompt = (
            system_prompt or
            "Eres un agente neuronal especializado RLM en Zohar v4. "
            "Tu entrada es siempre una estructura JSON LID. "
            "Debes procesar referencias simbólicas sin solicitar texto crudo a menos que sea estrictamente necesario."
        )

        logger.info(f"RLMHarness: Ejecutando completación LID para tarea: '{task[:60]}...'")

        try:
            response = generate_completion(
                prompt=lid_prompt_str,
                system_prompt=effective_system_prompt,
                response_json=True
            )
            return response
        except Exception as e:
            logger.error(f"RLMHarness: Error en completación LLM: {e}")
            return {
                "status": "error",
                "error": str(e),
                "is_fallback": True
            }

    def clear_store(self) -> None:
        """Limpia el almacenamiento en memoria y en Redis de las claves administradas."""
        self._memory_store.clear()
        self._created_keys.clear()
        self._accessed_keys.clear()
        self._doc_counter = 0

        if self._redis_client:
            try:
                keys = self._redis_client.keys(f"{self.storage_prefix}*")
                if keys:
                    self._redis_client.delete(*keys)
                logger.info("RLMHarness: Almacenamiento Redis limpiado.")
            except Exception as e:
                logger.error(f"Error al limpiar claves en Redis: {e}")
