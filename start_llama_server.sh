#!/bin/bash
# start_llama_server.sh
# Levanta llama-server con Gemma 4 E2B en el puerto 8082.
# Endpoint compatible con Ollama / OpenAI (POST /v1/chat/completions).
#
# Uso:
#   ./start_llama_server.sh          # primer plano (Ctrl+C para detener)
#   ./start_llama_server.sh --bg     # segundo plano, guarda PID en /tmp/llama_server.pid

set -euo pipefail

# ─── Configuración ──────────────────────────────────────────────────────────
LLAMA_SERVER="/home/gorops/llama.cpp/build/bin/llama-server"
MODEL_PATH="/home/gorops/.cache/huggingface/hub/models--unsloth--gemma-4-E2B-it-qat-GGUF/snapshots/2ea637031baa8dc847d64b5dbb7011fd6a445849/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf"
QWEN_MODEL="/home/gorops/.gemini/antigravity/scratch/zohar-v4/models/qwen2.5-3b-instruct-q4_k_m.gguf"

HOST="127.0.0.1"
PORT="8083"
CTX_SIZE="12288"         # Reducido para optimizar VRAM/RAM
N_GPU_LAYERS="99"        # Máximo offload a GPU (ajusta si hay OOM)
THREADS="6"              # Reservar cores para el host
PARALLEL="4"             # Aumentado de 2 a 4 slots concurrentes
PID_FILE="/tmp/zohar_llama_server.pid"
LOG_FILE="/tmp/zohar_llama_server.log"

# ─── Seleccionar modelo ───────────────────────────────────────────────────────
if [ "${1:-}" = "--qwen" ]; then
    ACTIVE_MODEL="$QWEN_MODEL"
    echo "🔬  Modo PRUEBA → Qwen2.5-3B"
else
    ACTIVE_MODEL="$MODEL_PATH"
    echo "🚀  Modo PRODUCCIÓN → Gemma 4 E2B"
fi

# ─── Verificaciones ──────────────────────────────────────────────────────────
if [ ! -f "$LLAMA_SERVER" ]; then
    echo "❌  llama-server no encontrado en: $LLAMA_SERVER"
    exit 1
fi

if [ ! -f "$ACTIVE_MODEL" ] && [ ! -L "$ACTIVE_MODEL" ]; then
    echo "❌  Modelo no encontrado en: $ACTIVE_MODEL"
    exit 1
fi

# Verificar si ya corre otro proceso en el puerto
if lsof -Pi :"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "⚠️   El puerto $PORT ya está en uso. Detén el proceso existente primero."
    echo "    (Para matar: kill \$(cat $PID_FILE) o pkill -f 'llama-server.*$PORT')"
    exit 1
fi

# ─── Lanzar servidor ─────────────────────────────────────────────────────────
CMD="$LLAMA_SERVER \
  --model \"$ACTIVE_MODEL\" \
  --host $HOST \
  --port $PORT \
  --ctx-size $CTX_SIZE \
  --n-gpu-layers $N_GPU_LAYERS \
  --threads $THREADS \
  --parallel $PARALLEL \
  --flash-attn on \
  --cache-type-k q8_0 \
  --cache-type-v q8_0 \
  --cont-batching \
  --jinja \
  --chat-template gemma"

echo ""
echo "─────────────────────────────────────────────"
echo "  🧠  Zohar llama-server"
echo "  Modelo: $(basename "$ACTIVE_MODEL")"
echo "  Endpoint: http://$HOST:$PORT"
echo "  Contexto: $CTX_SIZE tokens"
echo "─────────────────────────────────────────────"
echo ""

if [ "${1:-}" = "--bg" ] || [ "${2:-}" = "--bg" ]; then
    # Segundo plano
    eval "$CMD" > "$LOG_FILE" 2>&1 &
    SERVER_PID=$!
    echo "$SERVER_PID" > "$PID_FILE"
    echo "✅  llama-server iniciado en segundo plano (PID: $SERVER_PID)"
    echo "    Logs:  $LOG_FILE"
    echo "    PID:   $PID_FILE"

    # Esperar a que el servidor responda (hasta 60 seg)
    echo "   Esperando respuesta en http://$HOST:$PORT/health ..."
    for i in $(seq 1 60); do
        if curl -sf "http://$HOST:$PORT/health" > /dev/null 2>&1; then
            echo "✅  Servidor listo después de ${i}s"
            echo ""
            echo "  Prueba rápida:"
            echo "  curl http://$HOST:$PORT/v1/models"
            break
        fi
        sleep 1
    done
else
    # Primer plano (trap para limpieza)
    cleanup() {
        echo ""
        echo "🛑  Deteniendo llama-server..."
        rm -f "$PID_FILE"
        exit 0
    }
    trap cleanup SIGINT SIGTERM

    eval "$CMD" &
    SERVER_PID=$!
    echo "$SERVER_PID" > "$PID_FILE"

    # Esperar a que arranque
    for i in $(seq 1 60); do
        if curl -sf "http://$HOST:$PORT/health" > /dev/null 2>&1; then
            echo "✅  Servidor listo (${i}s)"
            break
        fi
        sleep 1
    done

    echo ""
    echo "  Ctrl+C para detener."
    wait "$SERVER_PID"
fi
