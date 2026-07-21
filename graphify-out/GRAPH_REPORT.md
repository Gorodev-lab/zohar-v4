# Graph Report - .  (2026-07-21)

## Corpus Check
- Large corpus: 5217 files · ~3,370,450 words. Semantic extraction will be expensive (many Claude tokens). Consider running on a subfolder.

## Summary
- 1194 nodes · 2015 edges · 87 communities (71 shown, 16 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 71 edges (avg confidence: 0.51)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 60|Community 60]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 62|Community 62]]
- [[_COMMUNITY_Community 63|Community 63]]
- [[_COMMUNITY_Community 64|Community 64]]
- [[_COMMUNITY_Community 65|Community 65]]
- [[_COMMUNITY_Community 66|Community 66]]
- [[_COMMUNITY_Community 67|Community 67]]
- [[_COMMUNITY_Community 68|Community 68]]
- [[_COMMUNITY_Community 69|Community 69]]
- [[_COMMUNITY_Community 70|Community 70]]
- [[_COMMUNITY_Community 71|Community 71]]
- [[_COMMUNITY_Community 72|Community 72]]
- [[_COMMUNITY_Community 73|Community 73]]
- [[_COMMUNITY_Community 74|Community 74]]
- [[_COMMUNITY_Community 75|Community 75]]
- [[_COMMUNITY_Community 76|Community 76]]
- [[_COMMUNITY_Community 77|Community 77]]
- [[_COMMUNITY_Community 79|Community 79]]

## God Nodes (most connected - your core abstractions)
1. `$()` - 72 edges
2. `SecondBrainBuilder` - 54 edges
3. `RAGEngine` - 32 edges
4. `SemarnatDownloader` - 29 edges
5. `run_rsi_stream()` - 25 edges
6. `SemanticSearchEngine` - 25 edges
7. `GazetteScraper` - 24 edges
8. `generate_completion()` - 23 edges
9. `ASEAScraper` - 22 edges
10. `iter_pages_as_markdown()` - 21 edges

## Surprising Connections (you probably didn't know these)
- `Path` --uses--> `ZoharAgent`  [INFERRED]
  api/main.py → core/agent.py
- `Path` --uses--> `PDFDownloadVerifier`  [INFERRED]
  api/main.py → core/download_verifier.py
- `Path` --uses--> `RAGEngine`  [INFERRED]
  api/main.py → core/rag_engine.py
- `Path` --uses--> `SecondBrainBuilder`  [INFERRED]
  api/main.py → core/second_brain.py
- `Path` --uses--> `SemanticSearchEngine`  [INFERRED]
  api/main.py → core/semantic_search.py

## Import Cycles
- None detected.

## Communities (87 total, 16 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.05
Nodes (53): $(), activateModelChatTab(), activateTab(), appendLog(), drawSparkline(), escHtml(), executeRAGQuery(), initLlamaServerActions() (+45 more)

### Community 1 - "Community 1"
Cohesion: 0.05
Nodes (45): rag_query_endpoint(), rag_reindex_endpoint(), rag_search_endpoint(), Realiza una búsqueda semántica híbrida (BM25 + Vectorial) de notas del Second Br, Ejecuta el pipeline RAG completo:     Recuperación vectorial Top-K + Filtrado po, Búsqueda semántica vectorial pura de chunks con score de similitud., Indexa masivamente los documentos Markdown en extractions/ para el motor RAG., Indexa masivamente todas las notas del Second Brain para la búsqueda híbrida. (+37 more)

### Community 2 - "Community 2"
Cohesion: 0.05
Nodes (49): get_downloads_verification_status(), Retorna las estadísticas globales de verificación e integridad de descargas PDF., Audita todos los PDFs descargados en downloads/ y actualiza la tabla download_ma, verify_all_downloads_endpoint(), PDFDownloadVerifier, Any, Path, core/download_verifier.py Validador Híbrido Estricto de Integridad para Descarga (+41 more)

### Community 3 - "Community 3"
Cohesion: 0.05
Nodes (36): autolink_second_brain(), build_second_brain(), consume_pipeline_generator(), extract_project_info_from_text(), Extrae el nombre, la ubicación y el promovente de un proyecto en el texto     al, Ejecuta la sincronización completa del Second Brain de Obsidian., Ejecuta el auto-etiquetado YAML y la vinculación de wikilinks en el Second Brain, run_pipeline_generator() (+28 more)

### Community 4 - "Community 4"
Cohesion: 0.05
Nodes (44): _is_running(), rsi_start(), rsi_status(), core/config.py ============== Módulo centralizado de configuración, rutas y ejec, _call_llama_api(), extract_pdf_chunks(), extract_pdf_prefix(), extract_structured_metadata_with_llm() (+36 more)

### Community 5 - "Community 5"
Cohesion: 0.08
Nodes (35): extract_structured_batch(), extract_structured_project(), Endpoint para ejecutar la Extracción Estructurada Avanzada con LLM.     Persiste, Ejecuta la extracción estructurada en lote para múltiples proyectos pendientes., BaseModel, Realiza UPSERT de la evaluación estructurada extraída por LLM en PostgreSQL., upsert_project_evaluation(), query_gemini_api() (+27 more)

### Community 6 - "Community 6"
Cohesion: 0.11
Nodes (29): get_dw_pipeline_stats(), Retorna las estadísticas en tiempo real de la base de datos PostgreSQL., get_db_stats(), init_db_schema(), core/dw_pipeline.py Tubería Mínima Efectiva de Ingesta y Extracción para Zohar v, Devuelve estadísticas en tiempo real de la base de datos PostgreSQL., Crea las tablas promoventes y proyectos en PostgreSQL si no existen., Ejecuta la ingesta incremental de expedientes procesados (.json y .md) hacia Pos (+21 more)

### Community 7 - "Community 7"
Cohesion: 0.09
Nodes (23): ScraperLedger, clasificar_pdf_con_llm(), _classify_by_keyword(), download_pdf_via_requests(), extract_initial_pages_text(), make_chrome_driver(), mover_estudios_y_resolutivos(), Path (+15 more)

### Community 8 - "Community 8"
Cohesion: 0.12
Nodes (19): AcousticEncoder, Acoustic Signature Encoder.     Maps a 2D spectrogram (e.g., shape [1, frequency, Spatio-Temporal Trajectory Encoder for vessels.     Maps a sequence of GPS/AIS p, TrajectoryEncoder, LatentPredictor, JEPA Predictor model.     Predicts the representation of the target trajectory d, get_conapesca_permits_for_vessel(), get_dynamic_kinematic_modifier() (+11 more)

### Community 9 - "Community 9"
Cohesion: 0.08
Nodes (24): get_atomic_rsi_toggle_status(), get_cached_data_summary(), get_dw_status(), get_eval_questions(), get_llm_status(), get_model_status(), list_inference(), list_model_tools() (+16 more)

### Community 10 - "Community 10"
Cohesion: 0.11
Nodes (16): api_chat(), Endpoint de chat interactivo con el modelo activo.     Acepta: { "message": "..., core/agent.py Implementación del agente de IA interactivo de Zohar v4. Soporta L, Compila y actualiza todas las notas Markdown vinculadas en el Second Brain     y, Realiza una búsqueda semántica de alta precisión utilizando embeddings locales, Localiza un PDF en el corpus, extrae su texto página por página aplicando OCR hí, run_ocr_extraction(), run_second_brain_search() (+8 more)

### Community 11 - "Community 11"
Cohesion: 0.12
Nodes (20): Operación atómica de RSI: busca 1 ficha con metadatos incompletos o desconocidos, run_atomic_metadata_curation_step(), build_targeted_snippet(), core/text_utils.py ================== Utilidades de procesamiento de texto para, Construye un snippet determinista concatenando el encabezado (prefijo) del texto, Verifica la ejecución atómica de curaduría de metadatos., test_run_atomic_metadata_curation_step(), test_build_targeted_snippet_basic() (+12 more)

### Community 12 - "Community 12"
Cohesion: 0.11
Nodes (12): Diagnóstico aislado: navega, busca la clave, hace clic en el botón de descarga,, Diagnostico v2: replica EXACTA de los selectores reales de _descargar_clave_gen, Diagnostico v3: usa la clase REAL SemarnatDownloader (mismos selectores que ya f, Diagnostico v4: usa la clase real, pero en vez de mirar network logs, revisa si, Diagnostico v5: NO reimplementa nada de la logica de busqueda/clics. Usa el gene, make_chrome_driver(), scrapers/semarnat_downloader.py Motor principal de descarga SINAT/SEMARNAT. Chro, Click robusto con scroll, retry y fallback JS. (+4 more)

### Community 13 - "Community 13"
Cohesion: 0.14
Nodes (12): main(), Path, Executes the DDL schema.sql script to prepare database tables., Loads target claves from CSV or extracts them from gacetas., Queries SEMARNAT portal and downloads documents for missing files., Localiza el PDF de estudio para una clave SEMARNAT.         El downloader nombra, Localiza el archivo .md de extracción para una clave.         Puede ser: {clave}, Converts study PDFs to Markdown. (+4 more)

### Community 14 - "Community 14"
Cohesion: 0.15
Nodes (21): call_llama_server(), detect_base_indent(), extract_patch_window(), extract_python_block(), get_cycle_history(), log_jsonl(), make_backup(), Path (+13 more)

### Community 15 - "Community 15"
Cohesion: 0.14
Nodes (20): detect_active_backend(), generate_completion(), core/llm_client.py Capa de abstracción unificada para LLMs locales (llama-server, Detecta automáticamente qué proveedor de LLM está activo y disponible.     Prior, Genera una completación de chat con el backend de mayor prioridad activo.     Re, _build_prompt(), _build_system_prompt(), enrich_metadata_from_pdf() (+12 more)

### Community 16 - "Community 16"
Cohesion: 0.14
Nodes (18): _check_knockouts(), _chunk_text(), _fallback_report(), generate_report(), Path, core/inference_engine.py Motor de inferencia "Por Qué Sí / Por Qué No" usando Ge, Divide el texto en fragmentos superpuestos., Realiza búsquedas dirigidas (RAG local) para compilar el contexto más relevante. (+10 more)

### Community 17 - "Community 17"
Cohesion: 0.15
Nodes (13): Clasifica y renombra PDFs nuevos por clave SEMARNAT.      Regla de fallback posi, renombrar_archivos_por_clave(), Path, tests/test_sinat_downloader_harness.py HARNESS PRINCIPAL — Contratos inmutables, 3 PDFs sin keywords → resumen + estudio + resolutivo.          CONTRATO INMUTABL, 1 PDF sin keywords → estudio (índice 0 cuando n=1)., Si el nombre contiene keyword, prevalece sobre posición., Directorio vacío retorna listas vacías. (+5 more)

### Community 18 - "Community 18"
Cohesion: 0.11
Nodes (18): download_clave(), _event_stream(), extract_keys(), extract_pipeline_md(), r"""     SSE: Extrae claves SINAT del contenido de texto de las gacetas del año, SSE: Extrae texto Markdown de todos los PDFs en GACETAS_DIR (o subdirectorios se, SSE: Ejecuta el pipeline completo de ingestión.     Etapas: gacetas ASEA → gacet, SSE: Descarga los archivos del trámite (estudio, resumen, resolutivo) para una c (+10 more)

### Community 19 - "Community 19"
Cohesion: 0.17
Nodes (15): get_corpus_files_status(), Retorna el estado de conversión de archivos PDF a MD en todo el corpus     (estu, build_full_graph(), build_graph(), parse_semarnat_key(), Path, core/graph_builder.py Knowledge Graph de proyectos SEMARNAT para visualización c, Construye el grafo de conocimiento a partir de proyectos.     Retorna dict con n (+7 more)

### Community 20 - "Community 20"
Cohesion: 0.15
Nodes (8): CHARM, LinearProbe, Linear Probe for evaluating frozen representations of CHARM.     Used to demonst, Temporal Convolutional Network for encoding kinematics., Residual block for Temporal Convolutional Network (TCN) to handle time-series, Channel-Aware Representation Model (CHARM) for multimodal telemetry-metadata fus, TCNResidualBlock, TemporalConvNet

### Community 21 - "Community 21"
Cohesion: 0.15
Nodes (8): Updates the target encoder parameters via Exponential Moving Average (EMA)., Generates a binary mask of shape [Batch, SeqLen] with randomly masked blocks., Predictor model in the latent space. Predicts target representations      from m, Encoder for GPS trajectories. Converts coordinates and kinematics into      late, Trajectory Joint Embedding Predictive Architecture (T-JEPA) for GPS routes., TJEPA, TrajectoryEncoder, TrajectoryPredictor

### Community 22 - "Community 22"
Cohesion: 0.18
Nodes (12): api_classify_item(), Clasifica heurísticamente una clave o archivo sin uso de LLM., classify_item(), DocumentClassifier, Any, Path, core/classifier.py Clasificador heurístico determinístico (0% consumo LLM) para, Función helper para clasificar un elemento. (+4 more)

### Community 23 - "Community 23"
Cohesion: 0.14
Nodes (16): _determine_source(), download_md(), _extract_year_from_name(), get_gaceta_keys(), get_gaceta_keys_legacy(), get_gacetas_summary(), get_gacetas_summary_legacy(), get_inference() (+8 more)

### Community 24 - "Community 24"
Cohesion: 0.16
Nodes (13): Inicia el ciclo de ejecución del agente.         Retorna: (respuesta_final, tool, Ejecuta una consulta SQL de tipo SELECT en las tablas 'semarnat_projects' o, run_db_query(), ZoharAgent, tests/test_agent_tools.py Pruebas unitarias para las herramientas del agente y e, Valida que solo se permitan sentencias SELECT., Valida que se bloqueen palabras clave DDL/DML destructivas en subconsultas o en, Verifica que el agente detecte la etiqueta tool_call y la ejecute. (+5 more)

### Community 25 - "Community 25"
Cohesion: 0.18
Nodes (9): ASEAScraper, Path, scrapers/asea_scraper.py Descargador de Gacetas ASEA (sin Selenium — solo reques, Descarga todas las gacetas. Wrapper síncrono., Scraper de Gacetas ASEA (Agencia de Seguridad, Energía y Ambiente).     No requi, Retorna lista de gacetas disponibles:         [{"url": str, "year": int|None, "f, Extrae el año (20xx) de una cadena de texto., Generador SSE de descarga de gacetas ASEA.         Emite {"status": "progress"|" (+1 more)

### Community 26 - "Community 26"
Cohesion: 0.12
Nodes (15): tests/test_scrapers_2026.py Pruebas de inicialización y configuración (sin red,, ASEAScraper extrae el año de texto y URLs correctamente., GazetteScraper genera URL correcta para año 2026.      CONTRATO:         "ai=202, La URL del iframe incluye el año correctamente formateado., ASEAScraper guarda year_filter y output_dir correctamente.      CONTRATO:, Sin year_filter, ASEAScraper acepta todos los años., ASEAScraper crea directorio de salida., ASEAScraper tiene ASEA_INDEX_URL definida correctamente. (+7 more)

### Community 27 - "Community 27"
Cohesion: 0.17
Nodes (7): CFJEPA, MultiHorizonPredictor, Multi-horizon predictor for CF-JEPA. Takes context embedding and      temporal h, Asymmetric Context Encoder for processing the historical/past trajectory crop., Crop-based Forward JEPA (CF-JEPA) for forward multi-horizon trajectory forecasti, Updates the target encoder parameters via EMA., TemporalContextEncoder

### Community 28 - "Community 28"
Cohesion: 0.12
Nodes (15): tests/test_scraper_pipeline.py Tests con mocks — sin red, sin Chrome. Valida SSE, GET /api/scraper/extract-keys devuelve SSE con "complete" y CSV generado.      F, GET /api/scraper/run-pipeline ejecuta etapas de ingestión.      FIX: ASEAScraper, GET /api/status retorna JSON válido con campos requeridos., GET /api/corpus/pdfs retorna lista vacía cuando no hay PDFs., Verifica que extract-keys lee el contenido de las gacetas y extrae claves SINAT, Verifica los endpoints de resumen de gacetas y consulta de claves por gaceta par, ASEAScraper.descargar_gacetas_gen emite "progress" y "complete".      FIX: En Py (+7 more)

### Community 29 - "Community 29"
Cohesion: 0.17
Nodes (13): _atomic_rsi_worker_loop(), get_atomic_rsi_toggle_status(), api/routers/rsi.py ================== Endpoints de control para Auto-Mejora Recu, Ejecuta el ciclo de Auto-Mejora Recursiva (RSI) sobre semarnat_downloader.py, Retorna el estado activo/inactivo del Toggle de RSI Auto-Curaduría Atómica., Activa o desactiva el Toggle de RSI Auto-Curaduría Atómica desde el Dashboard UI, Background worker que ejecuta 1 iteración atómica de curaduría cada 30 segundos., _rsi_is_running() (+5 more)

### Community 30 - "Community 30"
Cohesion: 0.18
Nodes (6): Descarga una lista de bitácoras en batch., Descarga bitácoras desde un CSV., Descargador automático de documentos SINAT/SEMARNAT.     Usa Chrome + Selenium c, Wrapper síncrono. Consume _descargar_clave_gen, retorna último evento., Wrapper de reintentos sobre _descargar_clave_gen.          - Reintenta hasta max, SemarnatDownloader

### Community 31 - "Community 31"
Cohesion: 0.19
Nodes (6): Descarga bitácoras desde un CSV., Descargador automático de documentos SINAT/SEMARNAT.     Usa Chrome + Selenium c, Wrapper síncrono. Consume _descargar_clave_gen, retorna último evento., Wrapper de reintentos sobre _descargar_clave_gen.          - Reintenta hasta max, Descarga una lista de bitácoras en batch., SemarnatDownloader

### Community 32 - "Community 32"
Cohesion: 0.20
Nodes (7): DataFrame, DataQualityAuditor, Any, Runs specific quality audits on SEMARNAT projects:         1. Clave format valid, Pandas-based auditor that checks dataset health, enforces data types,      verif, postgres_upsert_method(), Custom upsert method for pandas.DataFrame.to_sql targeting PostgreSQL.

### Community 33 - "Community 33"
Cohesion: 0.24
Nodes (12): chronological_split(), get_h3_index(), get_mock_db_data(), get_real_db_data(), Splits the trajectory data chronologically per vessel to prevent temporal data l, Seeds the database with vessels and telemetry records if sparse or empty., Computes H3 index defensively supporting both v3 and v4 of h3 library., Queries real database tables (vessels, telemetry_records, obis_occurrences, cona (+4 more)

### Community 34 - "Community 34"
Cohesion: 0.23
Nodes (12): extract_claves_from_md(), get_cached_data_summary(), load_inference_cache(), main(), Path, Escanea todos los datos en disco y construye un diccionario     {clave: metadata, Carga todos los datos al Neo4j.     Retorna estadísticas del proceso., Retorna un resumen de todos los datos disponibles en disco,     sin necesidad de (+4 more)

### Community 35 - "Community 35"
Cohesion: 0.24
Nodes (10): get_graphify_betweenness(), Calcula el degree centrality normalizado del nodo `func_name` en el grafo     de, run_rsi(), main(), print_priority_table(), prioritize_targets(), Ordena los targets de mayor a menor betweenness centrality según graphify.     S, Imprime la tabla de prioridad de objetivos RSI. (+2 more)

### Community 36 - "Community 36"
Cohesion: 0.18
Nodes (11): _classify_by_keyword(), Detecta tipo por keywords en el nombre del archivo., tests/test_master_pipeline.py Test de integración del Plan Maestro Secuencial de, Verifica reglas de clasificación posicional y por keywords., Verifica la compilación del vault de Obsidian en el Second Brain., Verifica la inicialización del motor de búsqueda semántica RAG., Verifica los endpoints principales del API backend., test_api_status_and_health_endpoints() (+3 more)

### Community 37 - "Community 37"
Cohesion: 0.18
Nodes (11): download_pdf_via_requests(), extract_initial_pages_text(), mover_estudios_y_resolutivos(), Path, Descarga PDF usando requests con cookies de sesión Selenium., Espera activa inteligente de dos fases.     Fase 1: detectar inicio (hasta 30s)., Mueve evitando colisiones: agrega _v2, _v3... si ya existe., Clasifica por prefijo 'estudio.' / 'resolutivo.' y mueve. (+3 more)

### Community 38 - "Community 38"
Cohesion: 0.20
Nodes (6): _atomic_rsi_worker_loop(), LiveUpdateBroadcaster, Background worker que ejecuta 1 iteración atómica de curaduría cada 30 segundos., Activa o desactiva el Toggle de RSI Auto-Curaduría Atómica desde el Dashboard UI, toggle_atomic_rsi(), Queue

### Community 39 - "Community 39"
Cohesion: 0.22
Nodes (6): Dataset, MaritimeMultimodalDataset, Generates a reproducible pseudo-embedding from text for testing/fallback., Multimodal Dataset for LOGR containing GPS trajectories, text permissions metada, trajectories: List of numpy arrays, each of shape [vessel_points, 5], Generates real text embedding using Gemini gemini-embedding-2 if API key is pres

### Community 40 - "Community 40"
Cohesion: 0.27
Nodes (10): generate_programmatic_graph(), main(), merge_graphs(), Path, Runs the graphify CLI on the temp_corpus and returns the resulting graph dict., Merges semantic nodes/links extracted by graphify CLI into the programmatic grap, Loads the final graph JSON into public.knowledge_graph and Supabase., Programmatic fallback: Queries all tables and builds the complete knowledge grap (+2 more)

### Community 41 - "Community 41"
Cohesion: 0.29
Nodes (8): MTSJEPA, Multi-resolution Time Series JEPA (MTS-JEPA). Downsamples the trajectory     to, main(), test_cf_jepa_and_mts_jepa(), test_charm(), test_dataset(), test_t_jepa(), test_vicreg_loss()

### Community 42 - "Community 42"
Cohesion: 0.20
Nodes (10): api_status(), get_llama_status(), manage_server(), Controlador unificado de servicios: permite iniciar, detener o reiniciar     Lla, Retorna métricas del sistema: CPU, RAM, disco, uptime, Second Brain y estado de, Verifica si llama-server está activo en el puerto 8083 y responde a /health., Inicia el servidor llama-server usando el script local., Detiene el servidor llama-server matando su PID y enviando SIGTERM. (+2 more)

### Community 43 - "Community 43"
Cohesion: 0.20
Nodes (10): auto_fix_window_indentation(), extract_function_block(), fix_llm_indentation(), Extrae el bloque completo de un método usando AST.     Retorna (source_block, st, Reemplaza el bloque de la función `func_name` con `new_block`.     Preserva todo, Red de seguridad: des-indenta completamente el bloque devuelto por el LLM     co, Intenta reparar desalineaciones e 'indent jumps' producidos por el LLM dentro de, Valida que el código sea Python sintácticamente correcto. (+2 more)

### Community 44 - "Community 44"
Cohesion: 0.20
Nodes (8): GazetteScraper, scrapers/gazette_scraper.py Descargador de Gacetas SINAT/SEMARNAT. Usa Selenium, Intenta extraer clave SEMARNAT válida desde el nombre del archivo., Scraper de Gacetas Ecológicas publicadas en el portal SINAT.     Navega el ifram, GazetteScraper._descargar_gacetas_ano_gen emite "progress" y "complete".      CO, test_gazette_scraper_generator(), GazetteScraper crea el directorio de salida al inicializarse., test_gazette_scraper_output_dir_creation()

### Community 45 - "Community 45"
Cohesion: 0.20
Nodes (8): element_exists(), extract_metadata_from_dom(), extract_pdf_urls_from_network_log(), Intenta extraer la clave SINAT del DOM de la página., Verifica existencia de elemento sin lanzar excepción., Extrae URLs de PDF desde CDP performance logs., Intenta extraer metadatos estructurados directamente del DOM de la página de res, Generador SSE. Emite dicts con:             {"status": str, "msg": str, "level":

### Community 46 - "Community 46"
Cohesion: 0.20
Nodes (8): element_exists(), extract_metadata_from_dom(), extract_pdf_urls_from_network_log(), Verifica existencia de elemento sin lanzar excepción., Extrae URLs de PDF desde CDP performance logs., Intenta extraer metadatos estructurados directamente del DOM de la página de res, Generador SSE. Emite dicts con:             {"status": str, "msg": str, "level":, Intenta extraer la clave SINAT del DOM de la página.

### Community 47 - "Community 47"
Cohesion: 0.20
Nodes (9): tests/test_cache_strategy.py Pruebas unitarias y de integración para la estrateg, Verifica que el reporte de inferencia se sirve desde caché si el .md de origen n, Verifica que si el .md ya existe y es más nuevo que el PDF de origen,     /strea, Crea directorios temporales y los asocia a la API., Verifica que la caché del grafo se sirve si no hay cambios,     y se invalida re, temp_dirs(), test_extraction_sse_cache(), test_graph_cache_reactive_invalidation() (+1 more)

### Community 48 - "Community 48"
Cohesion: 0.25
Nodes (8): build_prompt(), Construye el prompt de diagnóstico para el LLM.     IMPORTANTE: solo se muestra, get_second_brain_context(), Path, core/rsi_brain.py Conexión bidireccional entre el motor RSI (auto_improver.py) y, Busca notas en second_brain/ relevantes para el archivo y función objetivo., Guarda automáticamente una lección aprendida en second_brain/03_Inferences/rsi_l, save_rsi_learning()

### Community 49 - "Community 49"
Cohesion: 0.22
Nodes (4): Generador SSE para descarga de gacetas.         Emite {"status": "progress"|"com, Registra la gaceta descargada en Supabase (si está configurado)., Genera URL del iframe para el año especificado., Detecta si el contenido PDF está en el body principal o en un iframe.         Re

### Community 50 - "Community 50"
Cohesion: 0.36
Nodes (4): DataDirectoryHandler, invalidate_redis_cache(), Invalida una llave específica en Redis si está disponible., FileSystemEventHandler

### Community 51 - "Community 51"
Cohesion: 0.25
Nodes (8): download_remaining(), download_remaining_generator(), Genera eventos SSE para descargar secuencialmente las claves que faltan en el co, SSE: Descarga secuencialmente todos los estudios PDF de claves registradas que f, Actualiza o inserta los metadatos de un proyecto en el archivo CSV data/claves_{, Inserta o actualiza los metadatos de un proyecto directamente en la base de dato, update_csv_metadata(), upsert_project_db()

### Community 52 - "Community 52"
Cohesion: 0.39
Nodes (3): VICReg Loss (Variance-Covariance-Invariance Regularization) for preventing     r, VICRegLoss, Tensor

### Community 53 - "Community 53"
Cohesion: 0.32
Nodes (5): Pruebas headful que verifican el número real de botones en SINAT.     Requieren, Espera con polling hasta que aparezcan botones en .descargas,         con fallba, 21PU2025H0155 debe tener 3 botones de descarga., 05CO2026I0001 debe tener 2 botones de descarga., TestSINATButtonCount

### Community 54 - "Community 54"
Cohesion: 0.33
Nodes (6): Base, extract_entities(), persist_to_db(), Lee la nota de la entidad y extrae los campos requeridos en formato JSON., Guarda los metadatos extraídos en la base de datos PostgreSQL utilizando SQLAlch, SemarnatProject

### Community 55 - "Community 55"
Cohesion: 0.33
Nodes (6): batch_summaries_endpoint(), live_updates(), Ejecuta un lote de auto-resúmenes de PDFs pendientes usando el generador batch M, SSE: Envía notificaciones automáticas al dashboard en tiempo real cuando hay cam, Request, StreamingResponse

### Community 56 - "Community 56"
Cohesion: 0.33
Nodes (5): _compute_sha256(), _cosine_similarity(), core/semantic_search.py Motor de búsqueda semántica para las notas del Second Br, Calcula el hash SHA256 de un texto., Calcula la similitud de coseno entre dos vectores.

### Community 57 - "Community 57"
Cohesion: 0.47
Nodes (5): calculate_item_score(), main(), normalize_str(), Calcula el score de precisión (0.0 a 1.0) para un proyecto individual.     Llave, Normaliza un string para comparación tolerante:     - Minúsculas     - Quita ace

### Community 58 - "Community 58"
Cohesion: 0.33
Nodes (3): Path, Descarga todas las gacetas de un año. Retorna lista de PDFs., Extrae enlaces PDF válidos del HTML del iframe.

### Community 59 - "Community 59"
Cohesion: 0.50
Nodes (4): check_wiki_links(), main(), Path, Retorna una lista de todas las notas enlazadas mediante wiki-links.

### Community 60 - "Community 60"
Cohesion: 0.70
Nodes (4): generate_csv_data(), generate_readme(), main(), upload_to_supabase()

### Community 62 - "Community 62"
Cohesion: 0.33
Nodes (4): get_project_graph_context(), Query Neo4j (or local graph cache fallback) to retrieve connections/relations fo, Detiene la extracción SSE activa., stop_single()

### Community 63 - "Community 63"
Cohesion: 0.50
Nodes (4): list_md(), Retorna el path relativo a BASE_DIR, o el path absoluto si no está dentro de él., Lista archivos .md del directorio de extracciones., _safe_relative_path()

## Knowledge Gaps
- **12 isolated node(s):** `Path`, `Path`, `Any`, `Path`, `Path` (+7 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **16 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `SecondBrainBuilder` connect `Community 3` to `Community 32`, `Community 36`, `Community 5`, `Community 38`, `Community 6`, `Community 9`, `Community 10`, `Community 11`, `Community 13`, `Community 48`, `Community 50`, `Community 51`, `Community 19`, `Community 55`, `Community 23`, `Community 24`?**
  _High betweenness centrality (0.112) - this node is a cross-community bridge._
- **Why does `ASEAScraper` connect `Community 25` to `Community 3`, `Community 38`, `Community 9`, `Community 50`, `Community 55`, `Community 23`, `Community 26`, `Community 28`?**
  _High betweenness centrality (0.057) - this node is a cross-community bridge._
- **Why does `generate_completion()` connect `Community 15` to `Community 1`, `Community 3`, `Community 5`, `Community 7`, `Community 9`, `Community 10`, `Community 11`, `Community 12`, `Community 16`, `Community 48`, `Community 54`, `Community 24`?**
  _High betweenness centrality (0.050) - this node is a cross-community bridge._
- **Are the 10 inferred relationships involving `SecondBrainBuilder` (e.g. with `DataDirectoryHandler` and `LiveUpdateBroadcaster`) actually correct?**
  _`SecondBrainBuilder` has 10 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `RAGEngine` (e.g. with `DataDirectoryHandler` and `LiveUpdateBroadcaster`) actually correct?**
  _`RAGEngine` has 6 INFERRED edges - model-reasoned connections that need verification._
- **Are the 8 inferred relationships involving `SemarnatDownloader` (e.g. with `DataDirectoryHandler` and `LiveUpdateBroadcaster`) actually correct?**
  _`SemarnatDownloader` has 8 INFERRED edges - model-reasoned connections that need verification._
- **What connects `api/main.py FastAPI unificado para Zohar Intelligence v4. Endpoints SSE, GZip, c`, `Invalida una llave específica en Redis si está disponible.`, `Retorna el path relativo a BASE_DIR, o el path absoluto si no está dentro de él.` to the rest of the system?**
  _438 weakly-connected nodes found - possible documentation gaps or missing edges._