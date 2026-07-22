#!/bin/bash

# ==========================================
# Configuración de Rutas
# ==========================================
VENV_PATH=".venv/bin/activate"
LLAMA_SERVER_BIN="/home/gorops/llama.cpp/build/bin/llama-server"
MODEL_PATH="AQUI_VA_LA_RUTA_DE_TU_MODELO.gguf"

# Archivos de log
LOG_DIR="logs"
FASTAPI_LOG="$LOG_DIR/fastapi.log"
LLAMA_LOG="$LOG_DIR/llama_server.log"

# ==========================================
# Preparación
# ==========================================
mkdir -p $LOG_DIR
echo "🚀 Iniciando infraestructura Zohar v4..."

# Función para detener todo limpiamente
cleanup() {
    echo ""
    echo "🛑 Deteniendo servicios..."
    kill $FASTAPI_PID 2>/dev/null
    kill $LLAMA_PID 2>/dev/null
    echo "✅ Servicios detenidos correctamente."
    exit 0
}

# Capturar señales de interrupción (Ctrl+C)
trap cleanup SIGINT SIGTERM

# ==========================================
# 1. Levantar FastAPI (Puerto 8004)
# ==========================================
echo "▶️  Iniciando FastAPI (Logs en $FASTAPI_LOG)..."
source $VENV_PATH
python -m uvicorn api.main:app --host 127.0.0.1 --port 8004 > $FASTAPI_LOG 2>&1 &
FASTAPI_PID=$!

# ==========================================
# 2. Levantar llama-server (Puerto 8083)
# ==========================================
echo "▶️  Iniciando llama-server (Logs en $LLAMA_LOG)..."
$LLAMA_SERVER_BIN -m "$MODEL_PATH" --port 8083 --host 127.0.0.1 -c 2048 > $LLAMA_LOG 2>&1 &
LLAMA_PID=$!

# ==========================================
# Estado final
# ==========================================
echo ""
echo "✨ ¡Todo en marcha!"
echo "📡 FastAPI escuchando en:     http://127.0.0.1:8004"
echo "🧠 llama-server escuchando en: http://127.0.0.1:8083"
echo ""
echo "👀 Para ver los logs en vivo, abre otra terminal y usa:"
echo "   tail -f $LOG_DIR/*.log"
echo ""
echo "Presiona Ctrl+C en esta terminal para apagar ambos servicios."

wait
