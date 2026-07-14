# Implementation Plan — Docker Deployment & Automated Second Brain Feeding

Este plan detalla los cambios requeridos para habilitar el despliegue del stack completo en Docker y asegurar la alimentación automática en tiempo real del Second Brain (base de datos + buscador semántico + markdown) sin requerir el uso de Obsidian.

## Proposed Changes

### 1. Dockerización Completa

#### [NEW] [Dockerfile](file:///home/gorops/proyectos%20antigravity/zohar-v4-main/Dockerfile)
Crearemos un archivo `Dockerfile` optimizado en la raíz del proyecto:
- Basado en `python:3.11-slim`.
- Instalar dependencias del sistema requeridas para ejecutar Google Chrome de forma estable en Linux.
- Instalar Google Chrome estable oficial.
- Instalar los requerimientos del proyecto en `requirements.txt` y agregar `rapidocr-onnxruntime` para el procesamiento local de OCR.
- Exponer el puerto `8004` e iniciar el servidor FastAPI mediante Uvicorn.

#### [MODIFY] [docker-compose.yml](file:///home/gorops/proyectos%20antigravity/zohar-v4-main/dw/docker-compose.yml)
Actualizaremos el archivo Docker Compose para unificar todo el flujo de trabajo:
- Modificar el servicio `download-model` para descargar **Gemma 4 E2B** (`gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf`) en lugar de Qwen.
- Actualizar `llama-cpp` para arrancar con el modelo de Gemma 4, mapeando e interactuando en el puerto `8083` (puerto nativo de inferencia local del proyecto).
- Añadir el servicio `api` (construido a partir del `Dockerfile` raíz) para levantar el dashboard y los scrapers en el mismo stack de contenedores.
- Conectar las variables de entorno de la base de datos (`DATABASE_URL=postgresql://postgres:postgres@db:5432/maritime_dw`) y del LLM (`LOCAL_LLM_URL=http://llama-cpp:8083`) para que se intercomuniquen nativamente dentro de la red del contenedor.

### 2. Alimentación Automática del Second Brain

#### [MODIFY] [api/main.py](file:///home/gorops/proyectos%20antigravity/zohar-v4-main/api/main.py)
Actualizaremos el pipeline de descargas unitarias `/api/scraper/download-clave` para:
- Ejecutar la sincronización de la bóveda Markdown mediante `SecondBrainBuilder(BASE_DIR).build_vault()`.
- Ejecutar la regeneración del índice semántico de embeddings locales con `SemanticSearchEngine(BASE_DIR).build_index()`.
- De esta manera, tan pronto como el usuario descargue o ingeste un proyecto individual (estudios/resolutivos/resúmenes), este se integrará de inmediato al Second Brain y estará disponible para búsquedas semánticas o chat con RAG en el dashboard sin requerir Obsidian.

---

## Verification Plan

### Manual Verification
1. Generar la build del nuevo contenedor de Docker localmente:
   `docker compose -f dw/docker-compose.yml build`
2. Levantar el stack completo (DB, Redis, Neo4j, llama-cpp, FastAPI) en segundo plano:
   `docker compose -f dw/docker-compose.yml up -d`
3. Probar la comunicación del API en `http://localhost:8004/api/model/status` y verificar que apunte correctamente a la instancia containerizada de `llama-cpp` corriendo Gemma 4.
4. Ejecutar una descarga desde el workflow en el dashboard y verificar que el Second Brain se actualice de forma reactiva (con logs SSE de compilación semántica de embeddings).
