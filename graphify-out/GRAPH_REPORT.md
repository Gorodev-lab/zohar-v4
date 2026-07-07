# Graph Report - .  (2026-07-02)

## Corpus Check
- Corpus is ~10,756 words - fits in a single context window. You may not need a graph.

## Summary
- 257 nodes · 363 edges · 16 communities
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 9 edges (avg confidence: 0.78)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_API Endpoints & Routing|API Endpoints & Routing]]
- [[_COMMUNITY_SEMARNAT Downloader Utility|SEMARNAT Downloader Utility]]
- [[_COMMUNITY_Gazette Scraper Core|Gazette Scraper Core]]
- [[_COMMUNITY_Frontend Interactions|Frontend Interactions]]
- [[_COMMUNITY_ASEA Scraper Core|ASEA Scraper Core]]
- [[_COMMUNITY_Frontend Architecture & Layout|Frontend Architecture & Layout]]
- [[_COMMUNITY_PDF File Classifier & Tests|PDF File Classifier & Tests]]
- [[_COMMUNITY_PDF Text & Block Processor|PDF Text & Block Processor]]
- [[_COMMUNITY_Knowledge Graph Builder|Knowledge Graph Builder]]
- [[_COMMUNITY_Selenium Driver & Harness Tests|Selenium Driver & Harness Tests]]
- [[_COMMUNITY_Pipeline Integration Tests|Pipeline Integration Tests]]

## God Nodes (most connected - your core abstractions)
1. `$()` - 26 edges
2. `ASEAScraper` - 17 edges
3. `GazetteScraper` - 17 edges
4. `SemarnatDownloader` - 13 edges
5. `renombrar_archivos_por_clave()` - 12 edges
6. `Requirements Specification` - 11 edges
7. `make_chrome_driver()` - 9 edges
8. `TestClassificationHeuristic` - 9 edges
9. `Dashboard HTML View` - 9 edges
10. `build_full_graph()` - 8 edges

## Surprising Connections (you probably didn't know these)
- `StreamingResponse` --uses--> `ASEAScraper`  [INFERRED]
  api/main.py → scrapers/asea_scraper.py
- `StreamingResponse` --uses--> `GazetteScraper`  [INFERRED]
  api/main.py → scrapers/gazette_scraper.py
- `Dashboard HTML View` --conceptually_related_to--> `FastAPI Web Framework`  [INFERRED]
  dashboard/index.html → requirements.txt
- `test_asea_scraper_generator()` --calls--> `ASEAScraper`  [EXTRACTED]
  tests/test_scraper_pipeline.py → scrapers/asea_scraper.py
- `test_gazette_scraper_generator()` --calls--> `GazetteScraper`  [EXTRACTED]
  tests/test_scraper_pipeline.py → scrapers/gazette_scraper.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Dashboard Navigation Modules** — dashboard_index_corpus_pdf, dashboard_index_md_lab, dashboard_index_grafo_red, dashboard_index_inference_lab [EXTRACTED 1.00]
- **PDF Processing Stack** — zohar_v4_requirements_pymupdf4llm, zohar_v4_requirements_markitdown, zohar_v4_requirements_pymupdf [INFERRED 0.85]
- **Geospatial Stack** — zohar_v4_requirements_shapely, zohar_v4_requirements_pyproj, zohar_v4_requirements_utm [INFERRED 0.85]

## Communities (16 total, 0 thin omitted)

### Community 0 - "API Endpoints & Routing"
Cohesion: 0.06
Nodes (38): api_status(), _event_stream(), extract_keys(), get_inference(), list_inference(), list_md(), list_pdfs(), api/main.py FastAPI unificado para Zohar Intelligence v4. Endpoints SSE, GZip, c (+30 more)

### Community 1 - "SEMARNAT Downloader Utility"
Cohesion: 0.08
Nodes (25): _classify_by_keyword(), download_pdf_via_requests(), element_exists(), extract_pdf_urls_from_network_log(), mover_estudios_y_resolutivos(), Path, scrapers/semarnat_downloader.py Motor principal de descarga SINAT/SEMARNAT. Chro, Click robusto con scroll, retry y fallback JS. (+17 more)

### Community 2 - "Gazette Scraper Core"
Cohesion: 0.11
Nodes (17): GazetteScraper, Path, Registra la gaceta descargada en Supabase (si está configurado)., Scraper de Gacetas Ecológicas publicadas en el portal SINAT.     Navega el ifram, Genera URL del iframe para el año especificado., Extrae enlaces PDF válidos del HTML del iframe., Descarga todas las gacetas de un año. Retorna lista de PDFs., Generador SSE para descarga de gacetas.         Emite {"status": "progress"|"com (+9 more)

### Community 3 - "Frontend Interactions"
Cohesion: 0.13
Nodes (17): $(), activateTab(), appendLog(), escHtml(), loadCorpus(), loadGraph(), loadInferenceList(), loadMdList() (+9 more)

### Community 4 - "ASEA Scraper Core"
Cohesion: 0.11
Nodes (17): ASEAScraper, Path, scrapers/asea_scraper.py Descargador de Gacetas ASEA (sin Selenium — solo reques, Descarga todas las gacetas. Wrapper síncrono., Scraper de Gacetas ASEA (Agencia de Seguridad, Energía y Ambiente).     No requi, Retorna lista de gacetas disponibles:         [{"url": str, "year": int|None, "f, Extrae el año (20xx) de una cadena de texto., Generador SSE de descarga de gacetas ASEA.         Emite {"status": "progress"|" (+9 more)

### Community 5 - "Frontend Architecture & Layout"
Cohesion: 0.12
Nodes (21): App CSS styles, App JavaScript client, CORPUS_PDF Tab, D3.js Library, GRAFO_RED Tab, Dashboard HTML View, INFERENCE_LAB Tab, MD_LAB Tab (+13 more)

### Community 6 - "PDF File Classifier & Tests"
Cohesion: 0.17
Nodes (12): Clasifica y renombra PDFs nuevos por clave SEMARNAT.      Regla de fallback posi, renombrar_archivos_por_clave(), Path, 3 PDFs sin keywords → resumen + estudio + resolutivo.          CONTRATO INMUTABL, 1 PDF sin keywords → estudio (índice 0 cuando n=1)., Si el nombre contiene keyword, prevalece sobre posición., Directorio vacío retorna listas vacías., Valida el clasificador posicional renombrar_archivos_por_clave().      REGLA DE (+4 more)

### Community 7 - "PDF Text & Block Processor"
Cohesion: 0.17
Nodes (16): classify_page(), _compile_patterns(), detect_bio_blocks(), detect_geo_blocks(), detect_legal_blocks(), _extract_matching_lines(), iter_pages_as_markdown(), Path (+8 more)

### Community 8 - "Knowledge Graph Builder"
Cohesion: 0.19
Nodes (14): get_graph(), Retorna el grafo de conocimiento., build_full_graph(), build_graph(), parse_semarnat_key(), Path, core/graph_builder.py Knowledge Graph de proyectos SEMARNAT para visualización c, Construye el grafo de conocimiento a partir de proyectos.     Retorna dict con n (+6 more)

### Community 9 - "Selenium Driver & Harness Tests"
Cohesion: 0.17
Nodes (9): scrapers/gazette_scraper.py Descargador de Gacetas SINAT/SEMARNAT. Usa Selenium, make_chrome_driver(), Chrome configurado: descarga sin diálogo, sin visor PDF, CDP logging., tests/test_sinat_downloader_harness.py HARNESS PRINCIPAL — Contratos inmutables, Pruebas headful que verifican el número real de botones en SINAT.     Requieren, Espera con polling hasta que aparezcan botones en .descargas,         con fallba, 21PU2025H0155 debe tener 3 botones de descarga., 05CO2026I0001 debe tener 2 botones de descarga. (+1 more)

### Community 10 - "Pipeline Integration Tests"
Cohesion: 0.14
Nodes (13): tests/test_scraper_pipeline.py Tests con mocks — sin red, sin Chrome. Valida SSE, GET /api/scraper/extract-keys devuelve SSE con "complete" y CSV generado.      F, GET /api/scraper/run-pipeline ejecuta etapas de ingestión.      FIX: ASEAScraper, GET /api/status retorna JSON válido con campos requeridos., GET /api/corpus/pdfs retorna lista vacía cuando no hay PDFs., GazetteScraper._descargar_gacetas_ano_gen emite "progress" y "complete".      CO, ASEAScraper.descargar_gacetas_gen emite "progress" y "complete".      FIX: En Py, test_api_corpus_pdfs_empty() (+5 more)

## Knowledge Gaps
- **11 isolated node(s):** `Path`, `State`, `Session`, `MD_LAB Tab`, `App JavaScript client` (+6 more)
  These have ≤1 connection - possible missing edges or undocumented components.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `GazetteScraper` connect `Gazette Scraper Core` to `API Endpoints & Routing`, `Selenium Driver & Harness Tests`, `Pipeline Integration Tests`?**
  _High betweenness centrality (0.365) - this node is a cross-community bridge._
- **Why does `make_chrome_driver()` connect `Selenium Driver & Harness Tests` to `SEMARNAT Downloader Utility`, `Gazette Scraper Core`?**
  _High betweenness centrality (0.294) - this node is a cross-community bridge._
- **Why does `ASEAScraper` connect `ASEA Scraper Core` to `API Endpoints & Routing`, `Pipeline Integration Tests`, `Gazette Scraper Core`?**
  _High betweenness centrality (0.131) - this node is a cross-community bridge._
- **What connects `api/main.py FastAPI unificado para Zohar Intelligence v4. Endpoints SSE, GZip, c`, `Convierte un generador síncrono en stream SSE async.`, `Crea StreamingResponse SSE con headers correctos.` to the rest of the system?**
  _103 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `API Endpoints & Routing` be split into smaller, more focused modules?**
  _Cohesion score 0.06153846153846154 - nodes in this community are weakly interconnected._
- **Should `SEMARNAT Downloader Utility` be split into smaller, more focused modules?**
  _Cohesion score 0.08095238095238096 - nodes in this community are weakly interconnected._
- **Should `Gazette Scraper Core` be split into smaller, more focused modules?**
  _Cohesion score 0.11333333333333333 - nodes in this community are weakly interconnected._