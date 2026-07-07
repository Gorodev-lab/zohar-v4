/**
 * Zohar Intelligence v4 — app.js
 * Vanilla JS + D3 v7 (CDN). Zero frameworks. Zero deps locales.
 * 4 tabs: CORPUS_PDF, MD_LAB, GRAFO_RED, INFERENCE_LAB
 */

'use strict';

/* =========================================================================
   ESTADO GLOBAL
   ========================================================================= */
const State = {
  activeTab:    'CORPUS_PDF',
  pdfs:         [],
  mds:          [],
  graph:        null,
  selectedPdf:  null,
  selectedMd:   null,
  sseSource:    null,
  systemStatus: null,
};

/* =========================================================================
   UTILIDADES
   ========================================================================= */
const $ = (sel, ctx = document) => ctx.querySelector(sel);
const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

function ts() {
  const d = new Date();
  return d.toTimeString().slice(0, 8);
}

function fmtSize(bytes) {
  if (bytes < 1024)       return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)}MB`;
}

function appendLog(consoleEl, msg, level = 'info') {
  if (!consoleEl) return;
  const line = document.createElement('div');
  line.className = `log-line log-line--${level}`;
  line.innerHTML = `<span class="log-line__ts">[${ts()}]</span><span class="log-line__msg">${escHtml(msg)}</span>`;
  consoleEl.appendChild(line);
  consoleEl.scrollTop = consoleEl.scrollHeight;
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function setProgress(barEl, pct) {
  if (!barEl) return;
  const fill = barEl.querySelector('.progress-bar__fill');
  if (fill) fill.style.width = `${Math.min(100, pct)}%`;
}

/* =========================================================================
   TAB NAVIGATION
   ========================================================================= */
function initTabs() {
  $$('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tabId = btn.dataset.tab;
      activateTab(tabId);
    });
  });
}

function activateTab(tabId) {
  State.activeTab = tabId;
  $$('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tabId));
  $$('.tab-panel').forEach(p => p.classList.toggle('active', p.id === `panel-${tabId}`));

  if (tabId === 'CORPUS_PDF')    loadCorpus();
  if (tabId === 'MD_LAB')        loadMdList();
  if (tabId === 'GRAFO_RED')     loadGraph();
  if (tabId === 'INFERENCE_LAB') loadInferenceList();
  if (tabId === 'SECOND_BRAIN') {
    if (State.systemStatus) updateSecondBrainUI(State.systemStatus);
    loadWikiNotesList();
  }
  if (tabId === 'WORKFLOW')      loadWorkflowGacetas();
}

/* =========================================================================
   SYSTEM STATUS
   ========================================================================= */
async function loadStatus() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    State.systemStatus = d;

    const cpuEl  = $('#status-cpu');
    const ramEl  = $('#status-ram');
    const dotEl  = $('#status-dot');

    if (cpuEl)  cpuEl.textContent = `CPU:${d.cpu_pct}%`;
    if (ramEl)  ramEl.textContent = `RAM:${d.ram_pct}%`;
    if (dotEl)  dotEl.parentElement.classList.add('status-indicator--active');

    // Actualizar contadores del Second Brain si existe
    if (d.second_brain) {
      updateSecondBrainUI(d);
    }
  } catch (err) {
    console.error('Error cargando estatus:', err);
    const dotEl = $('#status-dot');
    if (dotEl) dotEl.parentElement.classList.remove('status-indicator--active');
  }
}

/* =========================================================================
   TAB 1 — CORPUS_PDF
   ========================================================================= */
async function loadCorpus() {
  try {
    const r = await fetch('/api/corpus/pdfs');
    const d = await r.json();
    State.pdfs = d.pdfs || [];
    renderPdfList();
    const metaEl = $('#corpus-meta');
    if (metaEl) metaEl.textContent = `${State.pdfs.length} archivos`;
  } catch (e) {
    console.error('loadCorpus:', e);
  }
}

function renderPdfList() {
  const listEl = $('#pdf-list');
  if (!listEl) return;
  listEl.innerHTML = '';

  if (!State.pdfs.length) {
    listEl.innerHTML = `<li class="file-item"><span class="file-item__name text-muted">[ corpus vacío ]</span></li>`;
    return;
  }

  // Agrupar por carpeta
  const groups = {};
  State.pdfs.forEach(p => {
    if (!groups[p.folder]) groups[p.folder] = [];
    groups[p.folder].push(p);
  });

  Object.entries(groups).forEach(([folder, files]) => {
    const header = document.createElement('li');
    header.className = 'sidebar__section-label';
    header.style.cssText = 'list-style:none; padding:6px 8px; margin-top:4px;';
    header.textContent = `─ ${folder.toUpperCase()} (${files.length})`;
    listEl.appendChild(header);

    files.forEach(pdf => {
      const li = document.createElement('li');
      li.className = 'file-item';
      li.id = `pdf-${btoa(pdf.name).replace(/=/g, '')}`;
      li.innerHTML = `
        <span class="file-item__name" title="${escHtml(pdf.name)}">${escHtml(pdf.name)}</span>
        <span class="file-item__badge">${pdf.size_mb}MB</span>
      `;
      li.addEventListener('click', () => selectPdf(pdf, li));
      listEl.appendChild(li);
    });
  });
}

function selectPdf(pdf, li) {
  State.selectedPdf = pdf;
  $$('.file-item').forEach(el => el.classList.remove('selected'));
  li.classList.add('selected');

  const nameEl = $('#pdf-selected-name');
  if (nameEl) nameEl.textContent = pdf.name;

  const metaEl = $('#pdf-selected-meta');
  if (metaEl) metaEl.textContent = `${pdf.folder} · ${pdf.size_mb}MB`;
}

function initCorpusActions() {
  const btnExtract = $('#btn-extract-pdf');
  const btnStop    = $('#btn-stop-pdf');
  const logEl      = $('#pdf-log');
  const progressEl = $('#pdf-progress');
  const viewerEl   = $('#pdf-viewer');

  if (btnExtract) {
    btnExtract.addEventListener('click', () => {
      if (!State.selectedPdf) {
        appendLog(logEl, 'Selecciona un PDF primero', 'warn');
        return;
      }

      if (State.sseSource) {
        State.sseSource.close();
        State.sseSource = null;
      }

      if (viewerEl) viewerEl.textContent = '';

      // Limpiar cualquier botón de descarga previo
      const prevDlBtn = $('#btn-download-md-corpus');
      if (prevDlBtn) prevDlBtn.remove();

      appendLog(logEl, `Extrayendo: ${State.selectedPdf.name}`, 'info');
      setProgress(progressEl, 0);

      const url = `/stream/single?pdf_name=${encodeURIComponent(State.selectedPdf.name)}`;
      const es  = new EventSource(url);
      State.sseSource = es;

      btnExtract.disabled = true;
      if (btnStop) btnStop.disabled = false;

      es.onmessage = (e) => {
        const evt = JSON.parse(e.data);
        if (evt.pct !== undefined) setProgress(progressEl, evt.pct);

        if (evt.status === 'progress' && evt.md) {
          if (viewerEl) viewerEl.textContent += evt.md;
          appendLog(logEl, `Pág ${evt.page}/${evt.total} ${evt.is_scanned ? '[SCAN]' : ''}`, 'info');
        } else if (evt.status === 'saved') {
          // MD guardado en servidor — mostrar botón de descarga
          appendLog(logEl, evt.msg, 'ok');
          _showMdDownloadButton(evt.md_name, btnExtract.parentElement || logEl.parentElement);
        } else if (evt.status === 'complete') {
          appendLog(logEl, evt.msg, 'ok');
          es.close();
          btnExtract.disabled = false;
          if (btnStop) btnStop.disabled = true;
          setProgress(progressEl, 100);
        } else if (evt.status === 'error') {
          appendLog(logEl, evt.msg, 'error');
          es.close();
          btnExtract.disabled = false;
          if (btnStop) btnStop.disabled = true;
        }
      };

      es.onerror = () => {
        appendLog(logEl, 'Conexión SSE perdida', 'error');
        es.close();
        btnExtract.disabled = false;
        if (btnStop) btnStop.disabled = true;
      };
    });
  }

  if (btnStop) {
    btnStop.disabled = true;
    btnStop.addEventListener('click', async () => {
      if (State.sseSource) State.sseSource.close();
      if (State.selectedPdf) {
        await fetch(`/stop_single?pdf_name=${encodeURIComponent(State.selectedPdf.name)}`);
      }
      appendLog(logEl, 'Extracción detenida', 'warn');
      if (btnExtract) btnExtract.disabled = false;
      btnStop.disabled = true;
    });
  }
}

/**
 * Muestra un botón de descarga del .md junto a los controles de extracción.
 * @param {string} mdName  - nombre del archivo .md
 * @param {Element} parent - contenedor donde insertar el botón
 */
function _showMdDownloadButton(mdName, parent) {
  if (!mdName || !parent) return;
  // Evitar duplicados
  const prev = document.getElementById('btn-download-md-corpus');
  if (prev) prev.remove();

  const btn = document.createElement('a');
  btn.id        = 'btn-download-md-corpus';
  btn.className = 'btn btn-sm btn-ok';
  btn.href      = `/api/md/download?filename=${encodeURIComponent(mdName)}`;
  btn.download  = mdName;
  btn.textContent = `⬇ Descargar ${mdName}`;
  btn.style.cssText = 'display:inline-block; margin-top:8px; text-decoration:none;';
  parent.appendChild(btn);
}

/* =========================================================================
   TAB 2 — MD_LAB
   ========================================================================= */
async function loadMdList() {
  try {
    const r = await fetch('/api/md/list');
    const d = await r.json();
    State.mds = d.mds || [];
    renderMdList();
    const metaEl = $('#md-meta');
    if (metaEl) metaEl.textContent = `${State.mds.length} documentos`;
  } catch (e) {
    console.error('loadMdList:', e);
  }
}

function renderMdList() {
  const listEl = $('#md-list');
  if (!listEl) return;
  listEl.innerHTML = '';

  if (!State.mds.length) {
    listEl.innerHTML = `<li class="file-item"><span class="file-item__name text-muted">[ sin documentos MD ]</span></li>`;
    return;
  }

  State.mds.forEach(md => {
    const li = document.createElement('li');
    li.className = 'file-item';
    li.innerHTML = `
      <span class="file-item__name" title="${escHtml(md.name)}">${escHtml(md.name)}</span>
      <span class="file-item__badge">${fmtSize(md.size_bytes)}</span>
      <a
        class="file-item__dl-btn"
        href="/api/md/download?filename=${encodeURIComponent(md.name)}"
        download="${escHtml(md.name)}"
        title="Descargar ${escHtml(md.name)}"
        onclick="event.stopPropagation()"
      >⬇</a>
    `;
    li.addEventListener('click', () => selectMd(md, li));
    listEl.appendChild(li);
  });
}

async function selectMd(md, li) {
  State.selectedMd = md;
  $$('.file-item').forEach(el => el.classList.remove('selected'));
  li.classList.add('selected');

  const viewerEl  = $('#md-viewer-content');
  const badgesEl  = $('#md-badges');
  const headerEl  = $('#md-selected-name');

  if (headerEl) headerEl.textContent = md.name;
  if (viewerEl) viewerEl.textContent = 'Cargando...';
  if (badgesEl) badgesEl.innerHTML = '';

  try {
    const r = await fetch(`/api/md/read?filename=${encodeURIComponent(md.name)}`);
    const d = await r.json();
    if (viewerEl) viewerEl.textContent = d.content;

    // Detectar badges en el contenido
    if (badgesEl) {
      const content = d.content;
      const badges = [];
      if (/latitud|longitud|UTM|coordenadas/i.test(content)) badges.push('GEO');
      if (/NOM-\d+|LGEEPA|artículo/i.test(content))          badges.push('LAW');
      if (/especie|flora|fauna|hábitat/i.test(content))       badges.push('BIO');

      badges.forEach(b => {
        const span = document.createElement('span');
        span.className = `badge badge--${b.toLowerCase()}`;
        span.textContent = b;
        badgesEl.appendChild(span);
      });
    }
  } catch (e) {
    if (viewerEl) viewerEl.textContent = `Error: ${e.message}`;
  }
}

/* =========================================================================
   TAB 3 — GRAFO_RED (D3 v7)
   ========================================================================= */
async function loadGraph() {
  const containerEl = $('#graph-container');
  if (!containerEl) return;

  containerEl.innerHTML = '<div style="padding:16px; color: var(--color-text-muted);">Cargando grafo...</div>';

  try {
    const r = await fetch('/api/graph?format=compact');
    const d = await r.json();
    State.graph = d;
    renderGraph(d, containerEl);
    renderGraphMetrics(d);
  } catch (e) {
    containerEl.innerHTML = `<div style="padding:16px; color: var(--color-status-alert);">Error: ${escHtml(e.message)}</div>`;
  }
}

function renderGraph(data, containerEl) {
  containerEl.innerHTML = '';

  if (!window.d3) {
    containerEl.innerHTML = '<div style="padding:16px; color:var(--color-status-alert);">D3 no disponible (carga CDN pendiente)</div>';
    return;
  }

  const schema = data.schema || {};
  const nodeFields = schema.nodes || ['i','t','l','st','yr','deg','com'];
  const IDX = { i:0, t:1, l:2, st:3, yr:4, deg:5, com:6 };

  const nodes = data.nodes.map(n => ({
    id:     n[IDX.i],
    type:   n[IDX.t],
    label:  n[IDX.l],
    color:  n[IDX.st] || '#FFB000',
    year:   n[IDX.yr],
    degree: n[IDX.deg] || 1,
    com:    n[IDX.com] || 0,
  }));

  const nodeById = Object.fromEntries(nodes.map(n => [n.id, n]));
  const links = data.links.map(l => ({
    source: nodes[l[0]]?.id,
    target: nodes[l[1]]?.id,
    rel:    schema.rel_map?.[l[2]] || '',
  })).filter(l => l.source && l.target);

  const W = containerEl.clientWidth  || 900;
  const H = containerEl.clientHeight || 600;

  const svg = d3.select(containerEl)
    .append('svg')
    .attr('id', 'graph-svg')
    .attr('width', W)
    .attr('height', H);

  const g = svg.append('g');

  // Zoom
  svg.call(d3.zoom()
    .scaleExtent([0.1, 8])
    .on('zoom', e => g.attr('transform', e.transform)));

  // Tooltip
  const tooltip = d3.select(containerEl)
    .append('div')
    .attr('class', 'graph-tooltip');

  // Simulación
  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(80).strength(0.5))
    .force('charge', d3.forceManyBody().strength(-120))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide(d => Math.sqrt(d.degree) * 6 + 10));

  // Links
  const link = g.append('g')
    .selectAll('line')
    .data(links)
    .join('line')
    .attr('class', 'graph-link')
    .attr('stroke-width', 1);

  // Nodos
  const node = g.append('g')
    .selectAll('g')
    .data(nodes)
    .join('g')
    .attr('class', 'graph-node')
    .call(d3.drag()
      .on('start', (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on('drag',  (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on('end',   (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));

  node.append('circle')
    .attr('r', d => Math.max(4, Math.sqrt(d.degree) * 4))
    .attr('fill', d => d.color)
    .attr('fill-opacity', 0.85)
    .attr('stroke', d => d.color)
    .attr('stroke-width', 1);

  node.append('text')
    .attr('dy', d => Math.sqrt(d.degree) * 4 + 10)
    .attr('text-anchor', 'middle')
    .text(d => d.label.length > 14 ? d.label.slice(0, 12) + '..' : d.label);

  // Hover tooltip
  node.on('mousemove', (e, d) => {
    tooltip
      .style('display', 'block')
      .style('left', e.clientX + 12 + 'px')
      .style('top',  e.clientY - 8 + 'px')
      .html(`
        <div class="text-accent">${escHtml(d.label)}</div>
        <div class="text-muted">${d.type} · deg:${d.degree}</div>
        ${d.year ? `<div class="text-muted">${d.year}</div>` : ''}
      `);
  }).on('mouseleave', () => tooltip.style('display', 'none'));

  sim.on('tick', () => {
    link
      .attr('x1', d => d.source.x)
      .attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x)
      .attr('y2', d => d.target.y);

    node.attr('transform', d => `translate(${d.x},${d.y})`);
  });
}

function renderGraphMetrics(data) {
  const m = data.metrics || {};
  const el = $('#graph-metrics');
  if (!el) return;
  el.innerHTML = `
    <span class="text-muted">Nodos: <span class="text-accent">${m.n_nodes || 0}</span></span>
    <span class="text-muted">Links: <span class="text-accent">${m.n_links || 0}</span></span>
    <span class="text-muted">Proyectos: <span class="text-accent">${m.n_projects || 0}</span></span>
    <span class="text-muted">Deg.avg: <span class="text-accent">${m.avg_degree || 0}</span></span>
  `;
}

/* =========================================================================
   TAB 4 — INFERENCE_LAB
   ========================================================================= */
async function loadInferenceList() {
  try {
    const r = await fetch('/api/inference');
    const d = await r.json();
    renderInferenceList(d.estudios || []);
    const metaEl = $('#inference-meta');
    if (metaEl) metaEl.textContent = `${d.total} estudios`;
  } catch (e) {
    console.error('loadInferenceList:', e);
  }
}

function renderInferenceList(estudios) {
  const listEl = $('#inference-list');
  if (!listEl) return;
  listEl.innerHTML = '';

  if (!estudios.length) {
    listEl.innerHTML = `<li class="file-item"><span class="file-item__name text-muted">[ sin estudios MD ]</span></li>`;
    return;
  }

  estudios.forEach(e => {
    const li = document.createElement('li');
    li.className = 'file-item';
    const ready = e.md_ready;
    li.innerHTML = `
      <span class="file-item__name" title="${escHtml(e.pdf_name)}">${escHtml(e.pdf_name)}</span>
      <span class="file-item__badge ${ready ? 'text-ok' : 'text-muted'}">${ready ? '◈ MD' : '○ --'}</span>
    `;
    if (ready) {
      li.addEventListener('click', () => runInference(e.md_name, li));
    } else {
      li.style.opacity = '0.5';
      li.style.cursor = 'default';
    }
    listEl.appendChild(li);
  });
}

async function runInference(mdName, li) {
  $$('#inference-list .file-item').forEach(el => el.classList.remove('selected'));
  li.classList.add('selected');

  const reportEl  = $('#inference-report');
  const headerEl  = $('#inference-selected');
  if (headerEl) headerEl.textContent = mdName;
  if (reportEl) reportEl.innerHTML = '<div class="text-muted">Analizando... <span class="cursor-blink"></span></div>';

  try {
    const r = await fetch(`/api/inference/${encodeURIComponent(mdName)}`);
    const d = await r.json();
    renderInferenceReport(d, reportEl);
  } catch (e) {
    if (reportEl) reportEl.innerHTML = `<div class="text-alert">Error: ${escHtml(e.message)}</div>`;
  }
}

function renderInferenceReport(report, container) {
  if (!container) return;

  const v = report.veredicto || 'CONDICIONADO';
  const score = Math.round((report.score || 0) * 100);
  const confianza = report.confianza_pct || 0;

  const yesSignals = (report.yes_signals || []).map(s =>
    `<li class="signal-item signal-item--yes">${escHtml(s)}</li>`).join('');
  const noSignals = (report.no_signals || []).map(s =>
    `<li class="signal-item signal-item--no">${escHtml(s)}</li>`).join('');
  const knockouts = (report.knockouts || []).map(s =>
    `<li class="signal-item"><span class="badge badge--warn">KO</span> ${escHtml(s)}</li>`).join('');
  const condicionantes = (report.condicionantes || []).map(s =>
    `<li class="signal-item">${escHtml(s)}</li>`).join('');

  container.innerHTML = `
    <div class="verdict-card">
      <div class="verdict-card__header">
        <div class="verdict-label verdict-label--${v}">${v}</div>
        <div class="text-muted text-xs">Confianza: ${confianza}%</div>
      </div>

      <div class="text-xs text-muted">Score: ${score}%</div>
      <div class="score-bar mt-1">
        <div class="score-bar__fill" style="width:${score}%"></div>
      </div>
    </div>

    ${knockouts ? `
    <div class="mt-2">
      <div class="text-xs text-alert" style="letter-spacing:0.08em; text-transform:uppercase; margin-bottom:4px;">▸ Knockouts</div>
      <ul class="signal-list">${knockouts}</ul>
    </div>` : ''}

    <div class="mt-2">
      <div class="text-xs text-ok" style="letter-spacing:0.08em; text-transform:uppercase; margin-bottom:4px;">▸ Por Qué Sí</div>
      <ul class="signal-list">${yesSignals || '<li class="signal-item signal-item--yes text-muted">─</li>'}</ul>
    </div>

    <div class="mt-2">
      <div class="text-xs text-alert" style="letter-spacing:0.08em; text-transform:uppercase; margin-bottom:4px;">▸ Por Qué No</div>
      <ul class="signal-list">${noSignals || '<li class="signal-item signal-item--no text-muted">─</li>'}</ul>
    </div>

    ${condicionantes ? `
    <div class="mt-2">
      <div class="text-xs text-warn" style="letter-spacing:0.08em; text-transform:uppercase; margin-bottom:4px;">▸ Condicionantes</div>
      <ul class="signal-list">${condicionantes}</ul>
    </div>` : ''}

    <div class="mt-3 text-xs text-muted">
      Fuente: ${escHtml(report.meta?.source || 'gemini')} ·
      Archivo: ${escHtml(report.meta?.file?.split('/').pop() || '─')}
    </div>
  `;
}

/* =========================================================================
   SCRAPER SSE ACTIONS (Panel contextual del Sidebar)
   ========================================================================= */
function initScraperActions() {
  const btnExtractKeys  = $('#btn-extract-keys');
  const btnRunPipeline  = $('#btn-run-pipeline');
  const logEl           = $('#scraper-log');
  const progressEl      = $('#scraper-progress');

  function runSse(url, label) {
    if (State.sseSource) {
      State.sseSource.close();
      State.sseSource = null;
    }

    appendLog(logEl, `Iniciando: ${label}`, 'info');
    setProgress(progressEl, 0);

    const es = new EventSource(url);
    State.sseSource = es;

    if (btnExtractKeys)  btnExtractKeys.disabled = true;
    if (btnRunPipeline)  btnRunPipeline.disabled = true;

    es.onmessage = e => {
      const evt = JSON.parse(e.data);
      if (evt.pct !== undefined) setProgress(progressEl, evt.pct);
      const level = evt.level === 'warning' ? 'warn'
                  : evt.status === 'complete' ? 'ok'
                  : evt.status === 'error' ? 'error' : 'info';
      appendLog(logEl, evt.msg || evt.status, level);

      if (evt.status === 'complete' || evt.status === 'error') {
        es.close();
        if (btnExtractKeys)  btnExtractKeys.disabled = false;
        if (btnRunPipeline)  btnRunPipeline.disabled = false;
      }
    };

    es.onerror = () => {
      appendLog(logEl, 'SSE desconectado', 'error');
      es.close();
      if (btnExtractKeys)  btnExtractKeys.disabled = false;
      if (btnRunPipeline)  btnRunPipeline.disabled = false;
    };
  }

  if (btnExtractKeys) {
    btnExtractKeys.addEventListener('click', () => {
      const year = $('#scraper-year')?.value || '2026';
      runSse(`/api/scraper/extract-keys?year=${year}`, `Extraer claves ${year}`);
    });
  }

  if (btnRunPipeline) {
    btnRunPipeline.addEventListener('click', () => {
      const year = $('#scraper-year')?.value || '2026';
      runSse(`/api/scraper/run-pipeline?year=${year}`, `Pipeline ${year}`);
    });
  }
}

/* =========================================================================
   TAB 5 — SECOND_BRAIN ACTIONS
   ========================================================================= */
function updateSecondBrainUI(statusData) {
  const sb = statusData.second_brain;
  if (!sb) return;

  const totalEl = $('#sb-total-notes');
  if (totalEl) totalEl.textContent = `${sb.total_notes} notas`;

  if (sb.total_notes > 0) {
    const lastSyncEl = $('#sb-last-sync');
    if (lastSyncEl && lastSyncEl.textContent === 'No sincronizado') {
      lastSyncEl.textContent = 'Bóveda activa';
      lastSyncEl.style.color = 'var(--color-status-active)';
    }
  }
}

async function loadWikiNotesList() {
  const listEl = $('#sb-notes-list');
  if (!listEl) return;

  try {
    const r = await fetch('/api/second_brain/notes');
    const d = await r.json();
    listEl.innerHTML = '';

    if (!d.notes || d.notes.length === 0) {
      listEl.innerHTML = '<li class="file-item"><span class="file-item__name text-muted">[ sin notas — sincroniza la bóveda ]</span></li>';
      return;
    }

    d.notes.forEach(note => {
      const li = document.createElement('li');
      li.className = 'file-item';
      li.dataset.title = note.title;
      
      // Prefijo representativo por categoría
      let prefix = '▸ ';
      if (note.category === 'root') prefix = '◆ ';

      li.innerHTML = `
        <span class="file-item__name" title="${escHtml(note.name)}">${prefix}${escHtml(note.title)}</span>
      `;
      li.addEventListener('click', () => {
        listEl.querySelectorAll('.file-item').forEach(item => item.classList.remove('selected'));
        li.classList.add('selected');
        loadWikiNote(note.title);
      });
      listEl.appendChild(li);
    });

    // Cargar 00_Index por defecto si existe
    const indexItem = listEl.querySelector('[data-title="00_Index"]');
    if (indexItem) {
      indexItem.classList.add('selected');
      loadWikiNote('00_Index');
    } else if (d.notes.length > 0) {
      // Si no hay index, seleccionar la primera de la lista
      const first = listEl.querySelector('.file-item');
      if (first) {
        first.classList.add('selected');
        loadWikiNote(d.notes[0].title);
      }
    }
  } catch (err) {
    console.error('Error cargando lista de notas wiki:', err);
  }
}

async function loadWikiNote(title) {
  const titleEl    = $('#sb-note-title');
  const categoryEl = $('#sb-note-category');
  const viewerEl   = $('#sb-note-viewer');

  if (titleEl)    titleEl.textContent = title;
  if (viewerEl)   viewerEl.innerHTML = '<span class="text-muted">[ Cargando nota... ]</span>';

  try {
    const r = await fetch(`/api/second_brain/note?name=${encodeURIComponent(title)}`);
    if (!r.ok) {
      if (viewerEl) viewerEl.innerHTML = `<span class="text-alert">[ ERROR: Nota '${escHtml(title)}' no encontrada en la bóveda ]</span>`;
      return;
    }
    const d = await r.json();
    if (categoryEl) categoryEl.textContent = d.category;

    // Renderizar Markdown simple con enlaces de wiki
    let renderedHtml = renderMarkdownWithWikiLinks(d.content);
    if (viewerEl) viewerEl.innerHTML = renderedHtml;

    // Resaltar en la lista lateral
    const listEl = $('#sb-notes-list');
    if (listEl) {
      listEl.querySelectorAll('.file-item').forEach(item => {
        item.classList.toggle('selected', item.dataset.title === title);
      });
    }
  } catch (err) {
    if (viewerEl) viewerEl.innerHTML = `<span class="text-alert">[ ERROR al recuperar nota: ${escHtml(err)} ]</span>`;
  }
}

function renderMarkdownWithWikiLinks(mdText) {
  // 1. Escapar HTML base para seguridad
  let html = escHtml(mdText);

  // 2. Parsear títulos Markdown (# , ## , ### )
  html = html.replace(/^# (.*)$/gm, '<h1 style="color:var(--color-accent); font-size:15px; margin: 12px 0 6px;">$1</h1>');
  html = html.replace(/^## (.*)$/gm, '<h2 style="color:var(--color-accent); font-size:13px; margin: 10px 0 4px; font-weight:700;">$1</h2>');
  html = html.replace(/^### (.*)$/gm, '<h3 style="color:var(--color-text-primary); font-size:11px; margin: 8px 0 4px; font-weight:700;">$1</h3>');

  // 3. Parsear líneas divisorias (---)
  html = html.replace(/^---$/gm, '<div class="ascii-divider"></div>');

  // 4. Parsear texto en negrita (**texto**)
  html = html.replace(/\*\*(.*?)\*\*/g, '<strong style="color:var(--color-text-primary);">$1</strong>');

  // 5. Parsear citas de bloque (> )
  html = html.replace(/^&gt;\s?(.*)$/gm, '<blockquote style="border-left: 2px solid var(--color-accent); padding-left: 8px; color: var(--color-text-muted); margin: 6px 0;">$1</blockquote>');

  // 6. Parsear listas viñetas (- )
  html = html.replace(/^-\s?(.*)$/gm, '<div style="padding-left: 10px;">▸ $1</div>');

  // 7. Parsear bloques de código inline (`code`)
  html = html.replace(/`(.*?)`/g, '<code style="background:var(--color-surface-2); padding:0 4px; color:var(--color-text-primary); font-size:11px;">$1</code>');

  // 8. RESOLVEDOR DE WIKI-LINKS: [[Nota|Alias]] o [[Nota]]
  html = html.replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, '<span class="wiki-link" onclick="loadWikiNote(\'$1\')">$2</span>');
  html = html.replace(/\[\[([^\]]+)\]\]/g, '<span class="wiki-link" onclick="loadWikiNote(\'$1\')">$1</span>');

  // 9. Enlaces de archivos locales de tipo [nombre](file://path)
  html = html.replace(/\[(.*?)\]\(file:\/\/(.*?)\)/g, '<a href="file://$2" class="text-accent" style="text-decoration:underline;" target="_blank" onclick="event.stopPropagation()">$1</a>');

  return html;
}

// Exponer loadWikiNote globalmente para eventos onclick
window.loadWikiNote = loadWikiNote;

function initSecondBrainActions() {
  const btnSync = $('#btn-sync-sb');
  const logEl   = $('#sb-log');
  const progEl  = $('#sb-progress');

  if (btnSync) {
    btnSync.addEventListener('click', async () => {
      btnSync.disabled = true;
      if (progEl) {
        progEl.style.display = 'block';
        setProgress(progEl, 30);
      }
      appendLog(logEl, 'Iniciando compilación de la bóveda de Obsidian...', 'info');

      try {
        const r = await fetch('/api/second_brain/build', { method: 'POST' });
        if (progEl) setProgress(progEl, 70);
        
        const d = await r.json();
        
        if (progEl) {
          setProgress(progEl, 100);
          setTimeout(() => { progEl.style.display = 'none'; }, 2000);
        }

        if (r.ok && d.status === 'ok') {
          appendLog(logEl, '¡Bóveda sincronizada correctamente!', 'ok');
          appendLog(logEl, `Resultados: ${d.stats.total_proyectos} proyectos, ${d.stats.total_gacetas} gacetas, ${d.stats.total_municipios} entidades.`, 'ok');
          
          const lastSyncEl = $('#sb-last-sync');
          if (lastSyncEl) {
            const now = new Date().toLocaleTimeString();
            lastSyncEl.textContent = `Sincronizado: ${now}`;
            lastSyncEl.style.color = 'var(--color-status-active)';
          }

          // Recargar status del sistema y volver a renderizar la lista de notas
          await loadStatus();
          await loadWikiNotesList();
        } else {
          appendLog(logEl, `Error: ${d.detail || 'Fallo desconocido'}`, 'error');
        }
      } catch (err) {
        appendLog(logEl, `Fallo de red al conectar con API: ${err}`, 'error');
        if (progEl) progEl.style.display = 'none';
      } finally {
        btnSync.disabled = false;
      }
    });
  }
}

/* =========================================================================
   INIT
   ========================================================================= */
document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  initCorpusActions();
  initScraperActions();
  initSecondBrainActions();

  // Activar pestaña por defecto
  activateTab('CORPUS_PDF');

  // Cargar status del sistema y refrescar cada 30s
  loadStatus();
  setInterval(loadStatus, 30_000);
});

/* =========================================================================
   WORKFLOW MODULE
   ========================================================================= */
async function loadWorkflowGacetas() {
  const listEl = $('#wf-gacetas-list');
  if (!listEl) return;

  listEl.innerHTML = '<li class="file-item"><span class="file-item__name text-muted">[ cargando gacetas... ]</span></li>';

  try {
    const year = $('#scraper-year')?.value || 2026;
    const r = await fetch(`/api/scraper/gacetas-summary?year=${year}`);
    const d = await r.json();
    listEl.innerHTML = '';

    if (!d.gacetas || d.gacetas.length === 0) {
      listEl.innerHTML = '<li class="file-item"><span class="file-item__name text-muted">[ sin gacetas en el corpus ]</span></li>';
      return;
    }

    // Ordenar gacetas por nombre descendente
    d.gacetas.sort((a, b) => b.name.localeCompare(a.name));

    d.gacetas.forEach(gaceta => {
      const li = document.createElement('li');
      li.className = 'file-item';
      li.dataset.name = gaceta.name;
      
      const sizeKB = gaceta.size_bytes ? `(${(gaceta.size_bytes / 1024).toFixed(0)}KB)` : '';
      const countLabel = gaceta.clave_count > 0 ? `[${gaceta.clave_count} claves]` : '[sin claves]';

      li.innerHTML = `
        <span class="file-item__name" title="${escHtml(gaceta.name)}">▫ ${escHtml(gaceta.name)}</span>
        <span class="file-item__size text-muted" style="margin-left:auto; font-size:9px; font-family:var(--font-mono);">${countLabel} ${sizeKB}</span>
      `;

      li.addEventListener('click', () => {
        listEl.querySelectorAll('.file-item').forEach(item => item.classList.remove('selected'));
        li.classList.add('selected');
        loadWorkflowGacetaKeys(gaceta.name);
      });

      listEl.appendChild(li);
    });

    // Cargar la primera por defecto
    const first = listEl.querySelector('.file-item');
    if (first) {
      first.click();
    }
  } catch (err) {
    listEl.innerHTML = `<li class="file-item"><span class="file-item__name text-error">[ error: ${err} ]</span></li>`;
  }
}

async function loadWorkflowGacetaKeys(gacetaName) {
  const titleEl = $('#wf-selected-gaceta');
  const countEl = $('#wf-keys-count');
  const tbodyEl = $('#wf-keys-tbody');

  if (titleEl) titleEl.textContent = gacetaName;
  if (tbodyEl) tbodyEl.innerHTML = '<tr><td colspan="5" class="text-muted text-center" style="padding:20px;">[ cargando claves de la gaceta... ]</td></tr>';

  try {
    const year = $('#scraper-year')?.value || 2026;
    const r = await fetch(`/api/scraper/gaceta-keys?gaceta_name=${encodeURIComponent(gacetaName)}&year=${year}`);
    const d = await r.json();

    if (countEl) countEl.textContent = `${d.claves ? d.claves.length : 0} claves extraídas`;
    tbodyEl.innerHTML = '';

    if (!d.claves || d.claves.length === 0) {
      tbodyEl.innerHTML = '<tr><td colspan="5" class="text-muted text-center" style="padding:20px;">[ no se extrajeron claves SINAT de esta gaceta, o ejecuta la conversión a MD primero ]</td></tr>';
      return;
    }

    d.claves.forEach(item => {
      const tr = document.createElement('tr');
      tr.style.borderBottom = '1px solid var(--color-border)';

      const estudioStatus = item.has_pdf_estudio 
        ? '<span class="text-accent" style="font-weight:700;">[ SI ]</span>' 
        : '<span class="text-muted">[ NO ]</span>';

      const resolutivoStatus = item.has_pdf_resolutivo 
        ? '<span class="text-accent" style="font-weight:700;">[ SI ]</span>' 
        : '<span class="text-muted">[ NO ]</span>';

      const extractionStatus = item.has_extraction 
        ? '<span class="text-accent" style="font-weight:700;">[ SI ]</span>' 
        : '<span class="text-muted">[ NO ]</span>';

      const inferenceStatus = item.has_inference 
        ? '<span class="text-accent" style="font-weight:700;">[ SI ]</span>' 
        : '<span class="text-muted">[ NO ]</span>';

      tr.innerHTML = `
        <td style="padding:6px; font-family:var(--font-mono); font-weight:700; color:var(--color-accent);">${escHtml(item.clave)}</td>
        <td style="padding:6px; font-family:var(--font-mono);">${estudioStatus}</td>
        <td style="padding:6px; font-family:var(--font-mono);">${resolutivoStatus}</td>
        <td style="padding:6px; font-family:var(--font-mono);">${extractionStatus}</td>
        <td style="padding:6px; font-family:var(--font-mono);">${inferenceStatus}</td>
      `;
      tbodyEl.appendChild(tr);
    });
  } catch (err) {
    tbodyEl.innerHTML = `<tr><td colspan="5" class="text-error text-center" style="padding:20px;">[ error cargando claves: ${err} ]</td></tr>`;
  }
}
