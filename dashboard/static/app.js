/**
 * Zohar Intelligence v4 — app.js (Enhanced Edition)
 * Vanilla JS + D3 v7 (CDN). Zero frameworks. Zero deps locales.
 * Módulos: CORPUS_PDF, MD_LAB, GRAFO_RED, INFERENCE_LAB, SECOND_BRAIN, WORKFLOW
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
  // Sparkline history
  cpuHistory:   new Array(20).fill(0),
  ramHistory:   new Array(20).fill(0),
};

/* =========================================================================
   UTILIDADES DOM
   ========================================================================= */
const $ = (sel, ctx = document) => ctx.querySelector(sel);
const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

function ts() {
  return new Date().toTimeString().slice(0, 8);
}

function fmtSize(bytes) {
  if (bytes < 1024)        return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)}MB`;
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function appendLog(consoleEl, msg, level = 'info') {
  if (!consoleEl) return;
  const line = document.createElement('div');
  line.className = `log-line log-line--${level}`;
  line.innerHTML = `<span class="log-line__ts">[${ts()}]</span><span class="log-line__msg">${escHtml(msg)}</span>`;
  consoleEl.appendChild(line);
  // Keep max 100 lines
  while (consoleEl.children.length > 100) consoleEl.removeChild(consoleEl.firstChild);
  consoleEl.scrollTop = consoleEl.scrollHeight;
}

function setProgress(barEl, pct) {
  if (!barEl) return;
  const fill = barEl.querySelector('.progress-bar__fill');
  if (fill) fill.style.width = `${Math.min(100, Math.max(0, pct))}%`;
  barEl.setAttribute('aria-valuenow', Math.round(pct));
}

/* =========================================================================
   TOASTS
   ========================================================================= */
function showToast(msg, type = 'info', durationMs = 3500) {
  const container = $('#toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast toast--${type}`;
  toast.textContent = msg;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.animation = 'toast-out 200ms ease forwards';
    setTimeout(() => toast.remove(), 220);
  }, durationMs);
}

/* =========================================================================
   RELOJ EN TOPBAR
   ========================================================================= */
function startClock() {
  const el = $('#topbar-clock');
  if (!el) return;
  function tick() {
    el.textContent = new Date().toTimeString().slice(0, 8);
  }
  tick();
  setInterval(tick, 1000);
}

/* =========================================================================
   SPARKLINES (Canvas)
   ========================================================================= */
function drawSparkline(canvasId, history, color = '#FFB000') {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width;
  const H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  const max = Math.max(...history, 1);
  const step = W / (history.length - 1);

  ctx.beginPath();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  history.forEach((v, i) => {
    const x = i * step;
    const y = H - (v / max) * H;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Fill area
  ctx.lineTo(W, H);
  ctx.lineTo(0, H);
  ctx.closePath();
  ctx.fillStyle = color + '18';
  ctx.fill();
}

function updateSparklines() {
  drawSparkline('spark-cpu', State.cpuHistory, '#FFB000');
  drawSparkline('spark-ram', State.ramHistory, '#CC8D00');
}

/* =========================================================================
   TAB NAVIGATION
   ========================================================================= */
function initTabs() {
  $$('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => activateTab(btn.dataset.tab));
  });
}

function activateTab(tabId) {
  State.activeTab = tabId;
  $$('.tab-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === tabId);
    b.setAttribute('aria-selected', b.dataset.tab === tabId);
  });
  $$('.tab-panel').forEach(p => p.classList.toggle('active', p.id === `panel-${tabId}`));

  if (tabId === 'CORPUS_PDF')    loadCorpus();
  if (tabId === 'MD_LAB')        loadMdList();
  if (tabId === 'GRAFO_RED')     loadGraph();
  if (tabId === 'INFERENCE_LAB') loadInferenceList();
  if (tabId === 'SECOND_BRAIN')  { updateSecondBrainUI(State.systemStatus); loadWikiNotesList(); }
  if (tabId === 'WORKFLOW')      loadWorkflowGacetas();
}

/* =========================================================================
   SYSTEM STATUS
   ========================================================================= */
async function loadStatus() {
  try {
    const r = await fetch('/api/status');
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();
    State.systemStatus = d;

    // Update CPU / RAM
    const cpu = d.cpu_pct || 0;
    const ram = d.ram_pct || 0;

    State.cpuHistory.push(cpu); State.cpuHistory.shift();
    State.ramHistory.push(ram); State.ramHistory.shift();
    updateSparklines();

    const cpuEl  = $('#status-cpu');
    const ramEl  = $('#status-ram');
    const diskEl = $('#status-disk');
    const pill   = $('#sys-status-pill');
    const dot    = $('#status-dot');
    const txt    = $('#status-text');

    if (cpuEl)  cpuEl.textContent  = `${cpu}%`;
    if (ramEl)  ramEl.textContent  = `${ram}%`;
    if (diskEl && d.disk_free_gb !== undefined) diskEl.textContent = `${d.disk_free_gb}GB`;
    if (pill)   pill.classList.add('status-pill--online');
    if (txt)    txt.textContent = 'ONLINE';
    if (dot)    dot.style.background = '';

    // Sidebar stats
    updateSidebarStats(d);

    // Second Brain UI
    if (d.second_brain) updateSecondBrainUI(d);

  } catch (err) {
    const pill = $('#sys-status-pill');
    const txt  = $('#status-text');
    if (pill) pill.classList.remove('status-pill--online');
    if (txt)  txt.textContent = 'OFFLINE';
  }
}

function updateSidebarStats(d) {
  const sbNotes = d.second_brain?.total_notes || 0;
  const noteEl = $('#sb-stat-notes');
  if (noteEl) noteEl.textContent = sbNotes;
}

/* =========================================================================
   TAB 1 — CORPUS_PDF
   ========================================================================= */
async function loadCorpus() {
  try {
    const r = await fetch('/api/corpus/pdfs');
    const d = await r.json();
    State.pdfs = d.pdfs || [];
    renderPdfList(State.pdfs);

    const metaEl = $('#corpus-meta');
    if (metaEl) metaEl.textContent = `${State.pdfs.length} archivos`;

    // Nav count
    const nc = $('#nav-count-corpus');
    if (nc) nc.textContent = State.pdfs.length;

    // Sidebar stat
    const sp = $('#sb-stat-pdfs');
    if (sp) sp.textContent = State.pdfs.length;

    // Stats strip
    const counts = { resumenes: 0, estudios: 0, resolutivos: 0, gacetas: 0 };
    State.pdfs.forEach(p => { if (counts[p.folder] !== undefined) counts[p.folder]++; });
    Object.entries(counts).forEach(([k, v]) => {
      const el = $(`#stat-${k}`);
      if (el) el.textContent = v;
    });
  } catch (e) {
    console.error('loadCorpus:', e);
  }
}

function renderPdfList(pdfs) {
  const listEl = $('#pdf-list');
  if (!listEl) return;
  listEl.innerHTML = '';

  if (!pdfs.length) {
    listEl.innerHTML = `<li class="file-item file-item--loading"><span class="file-item__name text-muted">[ corpus vacío ]</span></li>`;
    return;
  }

  const groups = {};
  pdfs.forEach(p => {
    if (!groups[p.folder]) groups[p.folder] = [];
    groups[p.folder].push(p);
  });

  Object.entries(groups).forEach(([folder, files]) => {
    const header = document.createElement('li');
    header.className = 'sidebar__section-label';
    header.style.cssText = 'list-style:none; padding:5px 12px; margin-top:2px;';
    header.textContent = `─ ${folder.toUpperCase()} (${files.length})`;
    listEl.appendChild(header);

    files.forEach(pdf => {
      const li = document.createElement('li');
      li.className = 'file-item';
      li.id = `pdf-${btoa(pdf.name).replace(/[/+=]/g, '_')}`;
      li.setAttribute('role', 'option');
      li.setAttribute('aria-selected', 'false');
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
  $$('#pdf-list .file-item').forEach(el => {
    el.classList.remove('selected');
    el.setAttribute('aria-selected', 'false');
  });
  li.classList.add('selected');
  li.setAttribute('aria-selected', 'true');

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

  // Search filter
  const searchEl = $('#pdf-search');
  if (searchEl) {
    searchEl.addEventListener('input', () => {
      const q = searchEl.value.toLowerCase();
      const filtered = State.pdfs.filter(p => p.name.toLowerCase().includes(q));
      renderPdfList(filtered);
    });
  }

  if (btnExtract) {
    btnExtract.addEventListener('click', () => {
      if (!State.selectedPdf) {
        showToast('Selecciona un PDF primero', 'warn');
        appendLog(logEl, 'Selecciona un PDF primero', 'warn');
        return;
      }
      if (State.sseSource) { State.sseSource.close(); State.sseSource = null; }
      if (viewerEl) viewerEl.textContent = '';
      const prev = $('#btn-download-md-corpus');
      if (prev) prev.remove();

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
          appendLog(logEl, evt.msg, 'ok');
          _showMdDownloadButton(evt.md_name, btnExtract.parentElement || logEl.parentElement);
          showToast(`MD guardado: ${evt.md_name}`, 'ok');
        } else if (evt.status === 'complete') {
          appendLog(logEl, evt.msg, 'ok');
          es.close(); btnExtract.disabled = false;
          if (btnStop) btnStop.disabled = true;
          setProgress(progressEl, 100);
          loadCorpus();
        } else if (evt.status === 'error') {
          appendLog(logEl, evt.msg, 'error');
          showToast(`Error: ${evt.msg}`, 'error');
          es.close(); btnExtract.disabled = false;
          if (btnStop) btnStop.disabled = true;
        }
      };

      es.onerror = () => {
        appendLog(logEl, 'Conexión SSE perdida', 'error');
        es.close(); btnExtract.disabled = false;
        if (btnStop) btnStop.disabled = true;
      };
    });
  }

  if (btnStop) {
    btnStop.addEventListener('click', async () => {
      if (State.sseSource) State.sseSource.close();
      if (State.selectedPdf) {
        await fetch(`/stop_single?pdf_name=${encodeURIComponent(State.selectedPdf.name)}`);
      }
      appendLog(logEl, 'Extracción detenida', 'warn');
      showToast('Extracción detenida', 'warn');
      if (btnExtract) btnExtract.disabled = false;
      btnStop.disabled = true;
    });
  }
}

function _showMdDownloadButton(mdName, parent) {
  if (!mdName || !parent) return;
  const prev = document.getElementById('btn-download-md-corpus');
  if (prev) prev.remove();
  const btn = document.createElement('a');
  btn.id        = 'btn-download-md-corpus';
  btn.className = 'btn btn--ok';
  btn.href      = `/api/md/download?filename=${encodeURIComponent(mdName)}`;
  btn.download  = mdName;
  btn.innerHTML = `<span aria-hidden="true">⬇</span> ${escHtml(mdName)}`;
  btn.style.cssText = 'display:inline-flex; margin-top:8px; text-decoration:none;';
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
    renderMdList(State.mds);

    const metaEl = $('#md-meta');
    if (metaEl) metaEl.textContent = `${State.mds.length} documentos`;

    const nc = $('#nav-count-md');
    if (nc) nc.textContent = State.mds.length;

    const sp = $('#sb-stat-mds');
    if (sp) sp.textContent = State.mds.length;
  } catch (e) {
    console.error('loadMdList:', e);
  }
}

function renderMdList(mds) {
  const listEl = $('#md-list');
  if (!listEl) return;
  listEl.innerHTML = '';

  if (!mds.length) {
    listEl.innerHTML = `<li class="file-item file-item--loading"><span class="file-item__name text-muted">[ sin documentos MD ]</span></li>`;
    return;
  }

  mds.forEach(md => {
    const li = document.createElement('li');
    li.className = 'file-item';
    li.setAttribute('role', 'option');
    li.innerHTML = `
      <span class="file-item__name" title="${escHtml(md.name)}">${escHtml(md.name)}</span>
      <span class="file-item__badge">${fmtSize(md.size_bytes)}</span>
      <a
        class="file-item__dl-btn"
        href="/api/md/download?filename=${encodeURIComponent(md.name)}"
        download="${escHtml(md.name)}"
        title="Descargar ${escHtml(md.name)}"
        onclick="event.stopPropagation()"
        aria-label="Descargar ${escHtml(md.name)}"
      >⬇</a>
    `;
    li.addEventListener('click', () => selectMd(md, li));
    listEl.appendChild(li);
  });
}

function initMdLabActions() {
  // Search filter
  const searchEl = $('#md-search');
  if (searchEl) {
    searchEl.addEventListener('input', () => {
      const q = searchEl.value.toLowerCase();
      renderMdList(State.mds.filter(m => m.name.toLowerCase().includes(q)));
    });
  }

  // Extract all button
  const btnAll = $('#btn-extract-all-md');
  if (btnAll) {
    btnAll.addEventListener('click', () => {
      const logEl = $('#md-log');
      const progEl = $('#md-progress');
      if (logEl) logEl.classList.remove('hidden');
      if (progEl) progEl.classList.remove('hidden');

      if (State.sseSource) { State.sseSource.close(); }
      appendLog(logEl, 'Iniciando extracción masiva MD...', 'info');
      setProgress(progEl, 0);

      const es = new EventSource('/api/scraper/extract-pipeline-md');
      State.sseSource = es;
      btnAll.disabled = true;

      es.onmessage = (e) => {
        const evt = JSON.parse(e.data);
        if (evt.pct !== undefined) setProgress(progEl, evt.pct);
        const level = evt.level === 'warning' ? 'warn' : evt.status === 'complete' ? 'ok' : 'info';
        appendLog(logEl, evt.msg || evt.status, level);
        if (evt.status === 'complete' || evt.status === 'error') {
          es.close();
          btnAll.disabled = false;
          loadMdList();
          showToast(`Pipeline MD: ${evt.n_extracted || 0} extraídos`, 'ok');
        }
      };

      es.onerror = () => {
        appendLog(logEl, 'SSE error', 'error');
        es.close();
        btnAll.disabled = false;
      };
    });
  }
}

async function selectMd(md, li) {
  State.selectedMd = md;
  $$('#md-list .file-item').forEach(el => el.classList.remove('selected'));
  li.classList.add('selected');

  const viewerEl  = $('#md-viewer-content');
  const badgesEl  = $('#md-badges');
  const headerEl  = $('#md-selected-name');

  if (headerEl) headerEl.textContent = md.name;
  if (viewerEl) viewerEl.textContent = 'Cargando...';
  if (badgesEl) badgesEl.innerHTML = '';

  try {
    const r = await fetch(`/api/md/read?filename=${encodeURIComponent(md.name)}&page=1&page_size=200`);
    const d = await r.json();
    if (viewerEl) viewerEl.textContent = d.content + (d.total_pages > 1 ? `\n\n… (${d.total_pages - 1} páginas más)` : '');

    if (badgesEl) {
      const content = d.content;
      const badges = [];
      if (/latitud|longitud|UTM|coordenadas/i.test(content)) badges.push('geo');
      if (/NOM-\d+|LGEEPA|artículo/i.test(content))          badges.push('law');
      if (/especie|flora|fauna|hábitat/i.test(content))       badges.push('bio');
      badges.forEach(b => {
        const span = document.createElement('span');
        span.className = `badge badge--${b}`;
        span.textContent = b.toUpperCase();
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

  containerEl.innerHTML = '<div class="graph-placeholder"><span class="text-muted">[ Construyendo grafo... ]</span></div>';

  try {
    const r = await fetch('/api/graph?format=compact');
    const d = await r.json();
    State.graph = d;
    renderGraph(d, containerEl);
    renderGraphMetrics(d);
    renderGraphLegend(d);

    const nc = $('#nav-count-graph');
    if (nc) nc.textContent = d.metrics?.n_nodes || 0;
  } catch (e) {
    containerEl.innerHTML = `<div class="graph-placeholder"><span class="text-alert">Error: ${escHtml(e.message)}</span></div>`;
  }
}

function renderGraph(data, containerEl) {
  containerEl.innerHTML = '';
  if (!window.d3) {
    containerEl.innerHTML = '<div class="graph-placeholder"><span class="text-alert">D3 no disponible</span></div>';
    return;
  }

  const schema    = data.schema || {};
  const IDX       = { i:0, t:1, l:2, st:3, yr:4, deg:5, com:6 };
  const nodes     = data.nodes.map(n => ({
    id:     n[IDX.i],
    type:   n[IDX.t],
    label:  n[IDX.l],
    color:  n[IDX.st] || '#FFB000',
    year:   n[IDX.yr],
    degree: n[IDX.deg] || 1,
    com:    n[IDX.com] || 0,
  }));

  const links = data.links.map(l => ({
    source: nodes[l[0]]?.id,
    target: nodes[l[1]]?.id,
    rel:    schema.rel_map?.[l[2]] || '',
  })).filter(l => l.source && l.target);

  const W = containerEl.clientWidth  || 900;
  const H = containerEl.clientHeight || 500;

  const svg = d3.select(containerEl)
    .append('svg')
    .attr('id', 'graph-svg')
    .attr('width', W)
    .attr('height', H);

  const g = svg.append('g');

  svg.call(d3.zoom()
    .scaleExtent([0.05, 10])
    .on('zoom', e => g.attr('transform', e.transform)));

  const tooltip = d3.select(containerEl)
    .append('div')
    .attr('class', 'graph-tooltip')
    .attr('role', 'tooltip');

  const sim = d3.forceSimulation(nodes)
    .force('link',      d3.forceLink(links).id(d => d.id).distance(70).strength(0.4))
    .force('charge',    d3.forceManyBody().strength(-100))
    .force('center',    d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide(d => Math.sqrt(d.degree) * 5 + 8));

  const link = g.append('g')
    .selectAll('line')
    .data(links)
    .join('line')
    .attr('class', 'graph-link')
    .attr('stroke-width', 0.8);

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
    .attr('r', d => Math.max(3, Math.sqrt(d.degree) * 4))
    .attr('fill', d => d.color)
    .attr('fill-opacity', 0.8)
    .attr('stroke', d => d.color)
    .attr('stroke-width', 1.5);

  node.append('text')
    .attr('dy', d => Math.sqrt(d.degree) * 4 + 11)
    .attr('text-anchor', 'middle')
    .text(d => d.label.length > 12 ? d.label.slice(0, 10) + '..' : d.label);

  node.on('mousemove', (e, d) => {
    tooltip
      .style('display', 'block')
      .style('left', e.clientX + 14 + 'px')
      .style('top',  e.clientY - 10 + 'px')
      .html(`
        <div class="text-accent" style="font-weight:700;">${escHtml(d.label)}</div>
        <div class="text-muted">${d.type} · deg:${d.degree}</div>
        ${d.year ? `<div class="text-muted">${d.year}</div>` : ''}
      `);
  }).on('mouseleave', () => tooltip.style('display', 'none'));

  // Graph type filter
  const filterEl = $('#graph-filter-type');
  if (filterEl) {
    filterEl.addEventListener('change', () => {
      const type = filterEl.value;
      node.style('opacity', d => (type === 'all' || d.type === type) ? 1 : 0.1);
      link.style('opacity', l => {
        if (type === 'all') return 0.5;
        const sNode = nodes.find(n => n.id === (l.source?.id || l.source));
        const tNode = nodes.find(n => n.id === (l.target?.id || l.target));
        return (sNode?.type === type || tNode?.type === type) ? 0.5 : 0.05;
      });
    });
  }

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
  const m  = data.metrics || {};
  const el = $('#graph-metrics');
  if (!el) return;

  const pills = [
    ['Nodos', m.n_nodes || 0],
    ['Links', m.n_links || 0],
    ['Proyectos', m.n_projects || 0],
    ['Deg.avg', m.avg_degree || 0],
  ];

  el.innerHTML = pills.map(([label, val]) =>
    `<div class="graph-metric-pill">${label}: <span>${val}</span></div>`
  ).join('');
}

function renderGraphLegend(data) {
  const el = $('#graph-legend');
  if (!el) return;
  const colorMap = {};
  (data.nodes || []).forEach(n => {
    const type = n[1];
    const color = n[3];
    if (type && color && !colorMap[type]) colorMap[type] = color;
  });

  el.innerHTML = Object.entries(colorMap).map(([type, color]) =>
    `<div class="graph-legend-item">
      <div class="graph-legend-dot" style="background:${escHtml(color)}"></div>
      <span>${escHtml(type)}</span>
    </div>`
  ).join('');
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

    const nc = $('#nav-count-inference');
    if (nc) nc.textContent = d.total || 0;
  } catch (e) {
    console.error('loadInferenceList:', e);
  }
}

function renderInferenceList(estudios) {
  const listEl = $('#inference-list');
  if (!listEl) return;
  listEl.innerHTML = '';

  if (!estudios.length) {
    listEl.innerHTML = `<li class="file-item file-item--loading"><span class="file-item__name text-muted">[ sin estudios MD ]</span></li>`;
    return;
  }

  estudios.forEach(e => {
    const li = document.createElement('li');
    li.className = 'file-item';
    li.setAttribute('role', 'option');
    const ready = e.md_ready;
    li.innerHTML = `
      <span class="file-item__name" title="${escHtml(e.pdf_name)}">${escHtml(e.pdf_name)}</span>
      <span class="file-item__badge ${ready ? 'text-ok' : 'text-muted'}">${ready ? '◈' : '○'}</span>
    `;
    if (ready) {
      li.addEventListener('click', () => runInference(e.md_name, li));
    } else {
      li.style.opacity = '0.45';
      li.style.cursor  = 'default';
    }
    listEl.appendChild(li);
  });
}

async function runInference(mdName, li) {
  $$('#inference-list .file-item').forEach(el => el.classList.remove('selected'));
  li.classList.add('selected');

  const reportEl = $('#inference-report');
  const headerEl = $('#inference-selected');
  if (headerEl) headerEl.textContent = mdName;
  if (reportEl) reportEl.innerHTML = '<div class="inference-placeholder text-muted">Analizando... <span class="cursor-blink"></span></div>';

  try {
    const r = await fetch(`/api/inference/${encodeURIComponent(mdName)}`);
    const d = await r.json();
    renderInferenceReport(d, reportEl);
  } catch (e) {
    if (reportEl) reportEl.innerHTML = `<div class="text-alert" style="padding:24px;">Error: ${escHtml(e.message)}</div>`;
  }
}

function renderInferenceReport(report, container) {
  if (!container) return;

  const v          = report.veredicto || 'CONDICIONADO';
  const score      = Math.round((report.score || 0) * 100);
  const confianza  = report.confianza_pct || 0;

  const yesSignals     = (report.yes_signals || []).map(s => `<li class="signal-item signal-item--yes">${escHtml(s)}</li>`).join('');
  const noSignals      = (report.no_signals || []).map(s => `<li class="signal-item signal-item--no">${escHtml(s)}</li>`).join('');
  const knockouts      = (report.knockouts || []).map(s => `<li class="signal-item"><span class="badge badge--warn">KO</span> ${escHtml(s)}</li>`).join('');
  const condicionantes = (report.condicionantes || []).map(s => `<li class="signal-item">${escHtml(s)}</li>`).join('');

  container.innerHTML = `
    <div class="verdict-card verdict-card--${v}">
      <div class="verdict-card__header">
        <div class="verdict-label verdict-label--${v}">${v}</div>
        <div class="text-muted text-xs">Confianza: ${confianza}%</div>
      </div>
      <div class="text-xs text-muted">Score: ${score}%</div>
      <div class="score-bar mt-1">
        <div class="score-bar__fill" style="width:0%" data-target="${score}%"></div>
      </div>
    </div>

    ${knockouts ? `
    <div class="mt-2">
      <div class="text-xs text-alert" style="letter-spacing:.08em; text-transform:uppercase; margin-bottom:4px;">▸ Knockouts</div>
      <ul class="signal-list">${knockouts}</ul>
    </div>` : ''}

    <div class="mt-2">
      <div class="text-xs text-ok" style="letter-spacing:.08em; text-transform:uppercase; margin-bottom:4px;">▸ Señales Favorables</div>
      <ul class="signal-list">${yesSignals || '<li class="signal-item signal-item--yes text-muted">─</li>'}</ul>
    </div>

    <div class="mt-2">
      <div class="text-xs text-alert" style="letter-spacing:.08em; text-transform:uppercase; margin-bottom:4px;">▸ Señales Desfavorables</div>
      <ul class="signal-list">${noSignals || '<li class="signal-item signal-item--no text-muted">─</li>'}</ul>
    </div>

    ${condicionantes ? `
    <div class="mt-2">
      <div class="text-xs text-warn" style="letter-spacing:.08em; text-transform:uppercase; margin-bottom:4px;">▸ Condicionantes</div>
      <ul class="signal-list">${condicionantes}</ul>
    </div>` : ''}

    <div class="mt-3 text-xs text-muted">
      Fuente: ${escHtml(report.meta?.source || 'gemini')} ·
      Archivo: ${escHtml(report.meta?.file?.split('/').pop() || '─')}
    </div>
  `;

  // Animate score bar
  requestAnimationFrame(() => {
    const fill = container.querySelector('.score-bar__fill');
    if (fill) fill.style.width = fill.dataset.target || '0%';
  });
}

/* =========================================================================
   SCRAPER SSE ACTIONS
   ========================================================================= */
function initScraperActions() {
  const btnExtractKeys = $('#btn-extract-keys');
  const btnRunPipeline = $('#btn-run-pipeline');
  const logEl          = $('#scraper-log');
  const progressEl     = $('#scraper-progress');
  const pctEl          = $('#scraper-pct');

  function runSse(url, label) {
    if (State.sseSource) { State.sseSource.close(); State.sseSource = null; }
    appendLog(logEl, `Iniciando: ${label}`, 'info');
    setProgress(progressEl, 0);
    if (pctEl) pctEl.textContent = '0%';

    const es = new EventSource(url);
    State.sseSource = es;
    if (btnExtractKeys) btnExtractKeys.disabled = true;
    if (btnRunPipeline) btnRunPipeline.disabled = true;

    es.onmessage = e => {
      const evt = JSON.parse(e.data);
      if (evt.pct !== undefined) {
        setProgress(progressEl, evt.pct);
        if (pctEl) pctEl.textContent = `${Math.round(evt.pct)}%`;
      }
      const level = evt.level === 'warning' ? 'warn'
                  : evt.status === 'complete' ? 'ok'
                  : evt.status === 'error' ? 'error' : 'info';
      appendLog(logEl, evt.msg || evt.status, level);

      if (evt.status === 'complete') {
        es.close();
        if (btnExtractKeys) btnExtractKeys.disabled = false;
        if (btnRunPipeline) btnRunPipeline.disabled = false;
        showToast(`✓ ${label} completado`, 'ok');
        loadCorpus();
        loadMdList();
      } else if (evt.status === 'error') {
        es.close();
        if (btnExtractKeys) btnExtractKeys.disabled = false;
        if (btnRunPipeline) btnRunPipeline.disabled = false;
        showToast(`Error en ${label}`, 'error');
      }
    };

    es.onerror = () => {
      appendLog(logEl, 'SSE desconectado', 'error');
      es.close();
      if (btnExtractKeys) btnExtractKeys.disabled = false;
      if (btnRunPipeline) btnRunPipeline.disabled = false;
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
   TAB 5 — SECOND_BRAIN
   ========================================================================= */
function updateSecondBrainUI(statusData) {
  if (!statusData) return;
  const sb = statusData.second_brain;
  if (!sb) return;

  const totalEl = $('#sb-total-notes');
  if (totalEl) totalEl.textContent = sb.total_notes || 0;

  const nc = $('#nav-count-sb');
  if (nc) nc.textContent = sb.total_notes || 0;

  const noteEl = $('#sb-stat-notes');
  if (noteEl) noteEl.textContent = sb.total_notes || 0;
}

async function loadWikiNotesList() {
  const listEl = $('#sb-notes-list');
  if (!listEl) return;

  try {
    const r = await fetch('/api/second_brain/notes');
    const d = await r.json();
    listEl.innerHTML = '';

    if (!d.notes || !d.notes.length) {
      listEl.innerHTML = '<li class="file-item file-item--loading"><span class="file-item__name text-muted">[ sin notas — sincroniza ]</span></li>';
      return;
    }

    const nc = $('#nav-count-sb');
    if (nc) nc.textContent = d.notes.length;

    const metaEl = $('#sb-meta');
    if (metaEl) metaEl.textContent = `${d.notes.length} notas`;

    const sbSearch = $('#sb-search');
    let allNotes = d.notes;

    const logEl = $('#sb-log');
    const btnSemanticSearch = $('#btn-sb-semantic-search');
    const inputSemanticSearch = $('#sb-semantic-search-input');
    const searchStatusBar = $('#sb-search-status-bar');
    const searchStatusText = $('#sb-search-status-text');
    const btnClearSearch = $('#btn-sb-clear-search');

    function renderNoteList(notes) {
      listEl.innerHTML = '';
      notes.forEach(note => {
        const li = document.createElement('li');
        li.className = 'file-item';
        li.dataset.title = note.title;
        li.setAttribute('role', 'option');
        const prefix = note.category === 'root' ? '◆ ' : '▸ ';
        li.innerHTML = `<span class="file-item__name" title="${escHtml(note.name)}">${prefix}${escHtml(note.title)}</span>`;
        li.addEventListener('click', () => {
          listEl.querySelectorAll('.file-item').forEach(i => i.classList.remove('selected'));
          li.classList.add('selected');
          loadWikiNote(note.title);
        });
        listEl.appendChild(li);
      });
    }

    renderNoteList(allNotes);

    if (sbSearch) {
      sbSearch.addEventListener('input', () => {
        const q = sbSearch.value.toLowerCase();
        renderNoteList(allNotes.filter(n => n.title.toLowerCase().includes(q)));
      });
    }

    if (btnSemanticSearch && inputSemanticSearch) {
      btnSemanticSearch.onclick = async () => {
        const query = inputSemanticSearch.value.trim();
        if (!query) {
          showToast('Ingresa una consulta de búsqueda', 'error');
          return;
        }

        btnSemanticSearch.disabled = true;
        btnSemanticSearch.innerHTML = '<span aria-hidden="true">⏳</span>...';
        appendLog(logEl, `Búsqueda semántica: "${query}"...`, 'info');

        try {
          const res = await fetch(`/api/second_brain/search?q=${encodeURIComponent(query)}`);
          if (!res.ok) throw new Error('Error en el endpoint de búsqueda semántica');
          const searchData = await res.json();
          
          if (!searchData.results || searchData.results.length === 0) {
            appendLog(logEl, 'No se encontraron notas similares.', 'info');
            listEl.innerHTML = '<li class="file-item file-item--loading"><span class="file-item__name text-muted">[ sin resultados semánticos ]</span></li>';
            if (searchStatusBar) searchStatusBar.classList.remove('hidden');
            if (searchStatusText) searchStatusText.textContent = `0 resultados para: "${query.substring(0, 15)}..."`;
            return;
          }

          appendLog(logEl, `Búsqueda semántica completada: ${searchData.results.length} coincidencias`, 'ok');
          
          // Renderizar los resultados semánticos
          listEl.innerHTML = '';
          searchData.results.forEach(result => {
            const li = document.createElement('li');
            li.className = 'file-item';
            li.dataset.title = result.title;
            li.setAttribute('role', 'option');
            const prefix = result.category === 'root' ? '◆ ' : '▸ ';
            li.innerHTML = `
              <div style="display:flex; justify-content:space-between; width:100%; align-items:center;">
                <span class="file-item__name" title="${escHtml(result.name)}">${prefix}${escHtml(result.title)}</span>
                <span class="text-xs" style="color:var(--accent-color); font-family:var(--font-mono); font-weight:bold; opacity:0.8;">${result.pct}%</span>
              </div>
            `;
            li.addEventListener('click', () => {
              listEl.querySelectorAll('.file-item').forEach(i => i.classList.remove('selected'));
              li.classList.add('selected');
              loadWikiNote(result.title);
            });
            listEl.appendChild(li);
          });

          if (searchStatusBar) searchStatusBar.classList.remove('hidden');
          if (searchStatusText) searchStatusText.textContent = `Semántica para: "${query.substring(0, 12)}..."`;

          // Cargar la primera nota del resultado semántico
          if (searchData.results.length > 0) {
            loadWikiNote(searchData.results[0].title);
          }

        } catch (err) {
          appendLog(logEl, `Error de búsqueda semántica: ${err.message}`, 'error');
          showToast('Error en búsqueda semántica', 'error');
        } finally {
          btnSemanticSearch.disabled = false;
          btnSemanticSearch.innerHTML = '<span aria-hidden="true">▸</span> BUSCAR IA';
        }
      };

      inputSemanticSearch.onkeydown = (e) => {
        if (e.key === 'Enter') {
          btnSemanticSearch.click();
        }
      };
    }

    if (btnClearSearch) {
      btnClearSearch.onclick = () => {
        if (inputSemanticSearch) inputSemanticSearch.value = '';
        if (sbSearch) sbSearch.value = '';
        if (searchStatusBar) searchStatusBar.classList.add('hidden');
        renderNoteList(allNotes);
        
        // Cargar index o primera nota
        const indexItem = listEl.querySelector('[data-title="00_Index"]');
        if (indexItem) { indexItem.classList.add('selected'); loadWikiNote('00_Index'); }
        else if (allNotes.length) { listEl.querySelector('.file-item')?.click(); }
      };
    }

    // Stats strip
    const projEl = $('#sb-stat-proyectos');
    const gacEl  = $('#sb-stat-gacetas-sb');
    if (projEl) projEl.textContent = d.notes.filter(n => n.category === '02_Entities').length;
    if (gacEl)  gacEl.textContent  = d.notes.filter(n => n.category === '01_Sources').length;

    const indexItem = listEl.querySelector('[data-title="00_Index"]');
    if (indexItem) { indexItem.classList.add('selected'); loadWikiNote('00_Index'); }
    else if (allNotes.length) { listEl.querySelector('.file-item')?.click(); }

  } catch (err) {
    console.error('Error cargando notas wiki:', err);
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
      if (viewerEl) viewerEl.innerHTML = `<span class="text-alert">[ Nota '${escHtml(title)}' no encontrada ]</span>`;
      return;
    }
    const d = await r.json();
    if (categoryEl) categoryEl.textContent = d.category;
    if (viewerEl)   viewerEl.innerHTML = renderMarkdownWithWikiLinks(d.content);

    // Highlight in list
    const listEl = $('#sb-notes-list');
    if (listEl) {
      listEl.querySelectorAll('.file-item').forEach(item => {
        item.classList.toggle('selected', item.dataset.title === title);
      });
    }
  } catch (err) {
    if (viewerEl) viewerEl.innerHTML = `<span class="text-alert">[ Error: ${escHtml(String(err))} ]</span>`;
  }
}

function renderMarkdownWithWikiLinks(mdText) {
  let html = escHtml(mdText);
  // Headings
  html = html.replace(/^# (.*)$/gm, '<h1>$1</h1>');
  html = html.replace(/^## (.*)$/gm, '<h2>$1</h2>');
  html = html.replace(/^### (.*)$/gm, '<h3>$1</h3>');
  // HR
  html = html.replace(/^---$/gm, '<div class="ascii-divider"></div>');
  // Bold
  html = html.replace(/\*\*(.*?)\*\*/g, '<strong style="color:var(--color-text-primary);">$1</strong>');
  // Blockquotes
  html = html.replace(/^&gt;\s?(.*)$/gm, '<blockquote>$1</blockquote>');
  // Lists
  html = html.replace(/^-\s?(.*)$/gm, '<div style="padding-left:10px;">▸ $1</div>');
  // Inline code
  html = html.replace(/`(.*?)`/g, '<code>$1</code>');
  // Wiki links [[Note|Alias]] and [[Note]]
  html = html.replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, '<span class="wiki-link" onclick="loadWikiNote(\'$1\')">$2</span>');
  html = html.replace(/\[\[([^\]]+)\]\]/g, '<span class="wiki-link" onclick="loadWikiNote(\'$1\')">$1</span>');
  // External file links
  html = html.replace(/\[(.*?)\]\(file:\/\/(.*?)\)/g, '<a href="file://$2" class="text-accent" style="text-decoration:underline;" target="_blank" onclick="event.stopPropagation()">$1</a>');
  return html;
}

window.loadWikiNote = loadWikiNote;

function initSecondBrainActions() {
  const btnSync = $('#btn-sync-sb');
  const logEl   = $('#sb-log');
  const progEl  = $('#sb-progress');

  if (btnSync) {
    btnSync.addEventListener('click', async () => {
      btnSync.disabled = true;
      if (progEl) { progEl.classList.remove('hidden'); setProgress(progEl, 20); }
      appendLog(logEl, 'Compilando bóveda del Second Brain...', 'info');

      try {
        const r = await fetch('/api/second_brain/build', { method: 'POST' });
        if (progEl) setProgress(progEl, 70);
        const d = await r.json();

        if (progEl) {
          setProgress(progEl, 100);
          setTimeout(() => progEl.classList.add('hidden'), 1500);
        }

        if (r.ok && d.status === 'ok') {
          appendLog(logEl, `Bóveda sincronizada: ${d.stats.total_proyectos} proyectos`, 'ok');
          showToast(`Second Brain: ${d.stats.total_proyectos} proyectos`, 'ok');

          const lastSyncEl = $('#sb-last-sync');
          if (lastSyncEl) lastSyncEl.textContent = new Date().toLocaleTimeString();

          await loadStatus();
          await loadWikiNotesList();
        } else {
          appendLog(logEl, `Error: ${d.detail || 'Fallo desconocido'}`, 'error');
          showToast('Error sincronizando bóveda', 'error');
        }
      } catch (err) {
        appendLog(logEl, `Fallo de red: ${err}`, 'error');
        if (progEl) progEl.classList.add('hidden');
      } finally {
        btnSync.disabled = false;
      }
    });
  }
}

/* =========================================================================
   WORKFLOW MODULE
   ========================================================================= */
async function loadWorkflowGacetas() {
  const listEl = $('#wf-gacetas-list');
  if (!listEl) return;
  listEl.innerHTML = '<li class="file-item file-item--loading"><span class="file-item__name text-muted">[ cargando gacetas... ]</span></li>';

  try {
    const year   = $('#scraper-year')?.value || 2026;
    const source = $('#wf-filter-source')?.value || 'all';
    const r      = await fetch(`/api/scraper/gacetas-summary?year=${year}&source=${source}`);
    const d      = await r.json();
    listEl.innerHTML = '';

    if (!d.gacetas?.length) {
      listEl.innerHTML = '<li class="file-item file-item--loading"><span class="file-item__name text-muted">[ sin gacetas en el corpus ]</span></li>';
      return;
    }

    d.gacetas.sort((a, b) => b.name.localeCompare(a.name));
    let allGacetas = d.gacetas;

    function renderGacetas(gacetas) {
      listEl.innerHTML = '';
      gacetas.forEach(gaceta => {
        const li = document.createElement('li');
        li.className = 'file-item';
        li.dataset.name = gaceta.name;
        li.setAttribute('role', 'option');
        const countLabel = gaceta.clave_count > 0 ? `[${gaceta.clave_count}]` : '[─]';
        const sizeKB = gaceta.size_bytes ? `${(gaceta.size_bytes/1024).toFixed(0)}KB` : '';
        li.innerHTML = `
          <span class="file-item__name" title="${escHtml(gaceta.name)}">▫ ${escHtml(gaceta.name)}</span>
          <span class="file-item__badge text-muted">${countLabel} ${sizeKB}</span>
        `;
        li.addEventListener('click', () => {
          listEl.querySelectorAll('.file-item').forEach(i => i.classList.remove('selected'));
          li.classList.add('selected');
          loadWorkflowGacetaKeys(gaceta.name);
        });
        listEl.appendChild(li);
      });
    }

    renderGacetas(allGacetas);

    const wfSearch = $('#wf-search');
    if (wfSearch) {
      wfSearch.oninput = () => {
        const q = wfSearch.value.toLowerCase();
        renderGacetas(allGacetas.filter(g => g.name.toLowerCase().includes(q)));
      };
    }

    const first = listEl.querySelector('.file-item');
    if (first) first.click();

  } catch (err) {
    listEl.innerHTML = `<li class="file-item"><span class="file-item__name text-alert">[ error: ${escHtml(String(err))} ]</span></li>`;
  }
}

async function loadWorkflowGacetaKeys(gacetaName) {
  const titleEl = $('#wf-selected-gaceta');
  const countEl = $('#wf-keys-count');
  const tbodyEl = $('#wf-keys-tbody');

  if (titleEl) titleEl.textContent = gacetaName;
  if (tbodyEl) tbodyEl.innerHTML = '<tr><td colspan="6" class="text-muted text-center" style="padding:24px;">[ cargando claves... ]</td></tr>';

  try {
    const year = $('#scraper-year')?.value || 2026;
    const r    = await fetch(`/api/scraper/gaceta-keys?gaceta_name=${encodeURIComponent(gacetaName)}&year=${year}`);
    const d    = await r.json();

    if (countEl) countEl.textContent = `${d.claves?.length || 0} claves extraídas`;
    tbodyEl.innerHTML = '';

    if (!d.claves?.length) {
      tbodyEl.innerHTML = '<tr><td colspan="6" class="text-muted text-center" style="padding:24px;">[ sin claves SINAT — ejecuta conversión MD primero ]</td></tr>';
      return;
    }

    d.claves.forEach(item => {
      const tr = document.createElement('tr');
      const cell = (has) => has
        ? '<span class="text-ok" style="font-weight:700;">[ ✓ ]</span>'
        : '<span class="text-muted">[ ─ ]</span>';

      tr.innerHTML = `
        <td style="font-family:var(--font-mono); font-weight:700; color:var(--color-amber);">${escHtml(item.clave)}</td>
        <td>${cell(item.has_pdf_estudio)}</td>
        <td>${cell(item.has_pdf_resolutivo)}</td>
        <td>${cell(item.has_extraction)}</td>
        <td>${cell(item.has_inference)}</td>
        <td>
          <a class="btn" style="font-size:9px; padding:2px 6px;" href="/api/scraper/download-clave?clave=${encodeURIComponent(item.clave)}" onclick="event.preventDefault(); triggerDownload('${escHtml(item.clave)}')" aria-label="Descargar clave ${escHtml(item.clave)}">▸ DL</a>
        </td>
      `;
      tbodyEl.appendChild(tr);
    });
  } catch (err) {
    tbodyEl.innerHTML = `<tr><td colspan="6" class="text-alert text-center" style="padding:24px;">[ error: ${escHtml(String(err))} ]</td></tr>`;
  }
}

function triggerDownload(clave) {
  const year    = $('#scraper-year')?.value || '2026';
  const logEl   = $('#scraper-log');
  const progEl  = $('#scraper-progress');
  const pctEl   = $('#scraper-pct');

  showToast(`Iniciando descarga: ${clave}`, 'info');
  appendLog(logEl, `Descargando clave: ${clave}`, 'info');
  setProgress(progEl, 0);

  const es = new EventSource(`/api/scraper/download-clave?clave=${encodeURIComponent(clave)}&year=${year}`);

  es.onmessage = e => {
    const evt = JSON.parse(e.data);
    if (evt.pct !== undefined) {
      setProgress(progEl, evt.pct);
      if (pctEl) pctEl.textContent = `${Math.round(evt.pct)}%`;
    }
    const level = evt.level === 'warning' ? 'warn'
                : evt.status === 'complete' ? 'ok'
                : evt.status === 'error' ? 'error' : 'info';
    appendLog(logEl, evt.msg || evt.status, level);
    if (evt.status === 'complete' || evt.status === 'error') {
      es.close();
      if (evt.status === 'complete') {
        showToast(`✓ Descarga completada: ${clave}`, 'ok');
        loadWorkflowGacetas();
      } else {
        showToast(`Error descargando ${clave}`, 'error');
      }
    }
  };

  es.onerror = () => { es.close(); };
}

window.triggerDownload = triggerDownload;

/* =========================================================================
   INIT
   ========================================================================= */
document.addEventListener('DOMContentLoaded', () => {
  startClock();
  initTabs();
  initCorpusActions();
  initMdLabActions();
  initScraperActions();
  initSecondBrainActions();

  activateTab('CORPUS_PDF');

  loadStatus();
  setInterval(loadStatus, 30_000);
});
