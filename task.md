# Tareas de Implementación — Optimización y Monitoreo del Modelo Local (Gemma-4) 🧠⚡

- [x] **Componente 1: Configuración de Inferencia y Orquestación (`dw/` y script base)**
  - [x] Modificar `dw/docker-compose.yml` (parámetros de llama-cpp, healthcheck y montaje de docker.sock en api).
  - [x] Modificar `start_llama_server.sh` (variables de hilos, contexto y caché KV).
- [x] **Componente 2: Scaffolding, Semáforo y Métricas del Cliente LLM (`core/`)**
  - [x] Implementar el bloqueo de concurrencia (`threading.Lock`) y parámetro `n_predict` en `core/llm_client.py`.
  - [x] Implementar la medición y promedio de latencia por token en `core/llm_client.py`.
  - [x] Ajustar límites de longitud de fragmentos y `n_predict` en `core/semantic_search.py`.
  - [x] Agregar `n_predict` en llamadas a `generate_completion` de `core/rsi_brain.py`.
- [x] **Componente 3: Endpoints y Tarea de Auto-Recuperación en FastAPI (`api/`)**
  - [x] Implementar la lógica para obtener estadísticas de contenedor Docker en `api/main.py`.
  - [x] Crear el endpoint `/api/status/model` en `api/main.py`.
  - [x] Crear la tarea periódica `llama_self_healing_loop` y conectarla a `startup_event` en `api/main.py`.
- [x] **Componente 4: Pruebas, Verificación y Despliegue**
  - [x] Escribir y ejecutar pruebas para el endpoint `/api/status/model` y la concurrencia.
  - [x] Detener y levantar los contenedores mediante docker compose para aplicar los cambios.
  - [x] Validar la auto-recuperación matando manualmente el contenedor.
