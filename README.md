# Zohar Intelligence v4 🌌

> Plataforma inteligente y reactiva para el procesamiento masivo de proyectos ambientales de la SEMARNAT, extracción semántica de entidades y construcción de un Second Brain de conocimiento.

Zohar v4 es una aplicación de datos intensiva diseñada bajo criterios de alta disponibilidad, concurrencia no bloqueante y sincronización en tiempo real. Combina scrapers inteligentes basados en navegadores headless, extracción de metadatos del DOM, procesamiento híbrido de PDFs (OCR/Markdown), enriquecimiento con LLM local (Gemma 4 E2B) e inferencia analítica de viabilidad ambiental.

---

## 🏛️ Arquitectura del Sistema

```mermaid
graph TD
    A[Portal SEMARNAT] -->|Selenium Headless + CDP| B[Scrapers Engine]
    B -->|DOM Metadata| M[Metadatos Estructurados]
    B -->|PDFs| C[(Downloads Dir)]
    M -->|update_csv_metadata| CSV[(data/claves_YYYY.csv)]
    M -->|upsert_project_db| PG[(PostgreSQL)]
    C -->|PDF Processor / OCR| D[Markdown Extractions]
    D -->|Inference Engine / LLM| E[(Inference Cache JSON)]
    D & E -->|Second Brain Builder| F[(Second Brain / Obsidian Vault)]
    C -->|LLM Enricher background| PG

    subgraph FastAPI Backend
        G[watchdog File Monitor] -->|SSE Broadcaster| I[Live Updates Endpoint]
    end

    C & D & E -->|Monitorizado por| G
    I -->|Server-Sent Events| J[Dashboard SPA]
```

### Componentes de Software

| Componente | Archivo | Descripción |
|-----------|---------|-------------|
| FastAPI Backend | `api/main.py` | API unificada con endpoints SSE, gestión de descarga y persistencia |
| SPA Dashboard | `dashboard/` | Panel interactivo Glassmorphism con badges de estado en tiempo real |
| Engine de Descargas | `scrapers/semarnat_downloader.py` | Selenium + CDP + reintentos automáticos × 2 |
| LLM Enricher | `core/llm_enricher.py` | Enriquecimiento de metadatos con Gemma 4 E2B desde el PDF |
| Second Brain | `core/second_brain.py` | Constructor de vault Obsidian con wiki-links y YAML frontmatter |
| Data Warehouse | `dw/` | Schema PostgreSQL, pipeline de ingesta y auditoría de calidad |

---

## 🚀 Guía de Inicio Rápido

### Prerrequisitos

- Python 3.11+ con virtualenv
- Google Chrome instalado (Selenium Headless)
- PostgreSQL en `localhost:5432` con base de datos `maritime_dw`
- `llama-server` compilado con Vulkan (para enriquecimiento LLM local)

### 1. Configurar entorno

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configurar variables de entorno

Copia `.env.example` a `.env` y ajusta:

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/maritime_dw
LOCAL_LLM_URL=http://localhost:8083
LOCAL_LLM_MODEL=gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf
CHROME_BINARY=/opt/google/chrome/google-chrome
```

### 3. Iniciar el servidor

```bash
./start_server.sh
```

El dashboard estará disponible en **http://127.0.0.1:8004**

### 4. (Opcional) Iniciar el servidor LLM local

```bash
./start_llama_server.sh
```

El modelo Gemma 4 E2B se cargará en el puerto `8083` con aceleración Vulkan.

---

## 🔄 Cadena de Ingesta (Paso a Paso)

### Paso 1: Descarga con Extracción de Metadatos DOM

El scraper recibe la clave SINAT o bitácora, navega el portal Angular de SEMARNAT, extrae metadatos directamente del DOM (fuente 100% fidedigna) y descarga los PDFs:

```python
from scrapers.semarnat_downloader import SemarnatDownloader

downloader = SemarnatDownloader()
for event in downloader._descargar_clave_gen_with_retry("23QR2025T0061"):
    print(event)
```

**Metadatos extraídos del DOM:**
- Nombre del proyecto, fecha de ingreso, sector, estado, municipio, promovente

**Reintentos automáticos:** hasta 2 reintentos si hay error de red/timeout (no reintenta si el portal confirma `not_found`).

**Clasificación de archivos descargados:**
- `[R]` Resumen Ejecutivo
- `[E]` Estudio de Impacto Ambiental
- `[V]` Resolutivo (oficio oficial)

### Paso 2: Persistencia Dual

Inmediatamente después de la descarga, los metadatos DOM se persisten en:
- **CSV** (`data/claves_{year}.csv`) — backup ligero
- **PostgreSQL** (`public.semarnat_projects`) — fuente de verdad para queries y dashboards

### Paso 3: Enriquecimiento LLM en Background

Con el PDF descargado, **Gemma 4 E2B** (vía llama-server en puerto 8083) extrae en segundo plano los campos que el DOM no pudo obtener:

```json
{
  "promovente": "...",
  "sector": "...",
  "estado": "...",
  "municipio": "...",
  "descripcion_breve": "..."
}
```

Este paso ocurre en un `ThreadPoolExecutor` — **no bloquea** la siguiente descarga.

### Paso 4: Conversión a Markdown + Inferencia

Los PDFs se convierten a Markdown y se ejecuta la inferencia de viabilidad ambiental con el LLM local.

### Paso 5: Second Brain

El vault de Obsidian se actualiza automáticamente con nuevas notas de proyectos, wiki-links y frontmatter YAML.

---

## 📊 Dashboard — Estado de Descarga

Cada descarga muestra un badge de estado en tiempo real:

| Badge | Significado |
|-------|-------------|
| `✅ Completo [R][E][V]` | Los 3 tipos de documento descargados |
| `⚠️ Parcial (N/3)` | Solo algunos documentos disponibles |
| `❌ Fallida` | No se pudo descargar ningún archivo |
| `⟳ Reintento N/2` | Reintentando automáticamente |

---

## ⚡ Características Técnicas

### Live Updates (Watchdog + SSE)
Zohar monitoriza el almacenamiento continuamente. Cuando llega un nuevo PDF o reporte:
1. `watchdog` detecta el evento de archivo
2. El `LiveUpdateBroadcaster` envía SSE al dashboard
3. Las tablas se actualizan **sin recargar la página**

### Concurrencia No Bloqueante
Todas las operaciones I/O pesadas (OCR, LLM, scans de disco) corren en pools de hilos dedicados via `asyncio.to_thread`, manteniendo el event loop libre.

### Modelo Local — Gemma 4 E2B
- **Motor:** `llama-server` compilado con Vulkan (aceleración GPU)
- **Puerto:** `8083`
- **Modelo:** `gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf`
- **Usos:** Enriquecimiento de metadatos, clasificación de PDFs, inferencia de viabilidad ambiental, chat contextual

---

## 🗂️ Estructura del Proyecto

```
zohar-v4-main/
├── api/
│   └── main.py              # FastAPI backend unificado
├── core/
│   ├── inference_engine.py  # Motor de inferencia ambiental
│   ├── llm_client.py        # Cliente LLM unificado (llama-server/Ollama/Gemini)
│   ├── llm_enricher.py      # Enriquecimiento de metadatos desde PDF (NUEVO)
│   ├── pdf_processor.py     # Procesador OCR/Markdown de PDFs
│   ├── second_brain.py      # Constructor del vault Obsidian
│   └── semantic_search.py   # Búsqueda semántica en el Second Brain
├── dashboard/
│   ├── index.html           # SPA Dashboard Glassmorphism
│   └── static/app.js        # Lógica de cliente con SSE y badges de estado
├── data/
│   └── claves_{year}.csv    # Registro de claves (fuente backup)
├── dw/
│   ├── schema.sql           # Schema PostgreSQL
│   └── pipeline.py          # Pipeline de ingesta
├── downloads/               # PDFs descargados (estudios, resúmenes, resolutivos)
├── extractions/             # Markdown generado por OCR
├── scrapers/
│   └── semarnat_downloader.py  # Downloader Selenium + reintentos + DOM extractor
├── second_brain/            # Vault Obsidian con notas de proyectos
├── .env                     # Variables de entorno (no en git)
├── .env.example             # Plantilla de configuración
├── start_server.sh          # Inicio del servidor FastAPI
└── start_llama_server.sh    # Inicio del servidor LLM local (Vulkan)
```

---

## 📝 Variables de Entorno Requeridas

| Variable | Default | Descripción |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/maritime_dw` | Conexión PostgreSQL |
| `LOCAL_LLM_URL` | `http://localhost:8083` | URL del servidor llama-server |
| `LOCAL_LLM_MODEL` | `gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf` | Nombre del modelo cargado |
| `CHROME_BINARY` | — | Ruta al binario de Chrome (ej. `/opt/google/chrome/google-chrome`) |
| `GEMINI_API_KEY` | — | Opcional: clave para Gemini Cloud como fallback |
