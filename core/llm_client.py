"""
core/llm_client.py
Capa de abstracción unificada para LLMs locales (llama-server, Ollama) y remotos (Gemini API).
Soporta detección automática y orden de prioridad.
"""

import os
import json
import logging
import httpx
from typing import Optional, Any

logger = logging.getLogger(__name__)

def detect_active_backend() -> tuple[str, str]:
    """
    Detecta automáticamente qué proveedor de LLM está activo y disponible.
    Prioridad:
    1. llama-server (puerto 8083 por defecto)
    2. Ollama (puerto 11434 por defecto)
    3. Gemini API (si hay GEMINI_API_KEY)
    4. Heurística (sin LLM)

    Returns:
        tuple[provider, model_name]
    """
    # 1. Verificar llama-server
    local_url = os.environ.get("LOCAL_LLM_URL", "http://localhost:8083")
    try:
        # llama-server tiene un endpoint /health
        r = httpx.get(f"{local_url}/health", timeout=1.0)
        if r.status_code == 200:
            model_name = os.environ.get("LOCAL_LLM_MODEL", "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf")
            return "llama-server", model_name
    except Exception:
        pass

    # 2. Verificar Ollama
    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    try:
        r = httpx.get(f"{ollama_url}/api/tags", timeout=1.0)
        if r.status_code == 200:
            models_data = r.json()
            models_list = [m["name"] for m in models_data.get("models", [])]
            if models_list:
                env_model = os.environ.get("LOCAL_LLM_MODEL")
                if env_model and env_model in models_list:
                    return "ollama", env_model
                # Buscar gemma4
                for target in ["gemma4:e4b", "gemma:2b", "gemma:latest"]:
                    if target in models_list:
                        return "ollama", target
                return "ollama", models_list[0]
    except Exception:
        pass

    # 3. Verificar Gemini API
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini", "gemini-2.0-flash"

    return "heuristic", "fallback_heuristic"


def generate_completion(
    prompt: str,
    system_prompt: Optional[str] = None,
    response_json: bool = True,
    max_chars: Optional[int] = None
) -> dict:
    """
    Genera una completación de chat con el backend de mayor prioridad activo.
    Retorna un diccionario estructurado similar a la respuesta de inferencia esperada.
    """
    provider, model_name = detect_active_backend()
    logger.info(f"Usando LLM provider: {provider} (modelo: {model_name})")

    # Si se pide truncado específico
    if max_chars and len(prompt) > max_chars:
        mid = max_chars // 2
        prompt = prompt[:mid] + "\n\n[...TEXTO TRUNCADO POR LIMITACIONES DEL CONTEXTO LOCAL...]\n\n" + prompt[-mid:]

    # 1. Lógica para llama-server (usando /completion crudo para evitar bugs de chat templates en llama.cpp)
    if provider == "llama-server":
        local_url = os.environ.get("LOCAL_LLM_URL", "http://localhost:8083")
        
        # Formatear prompt con tags oficiales de Gemma
        formatted_prompt = ""
        if system_prompt:
            formatted_prompt += f"<start_of_turn>user\n{system_prompt.strip()}\n\n{prompt.strip()}<end_of_turn>\n<start_of_turn>model\n"
        else:
            formatted_prompt += f"<start_of_turn>user\n{prompt.strip()}<end_of_turn>\n<start_of_turn>model\n"

        payload = {
            "prompt": formatted_prompt,
            "temperature": 0.1,
            "n_predict": 512,
            "stop": ["<end_of_turn>", "<eos>"],
        }
        
        try:
            r = httpx.post(f"{local_url.rstrip('/')}/completion", json=payload, timeout=300.0)
            if r.status_code == 200:
                res_data = r.json()
                content = res_data["content"].strip()
                
                # Intentar parsear si se espera JSON
                if response_json:
                    if content.startswith("```"):
                        lines = content.split("\n")
                        if lines[0].startswith("```"):
                            lines.pop(0)
                        if lines and lines[-1].startswith("```"):
                            lines.pop()
                        content = "\n".join(lines).strip()
                    try:
                        parsed = json.loads(content)
                        parsed.setdefault("meta", {})
                        parsed["meta"]["modelo"] = f"{provider}:{model_name}"
                        return parsed
                    except json.JSONDecodeError as je:
                        logger.error(f"Error decodificando JSON del modelo local: {je}. Contenido: {content}")
                        raise je
                return {"text": content, "meta": {"modelo": f"{provider}:{model_name}"}}
            else:
                logger.error(f"Error de llama-server: {r.status_code} - {r.text}")
                raise RuntimeError(f"Server status {r.status_code}")
        except Exception as exc:
            logger.warning(f"Fallo en llama-server: {exc}. Intentando fallback a Ollama...")
            provider = "ollama"

    # 1b. Lógica para Ollama (API OpenAI-compatible)
    if provider == "ollama":
        base_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url.rstrip('/')}/v1"

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.1,
        }
        if response_json:
            payload["response_format"] = {"type": "json_object"}

        try:
            r = httpx.post(f"{base_url}/chat/completions", json=payload, timeout=90.0)
            if r.status_code == 200:
                res_data = r.json()
                content = res_data["choices"][0]["message"]["content"].strip()
                
                # Intentar parsear si se espera JSON
                if response_json:
                    if content.startswith("```"):
                        lines = content.split("\n")
                        if lines[0].startswith("```"):
                            lines.pop(0)
                        if lines and lines[-1].startswith("```"):
                            lines.pop()
                        content = "\n".join(lines).strip()
                    try:
                        parsed = json.loads(content)
                        parsed.setdefault("meta", {})
                        parsed["meta"]["modelo"] = f"{provider}:{model_name}"
                        return parsed
                    except json.JSONDecodeError as je:
                        logger.error(f"Error decodificando JSON de Ollama: {je}. Contenido: {content}")
                        raise je
                return {"text": content, "meta": {"modelo": f"{provider}:{model_name}"}}
            else:
                logger.error(f"Error de Ollama: {r.status_code} - {r.text}")
                raise RuntimeError(f"Server status {r.status_code}")
        except Exception as exc:
            logger.warning(f"Fallo en Ollama: {exc}. Intentando fallback...")
            if os.environ.get("GEMINI_API_KEY"):
                provider = "gemini"
                model_name = "gemini-2.0-flash"
            else:
                provider = "heuristic"

    # 2. Lógica para Gemini API
    if provider == "gemini":
        try:
            from google import genai
            api_key = os.environ.get("GEMINI_API_KEY", "")
            client = genai.Client(api_key=api_key)
            
            # Re-ensamblar prompts
            full_prompt = prompt
            if system_prompt:
                full_prompt = f"{system_prompt}\n\nTEXTO A PROCESAR:\n{prompt}"

            response = client.models.generate_content(
                model=model_name,
                contents=[{"role": "user", "parts": [{"text": full_prompt}]}]
            )
            raw = response.text.strip()
            
            # Limpiar posible markdown json
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            if response_json:
                parsed = json.loads(raw)
                parsed.setdefault("meta", {})
                parsed["meta"]["modelo"] = "gemini-2.0-flash"
                return parsed
            return {"text": raw, "meta": {"modelo": "gemini-2.0-flash"}}
        except Exception as exc:
            logger.error(f"Fallo en Gemini API: {exc}")
            provider = "heuristic"

    # 3. Fallback Heurístico
    if provider == "heuristic" or provider == "fallback_heuristic":
        # Retornará dict compatible con el report format
        return {
            "is_fallback": True,
            "provider": "heuristic"
        }
