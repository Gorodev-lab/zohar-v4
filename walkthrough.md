# Walkthrough — Zohar Intelligence v4 Integrations

Hemos completado, integrado y validado con éxito las mejoras en el motor de **OCR híbrido**, la pestaña **MODEL_CHAT**, la **Dockerización completa** del stack y la **sincronización en tiempo real** del Second Brain.

---

## 🛠️ 1. Motor de OCR Híbrido en el Pipeline

### Implementación
Modificamos [`core/pdf_processor.py`](file:///home/gorops/proyectos%20antigravity/zohar-v4-main/core/pdf_processor.py) para incorporar un flujo de fallback inteligente:
1. Intenta extraer el texto digital estándar de cada página para maximizar la velocidad.
2. Si el texto resultante tiene menos de **80 caracteres** (página escaneada o con texto atrapado en imagen), automáticamente re-procesa esa página específica activando el parámetro `use_ocr=True` y configurando el idioma en español (`ocr_language="spa"`).
3. Esto aprovecha la biblioteca `rapidocr-onnxruntime` que instalamos en el entorno virtual, la cual detecta y reconoce el texto de forma local y offline mediante ONNX Runtime.

### Validación
El script de prueba [`test_ocr_direct.py`](file:///home/gorops/proyectos%20antigravity/zohar-v4-main/test_ocr_direct.py) detectó automáticamente la falta de texto en la página 13 de un documento escaneado y le aplicó OCR en español de forma 100% exitosa.

---

## ✦ 2. Panel Interactivo "MODEL_CHAT" en el Dashboard

Agregamos una nueva pestaña en el dashboard para que puedas interactuar en tiempo real con el LLM activo y visualizar sus capacidades de agente (herramientas).

### Backend (`api/main.py`)
Añadimos tres nuevos endpoints de soporte en [`api/main.py`](file:///home/gorops/proyectos%20antigravity/zohar-v4-main/api/main.py):
1. **`GET /api/model/status`:** Retorna el proveedor y modelo de inferencia actualmente detectado y activo en el sistema.
2. **`GET /api/model/tools`:** Lista las herramientas disponibles del agente de IA (`database_query`, `second_brain_search`, `ocr_extraction`, `second_brain_sync`).
3. **`POST /api/chat`:** Procesa la conversación. Si se selecciona una clave en el selector RAG, lee su ficha de `second_brain/02_Entities/{clave}.md` y la inyecta como contexto primario en el prompt del sistema.

### Frontend
* **`dashboard/index.html`:** Incorporamos el botón **MODEL_CHAT** en la barra lateral y creamos una consola estilo terminal Unix con estética cyberpunk.
* **`dashboard/static/app.js`:** Añadimos las funciones de carga de estado del modelo, renderizado dinámico de herramientas del agente y gestión del flujo de mensajes (envío, placeholder de análisis y renderizado final con metadatos del modelo).

---

## 🐳 3. Dockerización Completa & Flujo Autónomo

### Contenedor de la API y Scraper
Creamos un [`Dockerfile`](file:///home/gorops/proyectos%20antigravity/zohar-v4-main/Dockerfile) en la raíz del proyecto que permite empaquetar la aplicación completa:
- Basado en `python:3.11-slim` para estabilidad de librerías geoespaciales y de OCR.
- Instala **Google Chrome estable oficial** y librerías del sistema asociadas para soportar de forma nativa e ininterrumpida las descargas automáticas mediante **Selenium Headless** dentro del contenedor.
- Instala todas las dependencias del proyecto de forma aislada.

### Docker Compose
Actualizamos [`dw/docker-compose.yml`](file:///home/gorops/proyectos%20antigravity/zohar-v4-main/dw/docker-compose.yml) para unificar la infraestructura y la lógica en un solo comando:
1. **Modelo de Producción:** Configuramos el contenedor `download-model` para descargar de forma automática el modelo oficial de producción **Gemma 4 E2B** (`gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf`) directamente de HuggingFace.
2. **Servidor Local de Inferencia:** Actualizamos `llama-cpp` para correr Gemma 4 mapeando e interactuando en el puerto nativo del proyecto (`8083:8083`) e inyectando la configuración `--chat-template gemma`.
3. **Servicio `api`:** Añadimos el contenedor FastAPI `zohar_api` al compose, vinculándolo a la base de datos PostgreSQL, al servidor de inferencia y al volumen de archivos de forma nativa.
4. **Cloudflare Tunnel:** Actualizamos `cloudflared` para tunelizar las peticiones de inferencia a través del puerto `8083`.

---

## 🧠 4. Alimentación y Sincronización Automática del Second Brain

Para los usuarios que **no utilizan Obsidian**, el dashboard de Zohar v4 actúa como el visualizador primario y navegador de notas del Second Brain.
Para garantizar que esta base de conocimiento esté siempre actualizada sin necesidad de clics manuales de sincronización:
- Modificamos el pipeline de descarga de claves unitarias (`/api/scraper/download-clave` en [`api/main.py`](file:///home/gorops/proyectos%20antigravity/zohar-v4-main/api/main.py)).
- Ahora, tan pronto como se completa la descarga y la inferencia del modelo para un proyecto individual, el backend ejecuta automáticamente:
  1. La compilación de fichas Markdown en `second_brain/` (`SecondBrainBuilder.build_vault()`).
  2. La regeneración del índice semántico de embeddings locales (`SemanticSearchEngine.build_index()`).
- De esta manera, el proyecto se vuelve inmediatamente indexado, navegable en el dashboard y disponible para consultas y RAG con el modelo de forma instantánea.
