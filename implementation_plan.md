# Plan de Implementación — Optimización y Monitoreo del Modelo Local (Gemma-4) 🧠⚡

Optimizar los recursos, la latencia y la concurrencia del contenedor de inferencia local `maritime_llama_cpp` (basado en `llama.cpp`) e integrar endpoints de monitoreo en tiempo real y auto-recuperación (self-healing) en el servidor API de Zohar v4.

## User Review Required

> [!IMPORTANT]
> - **Límite de Contexto de 4096**: Se reduce el tamaño de contexto de `llama-server` de `12288` a `4096` tokens. Esto reduce drásticamente el uso de memoria RAM/VRAM en el host, lo cual es ideal dado que las fichas y resúmenes del Second Brain son extremadamente compactos (<4KB).
> - **Cuantización KV a 4-bits (`q4_0`)**: Cambiar la cuantización del caché KV de `q8_0` a `q4_0` para reducir a la mitad la huella de memoria dinámica de contexto, manteniendo una excelente velocidad de re-ranking y procesamiento sin penalización perceptible de precisión.
> - **Hilos de CPU Limitados a 4**: Dado que el host cuenta con 4 cores físicos (8 hilos lógicos), limitaremos el parámetro `-t` a `4` para evitar el sobrecalentamiento por context switching excesivo de la CPU.
> - **Semáforo de Concurrencia de 1**: Se añadirá un bloqueo de concurrencia exclusivo en Python (`threading.Lock`) para asegurar que solo una petición se procese al mismo tiempo en el servidor de inferencia local, evitando la saturación del procesador de 4 núcleos físicos.

## Open Questions

*Ninguna. Todas las decisiones de diseño fueron alineadas con el usuario a través de la fase interactiva.*

## Proposed Changes

---

### Componente 1: Configuración de Inferencia y Orquestación (`dw/` y script base)

#### [MODIFY] [docker-compose.yml](file:///home/gorops/proyectos%20antigravity/zohar-v4-main/dw/docker-compose.yml)
- Ajustar los parámetros del comando de `llama-cpp`:
  - Cambiar `-c 12288` a `-c 4096`
  - Cambiar `-t 6` a `-t 4`
  - Cambiar `-b 4096 -ub 4096` a `-b 512 -ub 512`
  - Cambiar `--cache-type-k q8_0 --cache-type-v q8_0` a `--cache-type-k q4_0 --cache-type-v q4_0`
- Configurar y corregir el bloque `healthcheck` del servicio `llama-cpp` para que apunte al puerto `8083` en lugar del puerto por defecto de la imagen (`8080`), permitiendo que Docker reporte correctamente su estado como `healthy`.
- Añadir el montaje del socket de docker `/var/run/docker.sock:/var/run/docker.sock` al servicio de `api` para permitir el auto-reinicio y consulta de estadísticas desde FastAPI.

#### [MODIFY] [start_llama_server.sh](file:///home/gorops/proyectos%20antigravity/zohar-v4-main/start_llama_server.sh)
- Actualizar variables de configuración:
  - `CTX_SIZE="4096"`
  - `THREADS="4"`
- Modificar los flags del comando para incluir `--cache-type-k q4_0 --cache-type-v q4_0` y `-b 512 -ub 512`.

---

### Componente 2: Scaffolding, Semáforo y Métricas del Cliente LLM (`core/`)

#### [MODIFY] [llm_client.py](file:///home/gorops/proyectos%20antigravity/zohar-v4-main/core/llm_client.py)
- Añadir un bloqueo thread-safe `threading.Lock` exclusivo para las peticiones a `llama-server`.
- Implementar la firma de `generate_completion` para aceptar un parámetro opcional `n_predict` y pasarlo en el payload.
- Medir la latencia de respuesta y extraer `predicted_per_token_ms` para calcular y almacenar el promedio de latencia acumulado por token en memoria.
- Exportar la función `get_avg_latency_per_token()` para consumo de los endpoints.

#### [MODIFY] [semantic_search.py](file:///home/gorops/proyectos%20antigravity/zohar-v4-main/core/semantic_search.py)
- Limitar el tamaño de fragmentos a evaluar de 250 a 200 caracteres para ahorrar tokens en la ventana de contexto.
- Pasar el parámetro `n_predict=256` (o inferior) a `generate_completion` en `rerank_candidates` para forzar salidas de evaluación muy cortas.

---

### Componente 3: Endpoints y Tarea de Auto-Recuperación en FastAPI (`api/`)

#### [MODIFY] [main.py](file:///home/gorops/proyectos%20antigravity/zohar-v4-main/api/main.py)
- Implementar endpoint `/api/status/model` que retorne:
  - Estado del modelo local (`online`, `offline`, `booting`).
  - Estadísticas de uso de recursos en tiempo real del contenedor `maritime_llama_cpp` (CPU %, memoria usada en GB y %).
  - Latencia promedio por token del modelo local (desde `llm_client.py`).
- Implementar una tarea en segundo plano asíncrona periódica `llama_self_healing_loop()` lanzada en `startup_event` que:
  - Valide la salud de `llama-server` llamando a `/health`.
  - Envíe un prompt de prueba liviano a `/completion` y verifique si responde en menos de 10 segundos.
  - Si responde `unhealthy`, excede el umbral de latencia de 10 segundos o da error de conexión de red persistente, invoque un reinicio del contenedor `maritime_llama_cpp` a través de curl al socket `/var/run/docker.sock`.

## Verification Plan

### Automated Tests
- Ejecutar pruebas existentes: `pytest tests/test_humo.py` y `pytest tests/test_background_inference.py`.
- Añadir y correr pruebas específicas de concurrencia y salud de la API.

### Manual Verification
- Iniciar Docker Compose: `docker compose up -d`.
- Verificar en `docker ps` que `maritime_llama_cpp` pasa a estar `healthy`.
- Realizar peticiones simultáneas a la API para verificar la correcta aplicación del semáforo.
- Apagar el contenedor de `llama-cpp` y verificar que el loop de auto-recuperación de FastAPI lo enciende nuevamente tras detectar la inactividad.
- Consultar `/api/status/model` para confirmar que reporta las métricas de docker y la latencia correctamente.
