# Plan de Mejora del Frontend — Zohar v4 Dashboard

> Enfoque: AUDITAR y MEJORAR lo existente (incluido el tab HERRAMIENTAS
> recién añadido), no crear arquitectura nueva. Todo respeta el estilo
> Glassmorphism y la SPA actual (index.html + static/app.js + static/app.css).

## CORRECCIÓN DE AUDITORÍA (importante, leer primero)

La primer pasada de auditoría (script de cruce HTML↔JS↔CSS) marcó como
"CSS muerto / UI a medias" a varias clases que EN REALIDAD YA SE USAN
vía interpolación de template (`x--${var}`). Verificación manual en el código
confirma que los siguientes ítems de la FASE 1 original YA ESTABAN HECHOS:

- INFERENCE_LAB veredicto: `renderInferenceReport` emite
  `verdict-card--${v}` y `verdict-label--${v}` (app.js ~línea 1032).
- WORKFLOW kanban: `renderKanbanBoard` (app.js línea 1817) emite
  `kanban-card kanban-card--${state}`.
- GRAFO_RED: `renderGraph` usa `graph-node`/`graph-link`; el detalle de nodo
  emite `neo4j-entity-card--${verdict}` (app.js línea 2190).
- TOASTS: `showToast` asigna `toast toast--${type}` (app.js línea 77).

Por lo tanto NO se "arregló" nada de eso: funcionaba. El único trabajo
real de esta ronda fue el hueco en el tab HERRAMIENTAS (mi código)
+ poda de CSS sin uso + cache-bust. Ver abajo.

## Trabajo REAL ejecutado (esta sesión)

### R1 — Clasificador colorea badge de fuente (HERRAMIENTAS)
El `runToolsClassify` original solo hacia `JSON.stringify` del resultado y no
coloreaba. Ahora, si `source` es SEMARNAT/ASEA, appenda un
`badge badge--semarnat` / `badge--asea` (clases YA existentes y usadas
en el log del scraper). Usa la arquitectura que ya teníamos; no inventa nada.
- Archivo: dashboard/static/app.js — función `runToolsClassify`.
- Verificado: endpoint /api/classifier/classify devuelve source=SEMARNAT;
  la clase badge--semarnat existe y se aplica.

### R2 — Poda de CSS sin uso real
`badge--bio`, `badge--geo`, `badge--law` estaban definidas en app.css
pero NINGÚN JS las emite (el clasificador solo devuelve SEMARNAT/ASEA).
Borradas de app.css (~línea 941). Las demás `badge--*` se conservan.

### R3 — Cache-bust de app.js
Cada edición de app.js requería subir `?v=` manualmente. Subido a
`?v=4.0.5` en index.html para forzar recarga del navegador.

## Verificación aplicada
- `node --check dashboard/static/app.js` → SYNTAX OK.
- Endpoint /api/classifier/classify probado vivo (devuelve source/sector/estado).
- index.html servido confirma `app.js?v=4.0.5`.
- Revisión visual en http://127.0.0.1:8004 → HERRAMIENTAS (pendiente tuya).

## Conclusión de la auditoría de frontend
El frontend está SUSTANCIALMENTE COMPLETO y bien cableado. No hay UI a
medias reales: el CSS "muerto" inicial era ruido de detección. Las únicas
mejoras genuinas fueron las 3 de arriba (R1/R2/R3), todas menores y
dentro de la arquitectura existente.

## Siguiente paso sugerido (si se quiere seguir)
En lugar de "completar UI", la auditoría útil ahora es:
- Backend→Frontend coverage: qué endpoints del backend (70) siguen sin
  UI. Los 6 del tab HERRAMIENTAS ya están; restan internos/batch que
  probablemente no necesitan UI (p.ej. /api/extract/batch es lote, no interactivo).
- Robustez: manejo de errores 4xx/5xx en los fetch (hoy varios
  asumen 200). Esto SÍ es mejora real y está dentro de la arquitectura.
- Accesibilidad: roles/aria ya presentes; revisar foco visible.

Ninguno de esos es "arquitectura nueva"; todos mejoran lo que hay.
