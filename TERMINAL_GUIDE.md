# Zohar Intelligence v4 — Guía Rápida de Terminal

Esta guía proporciona las instrucciones detalladas y comandos de terminal necesarios para configurar, administrar e interactuar con el backend y el dashboard de Zohar v4 a través de la línea de comandos.

---

## 1. Configuración del Entorno

### Activar el Entorno Virtual
```bash
cd "/home/gorops/proyectos antigravity/zohar-v4-main"
source .venv/bin/activate
```

### Configurar `.env`
```bash
cp .env.example .env
nano .env
```

Campos críticos en `.env`:
```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/maritime_dw
LOCAL_LLM_URL=http://localhost:8083
LOCAL_LLM_MODEL=gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf
CHROME_BINARY=/opt/google/chrome/google-chrome
```

---

## 2. Comandos Operativos

### Iniciar Servidor FastAPI
```bash
./start_server.sh
# Dashboard disponible en http://127.0.0.1:8004
```

### Iniciar Servidor LLM Local (Gemma 4 E2B + Vulkan)
```bash
./start_llama_server.sh
# Escucha en http://localhost:8083
```

### Ejecutar Tests
```bash
PYTHONPATH="." pytest
```

### Verificar Puertos
```bash
lsof -i :8004   # FastAPI
lsof -i :8083   # llama-server
```

---

## 3. API Endpoints — Referencia Rápida

Con el servidor en `http://127.0.0.1:8004`:

### Sistema
```bash
# Estado del sistema
curl -s http://127.0.0.1:8004/api/status | jq .

# Estado del modelo local
curl -s http://127.0.0.1:8004/api/llama/status | jq .
```

### Scraper / Ingesta

```bash
# Extraer claves SINAT de un año (SSE streaming)
curl -N -s "http://127.0.0.1:8004/api/scraper/extract-keys?year=2026"

# Descargar documentos de una clave específica (SSE streaming)
# Incluye: DOM metadata, reintentos automáticos, enriquecimiento LLM background
curl -N -s "http://127.0.0.1:8004/api/scraper/download-clave?clave=23QR2025T0061&year=2026"

# Pipeline completo (SSE streaming)
curl -N -s http://127.0.0.1:8004/api/scraper/run-pipeline
```

**Eventos SSE que emite `download-clave`:**

| `status` | Descripción |
|----------|-------------|
| `progress` | Actualización de progreso (con `pct` 0-100) |
| `retry` | Reintentando descarga (con `attempt`, `max_retries`) |
| `complete` | Descarga finalizada (con `download_status`, `n_resumenes`, `n_estudios`, `n_resolutivos`) |
| `not_found` | Clave no encontrada en el portal |
| `error` | Error irrecuperable |

### llama-server

```bash
# Ver estado del servidor LLM
curl -s http://127.0.0.1:8004/api/llama/status

# Iniciar servidor LLM
curl -X POST http://127.0.0.1:8004/api/llama/start

# Detener servidor LLM
curl -X POST http://127.0.0.1:8004/api/llama/stop
```

### Second Brain

```bash
# Construir / actualizar vault Obsidian
curl -X POST http://127.0.0.1:8004/api/second_brain/build | jq .

# Listar todas las notas
curl -s http://127.0.0.1:8004/api/second_brain/notes | jq .

# Leer una nota específica
curl -s "http://127.0.0.1:8004/api/second_brain/note?name=Inferencia%20-%2023QR2025T0061"
```

### Corpus y Markdown

```bash
# Listar PDFs
curl -s http://127.0.0.1:8004/api/corpus/pdfs | jq .

# Listar archivos Markdown
curl -s http://127.0.0.1:8004/api/md/list | jq .

# Extraer PDF a Markdown (SSE)
curl -N -s "http://127.0.0.1:8004/stream/single?pdf_name=ejemplo.pdf"
```

### Data Warehouse

```bash
# Estado de conexión y calidad
curl -s http://127.0.0.1:8004/api/dw/status | jq .

# Ejecutar pipeline de ingesta (SSE)
curl -N -s http://127.0.0.1:8004/api/dw/run-pipeline
```

---

## 4. Uso Directo del Downloader (Python)

```python
from scrapers.semarnat_downloader import SemarnatDownloader

downloader = SemarnatDownloader(
    download_dir="downloads/",
    estudios_dir="downloads/estudios/",
    resumenes_dir="downloads/resumenes/",
    resolutivos_dir="downloads/resolutivos/",
)

# Con reintentos automáticos (recomendado)
for event in downloader._descargar_clave_gen_with_retry("23QR2025T0061"):
    print(f"[{event['status']}] {event.get('msg', '')}")

# Sin reintentos (original)
for event in downloader._descargar_clave_gen("23QR2025T0061"):
    print(event)
```

---

## 5. Uso Directo del LLM Enricher (Python)

```python
from pathlib import Path
from core.llm_enricher import enrich_metadata_from_pdf, find_best_pdf_for_enrichment

classified = {
    "estudios": [Path("downloads/estudios/23QR2025T0061.pdf")],
    "resumenes": [],
    "resolutivos": []
}

existing_metadata = {
    "project_name": "Parque Solar XYZ",
    "fecha_ingreso": "15/01/2026",
    "promovente": "Desconocido",   # ← LLM lo completará
}

best_pdf = find_best_pdf_for_enrichment(classified)
enriched = enrich_metadata_from_pdf(best_pdf, existing_metadata)
print(enriched)
# → { "project_name": "...", "promovente": "Empresa XYZ S.A. de C.V.", "sector": "Energía", ... }
```

---

## 6. Git Workflow

```bash
# Ver cambios pendientes
git status

# Commit y push
git add -A
git commit -m "feat: descripción del cambio"
git push origin main
```
