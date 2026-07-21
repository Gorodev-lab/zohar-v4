#!/bin/bash
# start_server.sh - Inicia el stack completo de Zohar v4 en Docker y abre el dashboard.

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR" || exit 1

echo "=========================================================="
echo "Iniciando Zohar Intelligence v4..."
echo "=========================================================="

# Levantar todos los servicios del docker-compose (db, redis, neo4j, llama-cpp, api)
echo "[INFO] Levantando contenedores Docker..."
docker compose -f dw/docker-compose.yml up -d

# Esperar a que el servidor FastAPI (puerto 8004) responda
echo "[WAIT] Esperando a que el servidor API responda en http://127.0.0.1:8004/ ..."
for i in {1..60}; do
    if curl -s http://127.0.0.1:8004/ > /dev/null; then
        echo "[PASS] Servidor API listo y conectado."
        break
    fi
    sleep 1
done

# Abrir el navegador con el dashboard
echo "[INFO] Abriendo el dashboard en el navegador..."
if command -v xdg-open > /dev/null; then
    xdg-open "http://127.0.0.1:8004/" &
elif command -v google-chrome > /dev/null; then
    google-chrome "http://127.0.0.1:8004/" &
elif command -v firefox > /dev/null; then
    firefox "http://127.0.0.1:8004/" &
fi

# Manejar salida graciosa de visualización de logs
cleanup() {
    echo ""
    echo "[INFO] Deteniendo visualización de logs."
    echo "[NOTE] Los contenedores siguen activos en segundo plano."
    echo "       Para apagarlos por completo ejecuta: docker compose -f dw/docker-compose.yml down"
    exit 0
}
trap cleanup SIGINT SIGTERM

# Mostrar logs de la API en tiempo real
echo "[LOGS] Mostrando logs del servidor api (Ctrl+C para salir)..."
echo "----------------------------------------------------------"
docker compose -f dw/docker-compose.yml logs -f api
