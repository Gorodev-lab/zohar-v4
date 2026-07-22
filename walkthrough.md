# Walkthrough — Optimización, Concurrencia y Monitoreo del Modelo Local 🧠⚡

Hemos implementado con éxito todas las optimizaciones de recursos, control de concurrencia y monitoreo proactivo para el contenedor de inferencia `maritime_llama_cpp` (Gemma-4) y el servidor API de Zohar v4.

---

## 🛠️ Cambios Realizados

### 1. Afinación del Contenedor de Inferencia (`dw/docker-compose.yml` y `start_llama_server.sh`)
- **Reducción de Hilos a Cores Físicos**: Parámetro `-t` ajustado a `4` (cores físicos del host) para evitar el sobrecalentamiento y context switching ineficiente de la CPU.
- **Ajuste de Contexto**: Tamaño de contexto `-c` reducido de `12288` a `4096` tokens.
- **Cuantización KV en 4-bits**: Parámetros de caché de claves y valores configurados en `--cache-type-k q4_0` y `--cache-type-v q4_0`.
- **Ajuste de Batching**: Reducción de parámetros de procesamiento por lote a `-b 512 -ub 512`.
- **Corrección de Docker Healthcheck**: Se redefinió la prueba de salud del contenedor para apuntar al puerto correcto (`8083`), logrando que pase a estado `healthy`.
- **Montaje de Docker Socket**: Se montó `/var/run/docker.sock` en el contenedor `zohar_api` para permitir el monitoreo y control del contenedor de inferencia.

### 2. Control de Concurrencia y Latencia (`core/llm_client.py`, `core/semantic_search.py`, `core/rsi_brain.py`)
- **Semáforo Exclusivo (`threading.Lock`)**: Se implementó una exclusión mutua en las peticiones a `llama-server` para serializar consultas y evitar sobrecargas de CPU.
- **Parámetro `n_predict` Configurable**: Ahora las llamadas a `generate_completion` permiten limitar el número máximo de tokens a generar.
  - En el re-ranking de `core/semantic_search.py`, se configuró `n_predict=256` y los fragmentos se recortaron a `200` caracteres.
  - En la curaduría atómica de `core/rsi_brain.py`, se configuró `n_predict=128`.
- **Medición de Latencia por Token**: Se implementó una persistencia en archivo temporal (`/tmp/zohar_llm_latency.json`) para calcular un promedio rolling de latencia por token.

### 3. Monitoreo de Recursos y Auto-Recuperación en FastAPI (`api/main.py`)
- **Endpoint `/api/status/model`**: Nuevo endpoint que expone el estado del modelo, latencia por token y uso real de CPU y memoria de `maritime_llama_cpp` (mediante llamadas al socket de Docker).
- **Auto-Recuperación (`llama_self_healing_loop`)**: Loop asíncrono periódico que evalúa la salud de `/health` y la latencia de `/completion`. Si se detecta un cuelgue o caída, realiza un `docker restart` automático del contenedor de inferencia.

---

## 🧪 Pruebas y Validación Realizadas

### 1. Pruebas Unitarias
Se desarrolló una suite de pruebas dedicadas en `tests/test_model_status_and_optimization.py` para validar:
- El correcto comportamiento del endpoint `/api/status/model`.
- La serialización secuencial bajo concurrencia mediante el Lock.
- El cálculo y persistencia del promedio rolling de latencia de tokens.

**Resultado:**
```bash
.venv/bin/pytest tests/test_model_status_and_optimization.py
```
> **3 passed in 0.85s** ✅

### 2. Pruebas de Integración (RSI API y Hybrid Search)
Se corrieron los tests de regresión para confirmar que no hubo efectos colaterales en la lógica RAG y de API:
- `tests/test_hybrid_search.py` -> **3 passed** ✅
- `tests/test_rsi_api.py` -> **3 passed** ✅

### 3. Validación Manual del Auto-Healing
- Se detuvo el contenedor de inferencia manualmente con `docker stop maritime_llama_cpp`.
- Tras 30 segundos, el loop de auto-recuperación de la API detectó la falta de conexión a `/health`, registró el reinicio e invocó el comando de socket.
- El contenedor `maritime_llama_cpp` volvió a iniciarse automáticamente y se restableció el estado `online` y `healthy`.
