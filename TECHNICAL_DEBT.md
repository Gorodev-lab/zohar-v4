# Deuda Técnica

## Option E: maritime_llama_cpp fuera de compose

**Contexto**:
- `maritime_llama_cpp` fue levantado fuera del compose canónico (docker run directo en red bridge).
- Esto causó que el self-healing de `zohar_api` no pudiera resolver el DNS interno (`llama-cpp`), generando reinicios constantes.

**Fix aplicado (Opción A)**:
```bash
# Conexión manual a la red dw_default con alias explícito
docker network disconnect dw_default maritime_llama_cpp
docker network connect --alias llama-cpp dw_default maritime_llama_cpp
```

**Causa raíz del crash al recrear el container**:
El comando de `docker run` incluía `/app/llama-server` como argumento redundante. La imagen `ghcr.io/ggml-org/llama.cpp:server` ya define ese binario como `ENTRYPOINT`, por lo que pasarlo como argumento causaba un error de argumento inválido (`error: invalid argument: /app/llama-server`).

**Fix aplicado (recreación)**:
```bash
docker run -d --name maritime_llama_cpp \
  --health-cmd "curl -f http://localhost:8083/health" \
  --health-interval 30s \
  --health-retries 3 \
  --health-timeout 5s \
  --health-start-period 60s \
  -e LLAMA_ARG_HOST=0.0.0.0 \
  -p 8083:8083 \
  -v '/home/gorops/proyectos antigravity/zohar-v4-main/models:/models' \
  --network dw_default --network-alias llama-cpp \
  ghcr.io/ggml-org/llama.cpp:server \
  /app/llama-server -m /models/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf \
  -c 4096 --parallel 1 -t 4 -b 512 -ub 512 --host 0.0.0.0 --port 8083 --cont-batching
```
**Nota**: El argumento `/app/llama-server` debe **omitirse** en futuras reconstrucciones, ya que la imagen lo define como `ENTRYPOINT`.

**Pendiente (Opción B)**:
- Recrear `maritime_llama_cpp` vía compose canónico cuando haya ventana sin FASE A corriendo.
- **No aplicar Opción B ni C** hasta nueva orden.

**Impacto**:
- El container ahora resuelve correctamente desde `zohar_api` (DNS interno funcionando).
- Self-healing dejó de reiniciar `zohar_api`.
- FASE A (PID 28748) no fue interrumpida.
- Healthcheck corregido (puerto 8083 en lugar de 8080).

**Riesgo residual**:
- El container sigue fuera del compose, lo que puede causar inconsistencias en futuros despliegues o actualizaciones.

---

## Option F: Configuración de slots en llama-server

**Contexto**:
llama-server por defecto reserva memoria para **4 slots concurrentes** (`-ns 4`). En entornos de bajos recursos (CPU/Swap limitado), esto multiplica el uso de `n_ctx` y puede causar **crashes por OOM Killer** o fallos de `llama_decode`.

**Problema identificado**:
- El contenedor `maritime_llama_cpp` crasheó a pesar de usar `-np 1` y `-c 4096`, debido a que el modelo Gemma 4 E2B (14GB) + contexto de 4096 tokens + 4 slots supera la memoria disponible en el host.

**Fix aplicado**:
Limitar explícitamente a **1 slot** usando `-np 1` en el comando de `docker run`:
```bash
docker run -d --name maritime_llama_cpp \
  ...
  --network dw_default --network-alias llama-cpp \
  ghcr.io/ggml-org/llama.cpp:server \
  /app/llama-server -m /models/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf \
  -c 4096 --parallel 1 -t 4 -b 512 -ub 512 --host 0.0.0.0 --port 8083 \
  -np 1  # <-- Slot único para evitar OOM
```

**Pendiente**:
- Verificar si `-np 1` es suficiente para entornos con **<16GB RAM** y Gemma 4 E2B.
- Considerar reducir `-c 4096` a `-c 2048` si persisten los crashes.

**Impacto**:
- Reduce el uso de memoria de ~14GB (4 slots) a ~3.5GB (1 slot) para Gemma 4 E2B.
- Evita que el OOM Killer mate el proceso.

**Riesgo residual**:
- Velocidad de inferencia reducida (~20 tokens/s en CPU con 1 slot).
### Option F: Segmentation Fault en llama-server
Al procesar prompts grandes (>3000 tokens) con ghcr.io/ggml-org/llama.cpp:server, el contenedor muere silenciosamente cerrando la conexión HTTP ('Remote end closed connection'). No es falta de RAM, es incompatibilidad de instrucciones CPU en esta imagen de Docker. Bloquea la extracción batch.
