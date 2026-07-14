#!/bin/bash
# start_server.sh - Inicia el servidor Zohar v4 y abre el dashboard en el navegador.

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR" || exit 1


# Activar el entorno virtual
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "Error: Entorno virtual no encontrado en .venv/"
    exit 1
fi

echo "Iniciando uvicorn en http://127.0.0.1:8004..."
# Ejecutar uvicorn en segundo plano
PYTHONPATH="." uvicorn api.main:app --host 127.0.0.1 --port 8004 &
SERVER_PID=$!

# Función para detener el servidor al salir
cleanup() {
    echo "Deteniendo el servidor (PID $SERVER_PID)..."
    kill "$SERVER_PID" 2>/dev/null
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# Esperar a que el servidor esté activo
echo "Esperando a que el servidor responda..."
for i in {1..30}; do
    if curl -s http://127.0.0.1:8004/ > /dev/null; then
        echo "Servidor listo."
        break
    fi
    sleep 0.5
done

# Abrir el navegador
if command -v xdg-open > /dev/null; then
    xdg-open "http://127.0.0.1:8004/" &
elif command -v google-chrome > /dev/null; then
    google-chrome "http://127.0.0.1:8004/" &
elif command -v firefox > /dev/null; then
    firefox "http://127.0.0.1:8004/" &
fi

# Mantener el script en primer plano para ver los logs y manejar la señal de salida
wait "$SERVER_PID"
