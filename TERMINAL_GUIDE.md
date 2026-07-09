# Zohar Intelligence v4 — Guía Rápida de Terminal

Esta guía proporciona las instrucciones detalladas y comandos de terminal necesarios para configurar, administrar e interactuar con el backend y el dashboard de Zohar v4 a través de la línea de comandos.

---

## 1. Configuración del Entorno

Antes de ejecutar cualquier comando, asegúrate de activar el entorno virtual y configurar las variables de entorno necesarias.

### Activar el Entorno Virtual
Desde el directorio raíz del proyecto:
```bash
# Si estás en el directorio de scratch/pruebas:
cd "/home/gorops/.gemini/antigravity/scratch/zohar-v4"
source .venv/bin/activate

# O si creaste un entorno en el directorio principal:
cd "/home/gorops/proyectos antigravity/zohar-v4-main"
source .venv/bin/activate
```

### Configuración del archivo `.env`
Copia el archivo de ejemplo y rellena las credenciales correspondientes:
```bash
cp .env.example .env
nano .env
```
Asegúrate de configurar los siguientes campos críticos en `.env`:
*   `GEMINI_API_KEY`: Tu clave de API para el motor de inferencia e IA.
*   `NEO4J_URI` (puerto 7688 por defecto para esta instancia).
*   `SUPABASE_URL` y keys para la base de datos de gacetas.

---

## 2. Comandos Operativos y de Administración

### Levantar el Servidor FastAPI (Uvicorn)
Para iniciar el servidor del dashboard en el puerto `8004` con recarga automática:
```bash
PYTHONPATH="." uvicorn api.main:app --host 127.0.0.1 --port 8004 --reload
```
O de manera alternativa, puedes usar el script automatizado para levantar el servidor y abrir el dashboard en el navegador:
```bash
./start_server.sh
```

### Ejecutar Pruebas Unitarias
Para correr la suite de tests (`pytest`) y verificar que todos los módulos e integraciones funcionen correctamente:
```bash
PYTHONPATH="." pytest
```

### Verificar Estado de Puertos del Sistema
Para comprobar si el servidor está escuchando en el puerto `8004`:
```bash
lsof -i :8004
```

---

## 3. Interacción con la API mediante `curl`

Con el servidor corriendo en `http://127.0.0.1:8004`, puedes interactuar con cada uno de los módulos utilizando comandos `curl`.

### Módulo 1: Estado del Sistema e Integración
*   **Obtener métricas del sistema** (Uptime, uso de CPU, RAM, estadísticas del Second Brain):
    ```bash
    curl -s http://127.0.0.1:8004/api/status | jq .
    ```

### Módulo 2: Corpus PDF
*   **Listar todos los archivos PDF del corpus**:
    ```bash
    curl -s http://127.0.0.1:8004/api/corpus/pdfs | jq .
    ```
*   **Extraer páginas de un PDF a Markdown en tiempo real (SSE - Streaming)**:
    *(Usa `-N` o `--no-buffer` para forzar a curl a mostrar el flujo SSE de inmediato sin guardarlo en buffer)*:
    ```bash
    curl -N -s "http://127.0.0.1:8004/stream/single?pdf_name=Gaceta_ECOLOGICA_2026_01.pdf"
    ```
*   **Detener una extracción activa de PDF**:
    ```bash
    curl -s "http://127.0.0.1:8004/stop_single?pdf_name=Gaceta_ECOLOGICA_2026_01.pdf"
    ```

### Módulo 3: MD Lab (Lectura de Markdowns)
*   **Listar archivos Markdown extraídos en `extractions/`**:
    ```bash
    curl -s http://127.0.0.1:8004/api/md/list | jq .
    ```
*   **Leer el contenido de un archivo Markdown específico**:
    ```bash
    curl -s "http://127.0.0.1:8004/api/md/read?filename=Gaceta_ECOLOGICA_2026_01.md"
    ```

### Módulo 4: Grafo de Red (D3)
*   **Obtener la estructura de nodos y enlaces del grafo de conocimiento**:
    ```bash
    curl -s http://127.0.0.1:8004/api/graph | jq .
    ```

### Módulo 5: Scraper de Gacetas SEMARNAT
*   **Ver resumen de gacetas disponibles en base de datos**:
    ```bash
    curl -s http://127.0.0.1:8004/api/scraper/gacetas-summary | jq .
    ```
*   **Extraer claves SINAT del año seleccionado** (ejemplo: 2026):
    ```bash
    curl -s "http://127.0.0.1:8004/api/scraper/extract-keys?year=2026" | jq .
    ```
*   **Ejecutar el Pipeline completo de Ingestión en tiempo real (SSE - Streaming)**:
    ```bash
    curl -N -s http://127.0.0.1:8004/api/scraper/run-pipeline
    ```

### Módulo 6: Second Brain & Inference Lab
*   **Construir o actualizar la base de conocimiento del Second Brain**:
    ```bash
    curl -X POST http://127.0.0.1:8004/api/second_brain/build | jq .
    ```
*   **Listar todas las notas del Second Brain**:
    ```bash
    curl -s http://127.0.0.1:8004/api/second_brain/notes | jq .
    ```
*   **Leer una nota de inferencia o entidad específica**:
    ```bash
    curl -s "http://127.0.0.1:8004/api/second_brain/note?path=03_Inferences/clave_SINAT_ejemplo.md"
    ```
*   **Obtener la lista de inferencias de IA generadas**:
    ```bash
    curl -s http://127.0.0.1:8004/api/inference | jq .
    ```
