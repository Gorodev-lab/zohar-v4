"""
core/llm_client.py
Capa de abstracción unificada para LLMs locales (llama-server, Ollama) y remotos (Gemini API).
Soporta detección automática y orden de prioridad.
"""

import os
import json
import re
import logging
import httpx
import threading
from pathlib import Path
from typing import Optional, Any

logger = logging.getLogger(__name__)

# Lock para sincronización de acceso al servidor local
_llama_server_lock = threading.Lock()

# Lock y variables locales para estadísticas de latencia
_stats_lock = threading.Lock()
_total_tokens = 0
_total_time_ms = 0.0
STATS_FILE = Path("/tmp/zohar_llm_latency.json")

def update_latency_stats(tokens: int, time_ms: float):
    global _total_tokens, _total_time_ms
    with _stats_lock:
        _total_tokens += tokens
        _total_time_ms += time_ms
        if _total_tokens > 20000:
            _total_tokens = int(_total_tokens * 0.2)
            _total_time_ms = _total_time_ms * 0.2
        
        try:
            STATS_FILE.write_text(json.dumps({
                "total_tokens": _total_tokens,
                "total_time_ms": _total_time_ms
            }))
        except Exception:
            pass

def get_avg_latency_per_token() -> float:
    try:
        if STATS_FILE.exists():
            data = json.loads(STATS_FILE.read_text())
            tokens = int(data.get("total_tokens", 0))
            time_ms = float(data.get("total_time_ms", 0.0))
            if tokens > 0:
                return time_ms / tokens
    except Exception:
        pass

    with _stats_lock:
        if _total_tokens == 0:
            return 0.0
        return _total_time_ms / _total_tokens

def detect_active_backend() -> tuple[str, str]:
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

    # 3. Verificar Mistral API
    if os.environ.get("MISTRAL_API_KEY"):
        return "mistral", os.environ.get("MISTRAL_MODEL", "mistral-small-latest")

    # 4. Verificar Gemini API
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini", "gemini-2.0-flash"

    return "heuristic", "fallback_heuristic"


def generate_completion(
    prompt: str,
    system_prompt: Optional[str] = None,
    response_json: bool = True,
    max_chars: Optional[int] = None,
    n_predict: Optional[int] = None
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
        # Bloquear acceso para evitar concurrencia en el servidor local de hilos reducidos
        with _llama_server_lock:
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
                "n_predict": n_predict or 512,
                "stop": ["<end_of_turn>", "<eos>"],
            }
            
            try:
                r = httpx.post(f"{local_url.rstrip('/')}/completion", json=payload, timeout=300.0)
                if r.status_code == 200:
                    res_data = r.json()
                    content = res_data["content"].strip()
                    
                    # Track de métricas de latencia de tokens generados
                    timings = res_data.get("timings", {})
                    pred_n = timings.get("predicted_n", 0)
                    pred_ms = timings.get("predicted_ms", 0.0)
                    if pred_n > 0:
                        update_latency_stats(pred_n, pred_ms)
                    
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
                            match = re.search(r"\{.*\}", content, re.DOTALL)
                            if match:
                                content = match.group(0)
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
                logger.warning(f"Fallo en llama-server: {exc}. Intentando fallback...")
                if os.environ.get("MISTRAL_API_KEY"):
                    provider = "mistral"
                    model_name = os.environ.get("MISTRAL_MODEL", "mistral-small-latest")
                elif os.environ.get("GEMINI_API_KEY"):
                    provider = "gemini"
                    model_name = "gemini-2.0-flash"
                else:
                    provider = "heuristic"

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
                        match = re.search(r"\{.*\}", content, re.DOTALL)
                        if match:
                            content = match.group(0)
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
            if os.environ.get("MISTRAL_API_KEY"):
                provider = "mistral"
                model_name = os.environ.get("MISTRAL_MODEL", "mistral-small-latest")
            elif os.environ.get("GEMINI_API_KEY"):
                provider = "gemini"
                model_name = "gemini-2.0-flash"
            else:
                provider = "heuristic"

    # 1c. Lógica para Mistral API (api.mistral.ai)
    if provider == "mistral":
        api_key = os.environ.get("MISTRAL_API_KEY", "")
        m_model = model_name if (model_name and not model_name.endswith(".gguf")) else os.environ.get("MISTRAL_MODEL", "mistral-small-latest")
        if not api_key:
            logger.error("MISTRAL_API_KEY no configurada.")
            provider = "heuristic"
        else:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            payload = {
                "model": m_model,
                "messages": messages,
                "temperature": 0.1,
            }
            if response_json:
                payload["response_format"] = {"type": "json_object"}

            try:
                r = httpx.post("https://api.mistral.ai/v1/chat/completions", headers=headers, json=payload, timeout=60.0)
                if r.status_code == 200:
                    res_data = r.json()
                    content = res_data["choices"][0]["message"]["content"].strip()
                    
                    if response_json:
                        if content.startswith("```"):
                            lines = content.split("\n")
                            if lines[0].startswith("```"):
                                lines.pop(0)
                            if lines and lines[-1].startswith("```"):
                                lines.pop()
                            content = "\n".join(lines).strip()
                        try:
                            match = re.search(r"\{.*\}", content, re.DOTALL)
                            if match:
                                content = match.group(0)
                            parsed = json.loads(content)
                            parsed.setdefault("meta", {})
                            parsed["meta"]["modelo"] = f"mistral:{m_model}"
                            return parsed
                        except json.JSONDecodeError as je:
                            logger.error(f"Error decodificando JSON de Mistral API: {je}. Contenido: {content}")
                            raise je
                    return {"text": content, "meta": {"modelo": f"mistral:{m_model}"}}
                else:
                    logger.error(f"Error de Mistral API: {r.status_code} - {r.text}")
                    raise RuntimeError(f"Mistral API HTTP status {r.status_code}: {r.text[:200]}")
            except Exception as exc:
                logger.warning(f"Fallo en Mistral API: {exc}. Intentando fallback a Gemini...")
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
                match = re.search(r"\{.*\}", raw, re.DOTALL)
                if match:
                    raw = match.group(0)
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


def query_gemini_api(prompt: str) -> str:
    """Envía un prompt directamente a la API de Gemini Generative Language con reintentos exponenciales ante 429/503."""
    import time
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return "[LLM Error] GEMINI_API_KEY no configurada"

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }

    max_retries = 4
    backoff = 2.0
    
    for attempt in range(max_retries):
        try:
            r = httpx.post(url, json=payload, timeout=40.0)
            if r.status_code == 200:
                data = r.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return parts[0].get("text", "")
            
            if r.status_code in (429, 500, 503):
                sleep_time = backoff ** attempt
                logger.warning(
                    "Gemini API retornó HTTP %d (intento %d/%d). Reintentando en %.1f segundos...",
                    r.status_code, attempt + 1, max_retries, sleep_time
                )
                time.sleep(sleep_time)
                continue
                
            return f"[LLM Error] HTTP {r.status_code}: {r.text[:200]}"
        except Exception as exc:
            if attempt == max_retries - 1:
                logger.warning("Fallo definitivo consultando Gemini API tras %d intentos: %s", max_retries, exc)
                return f"[LLM Error] {str(exc)}"
            sleep_time = backoff ** attempt
            logger.warning("Excepción de red en Gemini API (intento %d/%d): %s. Reintentando...", attempt + 1, max_retries, exc)
            time.sleep(sleep_time)
            
    return "[LLM Error] Excedido el número máximo de reintentos"

