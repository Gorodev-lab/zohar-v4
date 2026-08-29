"""
core/llm_client.py
Capa de abstracción unificada para LLMs locales (llama-server, Ollama) y remotos (Gemini API).
Soporta detección automática y orden de prioridad.
"""

import os
import json
import re
import time
import logging
import httpx
import threading
from pathlib import Path
from typing import Optional, Any

logger = logging.getLogger(__name__)

# --- JSON tolerante para respuestas de modelo local ---
# Gemma E2B a veces emite JSON casi-válido (comas finales, comillas simples).
# En vez de descartar el análisis real a heurístico, intentamos repararlo.
def _tolerant_json_loads(text: str):
    text = text.strip()
    # 1) intento estricto
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 2) quitar fences ```json ... ``` por si acaso
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # 3) reparar coma final antes de ] o } (quirk más común de gemma)
    fixed = re.sub(r",(\s*[\]}])", r"\1", cleaned)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    # 4) último recurso: extraer primer {...} y reparar
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        block = re.sub(r",(\s*[\]}])", r"\1", m.group(0))
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            pass
    raise json.JSONDecodeError("no se pudo parsear el JSON del modelo", text, 0)


# Lock para sincronización de acceso al servidor local
_llama_server_lock = threading.Lock()

# Bandera compartida (thread-safe vía GIL) que indica si hay una inferencia
# REAL en curso contra el llama-server local. El self-healing loop la consulta
# para NO reiniciar el contenedor mientras el server está ocupado con una
# generación legítima (el probe de salud quedaría en cola detrás de la
# inferencia y tardaría >10s, lo que el loop interpretaría erróneamente como
# "server colgado" y mataría la inferencia en curso).
LLM_INFERENCE_IN_PROGRESS = threading.Event()
# Watchdog: timestamp (time.monotonic) en que se hizo SET. Si la bandera
# queda atascada en True por un crash abrupto del proceso (SIGKILL/OOM/
# segfault) que no dispara el finally, el self-healing la ignora tras un
# umbral de seguridad para no bloquearse indefinidamente.
LLM_INFERENCE_STARTED_AT = None

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
    # Orden de prioridad (decisión 2026-07-24): Mistral (cloud) primario,
    # luego Gemini, luego llama-server local, luego Ollama, luego heurístico.
    # El llama-server local es fallback de Mistral ante cuota/indisponibilidad.

    # 1. Verificar Mistral API (primario) — SOLO si la key es válida.
    #    Una key presente pero inválida (401) ya no bloquea el pipeline: caemos
    #    al llama-server local (que ahora funciona). Esto evita que un token
    #    vencido deje todos los veredictos en heurística.
    mistral_key = os.environ.get("MISTRAL_API_KEY")
    if mistral_key:
        try:
            probe = httpx.get(
                "https://api.mistral.ai/v1/models",
                headers={"Authorization": f"Bearer {mistral_key}"},
                timeout=4.0,
            )
            if probe.status_code == 200:
                return "mistral", os.environ.get("MISTRAL_MODEL", "mistral-small-latest")
            logger.warning(
                "Mistral key inválida (HTTP %s) — usando llama-server local como backend.",
                probe.status_code,
            )
        except Exception as exc:
            logger.warning("Mistral inalcanzable (%s) — usando llama-server local.", exc)

    # 2. Verificar Gemini API
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini", "gemini-2.0-flash"

    # 3. Verificar llama-server (fallback local / primario offline)
    local_url = os.environ.get("LOCAL_LLM_URL", "http://localhost:8083")
    try:
        r = httpx.get(f"{local_url}/health", timeout=1.0)
        if r.status_code == 200:
            model_name = os.environ.get("LOCAL_LLM_MODEL", "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf")
            return "llama-server", model_name
    except Exception:
        pass

    # 4. Verificar Ollama
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
                for target in ["gemma4:e4b", "gemma:2b", "gemma:latest"]:
                    if target in models_list:
                        return "ollama", target
                return "ollama", models_list[0]
    except Exception:
        pass

    # 5. Heurístico
    return "heuristic", "fallback_heuristic"


def generate_completion(
    prompt: str,
    system_prompt: Optional[str] = None,
    response_json: bool = True,
    max_chars: Optional[int] = None,
    n_predict: Optional[int] = None,
    prefer_local: bool = False,
    temperature: Optional[float] = None,
) -> dict:
    """
    Genera una completación de chat con el backend de mayor prioridad activo.

    Cadena de providers (decisión de arquitectura 2026-07-24):
      MISTRAL (cloud, primario) -> llama-server LOCAL (fallback de cuota/indisp.)
      -> GEMINI -> OLLAMA -> heuristic.

    Mistral es el provider primario. El LLM local (llama-server) sólo se usa
    cuando Mistral falla por cuota/rate-limit (429, "quota", "rate limit") o por
    indisponibilidad (timeout/conexión). Si ambos (Mistral y local) fallan, se
    lanza RuntimeError explícito para no dejar el pipeline colgado.

    prefer_local=True fuerza al llama-server local como primero (modo offline
    explícito); en ese caso la cadena es local -> mistral -> gemini -> heuristic.
    """
    # Truncado opcional de prompt
    if max_chars and len(prompt) > max_chars:
        mid = max_chars // 2
        prompt = prompt[:mid] + "\n\n[...TEXTO TRUNCADO POR LIMITACIONES DEL CONTEXTO...]\n\n" + prompt[-mid:]

    # Determinar el primer provider:
    #  - prefer_local=True -> forzar llama-server (modo offline explícito)
    #  - prefer_local=False -> detect_active_backend() (Mistral primero en prod;
    #    respetado por tests que parchean detect_active_backend)
    if prefer_local:
        first_provider = "llama-server"
    else:
        first_provider, _ = detect_active_backend()

    # Cadena de providers en orden de preferencia (Mistral primario, local como
    # fallback de cuota/indisponibilidad, luego Gemini/Ollama/heurístico).
    full_chain = ["mistral", "llama-server", "gemini", "ollama", "heuristic"]
    # Reorganizar: primer provider primero, luego el resto en orden.
    provider_chain = [first_provider] + [p for p in full_chain if p != first_provider]

    # En modo offline (llama-server es el primero por elección o porque los
    # clouds están caídos/sin crédito), NO perder tiempo ni enfriar el server
    # probando Mistral/Gemini/Ollama muertos: truncamos la cadena en el
    # primer provider LOCAL. Esto evita el timeout de Ollama (10s) por clave
    # que deja al llama-server idle y provoca desconexiones en frio.
    if first_provider in ("llama-server", "ollama"):
        _local_idx = provider_chain.index(first_provider)
        provider_chain = provider_chain[: _local_idx + 1]
        # (no incluir "heuristic" aqui: _complete_local ya reintenta con prime)

    errors: list[str] = []

    for provider in provider_chain:
        try:
            if provider == "mistral":
                result = _complete_mistral(prompt, system_prompt, response_json, temperature)
            elif provider == "llama-server":
                result = _complete_local(prompt, system_prompt, response_json, n_predict, prefer_local, temperature)
            elif provider == "gemini":
                result = _complete_gemini(prompt, system_prompt, response_json, temperature)
            elif provider == "ollama":
                result = _complete_ollama(prompt, system_prompt, response_json, temperature)
            elif provider == "heuristic":
                # Si llegamos aquí por fallo de todos los anteriores, no devolvemos
                # un dict silencioso: lanzamos error explícito.
                if errors:
                    raise RuntimeError(
                        "Ambos providers fallaron: Mistral (cloud) y llama-server (local). "
                        + "Errores: " + " | ".join(errors)
                    )
                return {"is_fallback": True, "provider": "heuristic"}
            else:
                continue

            logger.info("Usando LLM provider: %s (modelo: %s)", provider, result.get("meta", {}).get("modelo"))
            return result

        except MistralRequestError as exc:
            # 401/403 = key inválida/expirada -> NO es un error de prompt, es
            # un fallo de auth recuperable: caer al siguiente provider (local).
            # 400 por prompt malformado -> tampoco sirve reintentar en otro
            # provider (el prompt es el mismo), pero para no dejar el pipeline
            # en heurística cuando el local SÍ funciona, también caemos a local.
            err_msg = f"{provider}: {exc}"
            errors.append(err_msg)
            logger.warning(
                "Mistral falló (auth/prompt): %s. Probando siguiente provider (local)...",
                exc,
            )
            continue
        except Exception as exc:  # noqa: BLE001 - capturar fallos recuperables del provider
            err_msg = f"{provider}: {exc}"
            errors.append(err_msg)
            # Detectar cuota/indisponibilidad de Mistral para log claro
            if provider == "mistral" and _is_quota_or_unavailable(exc):
                logger.warning("Mistral no disponible (cuota/rate-limit/conn): %s. Usando fallback local.", exc)
            else:
                logger.warning("Fallo en provider %s: %s. Probando siguiente en la cadena...", provider, exc)
            continue

    # Si salimos del bucle sin retornar, todos fallaron
    raise RuntimeError(
        "Ambos providers fallaron: Mistral (cloud) y llama-server (local). "
        + "Errores: " + " | ".join(errors)
    )


class MistralQuotaOrAvailabilityError(RuntimeError):
    """Error RECUPERABLE de Mistral: cuota/rate-limit (429), servicio no
    disponible (503), timeout de conexión o error de red/DNS.
    Permite el fallback a otro provider (llama-server local)."""


class MistralRequestError(RuntimeError):
    """Error NO RECUPERABLE de Mistral: request inválido (400), auth inválida
    (401/403), etc. No se reintenta en otro provider; se propaga directo."""


def _is_quota_or_unavailable(exc: Exception) -> bool:
    """Detecta errores de cuota/rate-limit/indisponibilidad de Mistral (recuperables)."""
    msg = str(exc).lower()
    return any(k in msg for k in (
        "429", "quota", "rate limit", "rate_limit", "exceeded",
        "too many requests", "timeout", "timed out", "connection",
        "name or service not known", "failed to resolve", "connecterror",
        "httpstatuserror", "503", "502", "500",
    ))


# Códigos HTTP de Mistral que NO son recuperables (fallan directo)
_NON_RECOVERABLE_STATUS = {400, 401, 403, 404, 405, 409, 422}


def _complete_mistral(prompt: str, system_prompt, response_json: bool, temperature: Optional[float] = None) -> dict:
    api_key = os.environ.get("MISTRAL_API_KEY", "")
    if not api_key:
        # Sin key es un problema de configuración → no recuperable
        raise MistralRequestError("MISTRAL_API_KEY no configurada")
    m_model = os.environ.get("MISTRAL_MODEL", "mistral-small-latest")
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
        "temperature": temperature if temperature is not None else 0.1,
    }
    if response_json:
        payload["response_format"] = {"type": "json_object"}

    try:
        r = httpx.post("https://api.mistral.ai/v1/chat/completions", headers=headers, json=payload, timeout=60.0)
    except (httpx.TimeoutException, httpx.ConnectError, httpx.ConnectTimeout,
            httpx.ReadTimeout, httpx.NetworkError) as exc:
        # Timeout/conexión → RECUPERABLE (fallback a local)
        raise MistralQuotaOrAvailabilityError(f"Timeout/conexión Mistral: {exc}") from exc
    except Exception as exc:
        # Otro error de red → RECUPERABLE
        raise MistralQuotaOrAvailabilityError(f"Error de red Mistral: {exc}") from exc

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
        # Clasificar el error HTTP
        if r.status_code in _NON_RECOVERABLE_STATUS:
            # 400/401/403/etc → NO RECUPERABLE: falla directo
            raise MistralRequestError(
                f"Mistral rechazó el request (HTTP {r.status_code}): {r.text[:200]}"
            )
        # 429/503/500/otros → RECUPERABLE: permite fallback a local
        raise MistralQuotaOrAvailabilityError(
            f"Mistral API HTTP status {r.status_code}: {r.text[:200]}"
        )


def _complete_local(prompt: str, system_prompt, response_json: bool, n_predict, prefer_local: bool, temperature: Optional[float] = None) -> dict:
    """Llama al llama-server local vía /completion. Fallback de Mistral."""
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
            "temperature": temperature if temperature is not None else 0.1,
            "n_predict": n_predict or 512,
            "stop": ["<end_of_turn>", "<eos>"],
        }

        try:
            # Marcar inferencia real en curso para que el self-healing loop
            # NO reinicie el contenedor mientras el server está ocupado.
            LLM_INFERENCE_IN_PROGRESS.set()
            LLM_INFERENCE_STARTED_AT = time.monotonic()
            max_local_retries = 3 if prefer_local else 1
            last_exc: Exception | None = None
            for attempt in range(1, max_local_retries + 1):
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
                                parsed = _tolerant_json_loads(content)
                                parsed.setdefault("meta", {})
                                parsed["meta"]["modelo"] = "llama-server:gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf"
                                return parsed
                            except json.JSONDecodeError as je:
                                logger.error(f"Error decodificando JSON del modelo local: {je}. Contenido: {content}")
                                raise je
                        return {"text": content, "meta": {"modelo": "llama-server:gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf"}}
                    else:
                        logger.error(f"Error de llama-server: {r.status_code} - {r.text}")
                        raise RuntimeError(f"Server status {r.status_code}")
                except Exception as exc:
                    last_exc = exc
                    if attempt < max_local_retries:
                        logger.warning(
                            "Fallo en llama-server (fallback local, intento %d/%d): %s. Re-verificando salud...",
                            attempt, max_local_retries, exc,
                        )
                        # El server GGUF/gemma en CPU desconecta la PRIMERA
                        # generacion tras quedar idle. Hacemos un llamado
                        # "prime" descartable para dejarlo caliente antes del
                        # reintento real.
                        try:
                            httpx.post(
                                f"{local_url.rstrip('/')}/completion",
                                json={"prompt": "<start_of_turn>user\nOK<end_of_turn>\n<start_of_turn>model\n",
                                      "n_predict": 8, "temperature": 0.0,
                                      "stop": ["<end_of_turn>", "<eos>"]},
                                timeout=60.0,
                            )
                        except Exception:
                            pass
                        # El server GGUF/gemma en CPU puede CRASHEAR (no solo
                        # idle) en generaciones largas; el contenedor (--restart
                        # unless-stopped) lo respawna en ~3s. Esperamos a que
                        # vuelva a estar saludable ANTES del reintento real,
                        # no asumimos que ya lo está.
                        _waited = 0.0
                        while _waited < 15.0:
                            time.sleep(2.0)
                            _waited += 2.0
                            if is_local_llm_healthy(probe_timeout=5.0).get("healthy"):
                                logger.info("llama-server recuperado tras crash (%ss); reintentando...", int(_waited))
                                break
                        else:
                            logger.warning("llama-server sigue no saludable tras %ss; se usará siguiente fallback.", int(_waited))
                            break
                        continue
                    logger.warning(f"Fallo definitivo en llama-server local: {exc}.")
            if last_exc:
                raise last_exc
            raise RuntimeError("llama-server local no retornó resultado")
        finally:
            # Limpiar la bandera SIEMPRE (éxito, error, timeout o crash parcial)
            # para no dejar al self-healing loop pausado indefinidamente.
            LLM_INFERENCE_IN_PROGRESS.clear()


def _complete_ollama(prompt: str, system_prompt, response_json: bool, temperature: Optional[float] = None) -> dict:
    base_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    if not base_url.endswith("/v1"):
        base_url = f"{base_url.rstrip('/')}/v1"

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": os.environ.get("OLLAMA_MODEL", "gemma:latest"),
        "messages": messages,
        "temperature": temperature if temperature is not None else 0.1,
    }
    if response_json:
        payload["response_format"] = {"type": "json_object"}

    r = httpx.post(f"{base_url}/chat/completions", json=payload, timeout=90.0)
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
                parsed["meta"]["modelo"] = f"ollama:{payload['model']}"
                return parsed
            except json.JSONDecodeError as je:
                logger.error(f"Error decodificando JSON de Ollama: {je}. Contenido: {content}")
                raise je
        return {"text": content, "meta": {"modelo": f"ollama:{payload['model']}"}}
    else:
        logger.error(f"Error de Ollama: {r.status_code} - {r.text}")
        raise RuntimeError(f"Server status {r.status_code}")


def _complete_gemini(prompt: str, system_prompt, response_json: bool, temperature: Optional[float] = None) -> dict:
    from google import genai
    api_key = os.environ.get("GEMINI_API_KEY", "")
    client = genai.Client(api_key=api_key)

    full_prompt = prompt
    if system_prompt:
        full_prompt = f"{system_prompt}\n\nTEXTO A PROCESAR:\n{prompt}"

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[{"role": "user", "parts": [{"text": full_prompt}]}]
    )
    raw = response.text.strip()

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


# ─────────────────────────────────────────────────────────────────────────────
# HEALTH CHECK EXPLÍCITO DEL LLM LOCAL
# Prioriza la infraestructura local (llama-server) sobre los fallbacks remotos
# (Mistral / Gemini). Valida no solo /health sino una inferencia real de prueba,
# porque /health puede responder 200 mientras el modelo aún está cargando pesos
# o saturando la RAM (el caso observado: 503 bajo carga tras devolver 200).
# ─────────────────────────────────────────────────────────────────────────────

def is_local_llm_healthy(probe_timeout: float = 8.0, probe_prompt: str = "Responde solo 'OK': ") -> dict:
    """
    Verifica que el llama-server local esté realmente operativo.

    Hace dos chequeos:
      1. GET /health  (debe ser 200)
      2. POST /completion con un prompt de prueba mínimo (debe devolver contenido)

    Retorna un dict:
        {
          "healthy": bool,
          "url": str,
          "model": str,
          "health_status": int,        # código HTTP de /health o 0 si no respondió
          "probe_ok": bool,            # la inferencia de prueba respondió
          "probe_ms": float,           # latencia del probe
          "error": Optional[str]
        }
    Nunca lanza excepción.
    """
    local_url = os.environ.get("LOCAL_LLM_URL", "http://localhost:8083").rstrip("/")
    model_name = os.environ.get("LOCAL_LLM_MODEL", "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf")
    result = {
        "healthy": False,
        "url": local_url,
        "model": model_name,
        "health_status": 0,
        "probe_ok": False,
        "probe_ms": 0.0,
        "error": None,
    }
    try:
        h = httpx.get(f"{local_url}/health", timeout=probe_timeout)
        result["health_status"] = h.status_code
        if h.status_code != 200:
            result["error"] = f"/health devolvió {h.status_code}"
            return result
    except Exception as exc:
        result["error"] = f"No se pudo conectar a /health: {exc}"
        return result

    # Segundo chequeo: inferencia real de prueba (el /health puede mentir bajo carga)
    try:
        t0 = time.time()
        r = httpx.post(
            f"{local_url}/completion",
            json={"prompt": probe_prompt, "n_predict": 8, "temperature": 0.0},
            timeout=probe_timeout,
        )
        dt = (time.time() - t0) * 1000.0
        result["probe_ms"] = round(dt, 1)
        if r.status_code == 200:
            content = (r.json().get("content") or "").strip()
            if content:
                result["probe_ok"] = True
                result["healthy"] = True
            else:
                result["error"] = "Probe devolvió contenido vacío"
        else:
            result["error"] = f"/completion devolvió {r.status_code}"
    except Exception as exc:
        result["error"] = f"Fallo en probe de inferencia: {exc}"
    return result


def prefer_local_backend() -> tuple[str, str, dict]:
    """
    Devuelve el backend a usar, priorizando SIEMPRE el llama-server local si está
    realmente sano (health + probe). Si no, cae al siguiente disponible según
    detect_active_backend() (Mistral / Gemini / heuristic).

    Retorna: (provider, model_name, health_info)
    """
    health = is_local_llm_healthy()
    if health["healthy"]:
        return "llama-server", health["model"], health
    # Local no disponible: delegar a la detección estándar (respetando su orden de prioridad)
    provider, model_name = detect_active_backend()
    return provider, model_name, health


def assert_local_llm_ready(raise_on_unhealthy: bool = False) -> dict:
    """
    Check explícito al inicio del pipeline. Loguea la decisión y retorna el estado.
    Si raise_on_unhealthy=True y el local no está sano, lanza RuntimeError.
    """
    provider, model_name, health = prefer_local_backend()
    if health["healthy"]:
        logger.info(
            "LLM LOCAL listo: %s (%s) - health=%s probe=%sms",
            provider, model_name, health["health_status"], health["probe_ms"],
        )
    else:
        logger.warning(
            "LLM LOCAL NO DISPONIBLE (%s). Usando fallback: %s (%s). Razon: %s",
            health.get("url"), provider, model_name, health.get("error"),
        )
        if raise_on_unhealthy:
            raise RuntimeError(f"llama-server local no disponible: {health.get('error')}")
    return {"provider": provider, "model": model_name, "local_health": health}

