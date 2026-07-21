# Tareas de Implementación — RSI Atómico & Contratos de Etapa 🌌

- [x] **Componente 1: Motor Backend de RSI Atómico (`api/` & `core/`)**
  - [x] Añadir la función `run_atomic_metadata_curation_step()` en `core/rsi_brain.py`.
  - [x] Implementar `build_targeted_snippet` en `core/text_utils.py` para snippet determinista con regex de estados y promoventes.
  - [x] Conectar endpoints `GET /api/rsi/toggle-status` y `POST /api/rsi/toggle` en `api/main.py`.
  - [x] Integrar el background worker loop `_atomic_rsi_worker_loop()` con emisión de eventos SSE.

- [x] **Componente 2: Matriz de Contratos de Etapa & Auto-Healing (`core/`)**
  - [x] Crear `core/stage_contracts.py` y los clasificadores por etapas.

- [x] **Componente 3: Toggle UI en el Dashboard (`dashboard/`)**
  - [x] Añadir switch/toggle "RSI Auto-Curaduría" en `dashboard/index.html`.
  - [x] Conectar handlers de Toggle y actualización en vivo SSE en `dashboard/static/app.js`.

- [x] **Componente 4: Robustez y Cuarentena de PDFs (`core/`)**
  - [x] Migración de Map-Reduce a Single-Pass en `core/pdf_summarizer.py`.
  - [x] Cuarentena automática de PDFs corruptos/ilegibles a subdirectorio `_corruptos/`.

- [x] **Componente 5: Pruebas & Verificación**
  - [x] Crear y ejecutar `tests/test_atomic_rsi_and_contracts.py`.
  - [x] Crear y ejecutar `tests/test_text_utils.py`.
  - [x] Ejecutar pytest para validar la suite completa (**61 passed, 2 deselected**).
