# Tareas de Implementación — RSI Atómico & Contratos de Etapa 🌌

- [x] **Componente 1: Motor Backend de RSI Atómico (`api/` & `core/`)**
  - [x] Añadir la función `run_atomic_metadata_curation_step()` en `core/rsi_brain.py`.
  - [x] Implementar UUID de sesión y prefijo dinámico en `RLMHarness`
- [x] Agregar soporte de TTL (Time-To-Live) en escrituras a Redis desde `RLMHarness`
- [x] Crear la heurística de estimación de tokens en `auto_improver.py`
- [x] Desarrollar la lógica de sanitización y recorte inteligente para outputs de `pytest`
- [x] Integrar el control del presupuesto de tokens dinámico en `build_prompt`
- [x] Escribir nuevas pruebas unitarias en `tests/test_token_budget.py`
- [x] Agregar pruebas de sesión/TTL de Redis en `tests/test_rlm_harness.py`
- `[x]` Ejecutar suite completa de validación (pytest) para confirmar sanidad

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
