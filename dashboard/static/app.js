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
  chatHistory:  [],
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
  if (tabId === 'WORKFLOW')      { loadWorkflowGacetas(); loadDataWarehouseStatus(); }
  if (tabId === 'MODEL_CHAT')    activateModelChatTab();
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
  }).on('mouseleave', () => tooltip.style('display', 'none'))
    .on('click', (e, d) => {
      e.stopPropagation();
      tooltip.style('display', 'none');
      if (typeof showGraphNodeDetail === 'function') {
        showGraphNodeDetail({
          id:         d.id,
          label:      d.label,
          type:       d.type,
          year:       d.year,
          degree:     d.degree,
          community:  d.com,
        });
      }
    });

  // Click on SVG background closes the detail panel
  svg.on('click', () => {
    const panel = document.getElementById('graph-detail-panel');
    if (panel) panel.classList.remove('graph-detail-panel--open');
  });

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
  const btnDownloadRemaining = $('#btn-download-remaining');
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
    if (btnDownloadRemaining) btnDownloadRemaining.disabled = true;

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
        if (btnDownloadRemaining) btnDownloadRemaining.disabled = false;
        showToast(`✓ ${label} completado`, 'ok');
        loadCorpus();
        loadMdList();
      } else if (evt.status === 'error') {
        es.close();
        if (btnExtractKeys) btnExtractKeys.disabled = false;
        if (btnRunPipeline) btnRunPipeline.disabled = false;
        if (btnDownloadRemaining) btnDownloadRemaining.disabled = false;
        showToast(`Error en ${label}`, 'error');
      }
    };

    es.onerror = () => {
      appendLog(logEl, 'SSE desconectado', 'error');
      es.close();
      if (btnExtractKeys) btnExtractKeys.disabled = false;
      if (btnRunPipeline) btnRunPipeline.disabled = false;
      if (btnDownloadRemaining) btnDownloadRemaining.disabled = false;
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

  if (btnDownloadRemaining) {
    btnDownloadRemaining.addEventListener('click', () => {
      const year = $('#scraper-year')?.value || '2026';
      runSse(`/api/scraper/download-remaining?year=${year}`, `Descarga pendientes ${year}`);
    });
  }
}

/* =========================================================================
   LLAMA SERVER ACTIONS
   ========================================================================= */
async function updateLlamaStatus() {
  const badge = $('#llama-status-badge');
  const btnStart = $('#btn-start-llama');
  const btnStop = $('#btn-stop-llama');
  if (!badge) return;

  try {
    const res = await fetch('/api/llama/status');
    const data = await res.json();
    
    badge.textContent = data.status.toUpperCase();
    if (data.status === 'online') {
      badge.style.color = '#00FF66';
      if (btnStart) btnStart.disabled = true;
      if (btnStop) btnStop.disabled = false;
    } else if (data.status === 'booting') {
      badge.style.color = '#FFB000';
      if (btnStart) btnStart.disabled = true;
      if (btnStop) btnStop.disabled = false;
    } else {
      badge.style.color = '#888888';
      if (btnStart) btnStart.disabled = false;
      if (btnStop) btnStop.disabled = true;
    }
  } catch (err) {
    badge.textContent = 'OFFLINE';
    badge.style.color = '#888888';
    if (btnStart) btnStart.disabled = false;
    if (btnStop) btnStop.disabled = true;
  }
}

function initLlamaServerActions() {
  const btnStart = $('#btn-start-llama');
  const btnStop = $('#btn-stop-llama');

  if (btnStart) {
    btnStart.addEventListener('click', async () => {
      btnStart.disabled = true;
      showToast('Iniciando llama-server local...', 'info');
      try {
        const res = await fetch('/api/llama/start', { method: 'POST' });
        const data = await res.json();
        showToast(data.msg, data.status === 'error' ? 'error' : 'ok');
      } catch (err) {
        showToast('Error de comunicación con el API', 'error');
      }
      updateLlamaStatus();
    });
  }

  if (btnStop) {
    btnStop.addEventListener('click', async () => {
      btnStop.disabled = true;
      showToast('Deteniendo llama-server...', 'info');
      try {
        const res = await fetch('/api/llama/stop', { method: 'POST' });
        const data = await res.json();
        showToast(data.msg, 'ok');
      } catch (err) {
        showToast('Error de comunicación con el API', 'error');
      }
      updateLlamaStatus();
    });
  }

  // Initial update
  updateLlamaStatus();
  // Poll status every 5 seconds
  setInterval(updateLlamaStatus, 5000);
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

    // Use marked.js if available, fallback to custom renderer
    if (viewerEl) {
      viewerEl.classList.add('md-viewer');
      if (typeof marked !== 'undefined') {
        viewerEl.innerHTML = marked.parse(d.content || '');
      } else {
        viewerEl.innerHTML = renderMarkdownWithWikiLinks(d.content);
      }
    }

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
  if (tbodyEl) tbodyEl.innerHTML = '<tr><td colspan="11" class="text-muted text-center" style="padding:24px;">[ cargando claves... ]</td></tr>';

  try {
    const year = $('#scraper-year')?.value || 2026;
    const r    = await fetch(`/api/scraper/gaceta-keys?gaceta_name=${encodeURIComponent(gacetaName)}&year=${year}`);
    const d    = await r.json();

    if (countEl) countEl.textContent = `${d.claves?.length || 0} claves extraídas`;
    tbodyEl.innerHTML = '';

    if (!d.claves?.length) {
      tbodyEl.innerHTML = '<tr><td colspan="11" class="text-muted text-center" style="padding:24px;">[ sin claves SINAT — ejecuta conversión MD primero ]</td></tr>';
      return;
    }

    d.claves.forEach(item => {
      const tr = document.createElement('tr');
      const cell = (has) => has
        ? '<span class="text-ok" style="font-weight:700;">[ ✓ ]</span>'
        : '<span class="text-muted">[ ─ ]</span>';

      const projName = item.project_name || `Proyecto ${item.clave}`;
      const location = item.location || 'Desconocida';

      tr.innerHTML = `
        <td style="font-family:var(--font-mono); font-weight:700; color:var(--color-amber);">${escHtml(item.clave)}</td>
        <td style="font-size:11px; max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${escHtml(projName)}">${escHtml(projName)}</td>
        <td style="font-size:11px; max-width:120px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${escHtml(location)}">${escHtml(location)}</td>
        <td>${cell(item.has_pdf_estudio)}</td>
        <td>${cell(item.has_pdf_resumen)}</td>
        <td>${cell(item.has_pdf_resolutivo)}</td>
        <td>${cell(item.has_md_estudio)}</td>
        <td>${cell(item.has_md_resumen)}</td>
        <td>${cell(item.has_md_resolutivo)}</td>
        <td>${cell(item.has_inference)}</td>
        <td>
          <a class="btn" style="font-size:9px; padding:2px 6px;" href="/api/scraper/download-clave?clave=${encodeURIComponent(item.clave)}" onclick="event.preventDefault(); triggerDownload('${escHtml(item.clave)}')" aria-label="Descargar clave ${escHtml(item.clave)}">▸ DL</a>
        </td>
      `;
      tbodyEl.appendChild(tr);
    });
  } catch (err) {
    tbodyEl.innerHTML = `<tr><td colspan="11" class="text-alert text-center" style="padding:24px;">[ error: ${escHtml(String(err))} ]</td></tr>`;
  }

  // Refresh kanban if visible
  const kanbanView = $('#wf-kanban-view');
  if (kanbanView && kanbanView.style.display !== 'none') {
    const year = $('#scraper-year')?.value || 2026;
    fetch(`/api/scraper/gaceta-keys?gaceta_name=${encodeURIComponent(gacetaName)}&year=${year}`)
      .then(r => r.json())
      .then(d => { if (d.claves) renderKanbanBoard(d.claves); })
      .catch(() => {});
  }
}

function triggerDownload(clave) {
  const year    = $('#scraper-year')?.value || '2026';
  const logEl   = $('#scraper-log');
  const progEl  = $('#scraper-progress');
  const pctEl   = $('#scraper-pct');

  showToast(`Iniciando descarga: ${clave}`, 'info');
  appendLog(logEl, `▸ Descargando clave: ${clave}`, 'info');
  setProgress(progEl, 0);

  const es = new EventSource(`/api/scraper/download-clave?clave=${encodeURIComponent(clave)}&year=${year}`);

  es.onmessage = async e => {
    const evt = JSON.parse(e.data);
    if (evt.pct !== undefined) {
      setProgress(progEl, evt.pct);
      if (pctEl) pctEl.textContent = `${Math.round(evt.pct)}%`;
    }

    // Evento de reintento: badge amarillo especial
    if (evt.status === 'retry') {
      appendLog(logEl, `⟳ Reintento ${evt.attempt}/${evt.max_retries} — ${evt.msg || ''}`, 'warn');
      showToast(`⟳ Reintentando descarga (${evt.attempt}/${evt.max_retries})...`, 'warn');
      return;
    }

    const level = evt.level === 'warning' ? 'warn'
                : evt.status === 'complete' ? 'ok'
                : evt.status === 'error' ? 'error' : 'info';
    appendLog(logEl, evt.msg || evt.status, level);

    if (evt.status === 'complete' || evt.status === 'error') {
      es.close();
      if (evt.status === 'complete') {
        // Calcular badges de tipo de documento
        const docBadges = [
          evt.n_resumenes  > 0 ? '<span title="Resumen Ejecutivo" style="color:var(--color-blue);font-weight:bold;">[R]</span>' : '<span style="opacity:0.3;">[R]</span>',
          evt.n_estudios   > 0 ? '<span title="Estudio de Impacto" style="color:var(--color-green);font-weight:bold;">[E]</span>' : '<span style="opacity:0.3;">[E]</span>',
          evt.n_resolutivos > 0 ? '<span title="Resolutivo" style="color:var(--color-amber);font-weight:bold;">[V]</span>' : '<span style="opacity:0.3;">[V]</span>',
        ].join(' ');

        // Badge de estado de completitud
        let statusBadge, toastMsg, toastLevel;
        if (evt.download_status === 'complete') {
          statusBadge = `✅ Completo ${docBadges}`;
          toastMsg = `✅ Descarga completa (3/3): ${clave}`;
          toastLevel = 'ok';
        } else if (evt.download_status === 'partial') {
          const totalDocs = (evt.n_resumenes > 0 ? 1 : 0) + (evt.n_estudios > 0 ? 1 : 0) + (evt.n_resolutivos > 0 ? 1 : 0);
          statusBadge = `⚠️ Parcial (${totalDocs}/3) ${docBadges}`;
          toastMsg = `⚠️ Descarga parcial (${totalDocs}/3): ${clave}`;
          toastLevel = 'warn';
        } else {
          statusBadge = `❌ Fallida`;
          toastMsg = `❌ Sin archivos descargados: ${clave}`;
          toastLevel = 'error';
        }

        // Insertar resumen de estado final en el log
        const statusDiv = document.createElement('div');
        statusDiv.className = 'log-line log-line--ok';
        statusDiv.style.cssText = 'border-left:3px solid var(--accent-color); padding-left:8px; margin-top:6px; font-family:var(--font-mono); font-size:10px;';
        statusDiv.innerHTML = `<span class="log-line__ts">[ESTADO]</span> <span class="log-line__msg">${statusBadge}</span>`;
        if (logEl) logEl.appendChild(statusDiv);

        showToast(toastMsg, toastLevel);

        // Preserve selected gaceta and reload its keys after refresh
        const selectedLi = $('#wf-gacetas-list .file-item.selected');
        const selectedGaceta = selectedLi?.dataset?.name || null;
        await loadWorkflowGacetas();
        if (selectedGaceta) {
          // Re-select the same gaceta in the refreshed list
          const listEl = $('#wf-gacetas-list');
          const targetLi = listEl?.querySelector(`.file-item[data-name="${CSS.escape(selectedGaceta)}"]`);
          if (targetLi) {
            listEl.querySelectorAll('.file-item').forEach(i => i.classList.remove('selected'));
            targetLi.classList.add('selected');
            await loadWorkflowGacetaKeys(selectedGaceta);
          }
        }
      } else {
        showToast(`❌ Error descargando ${clave}`, 'error');
      }
    }
  };

  es.onerror = () => { es.close(); };
}

window.triggerDownload = triggerDownload;

/* =========================================================================
   KANBAN BOARD RENDERER
   ========================================================================= */
function renderKanbanBoard(claves) {
  const pendingEl   = $('#kanban-pending');
  const extractedEl = $('#kanban-extracted');
  const inferredEl  = $('#kanban-inferred');
  if (!pendingEl || !extractedEl || !inferredEl) return;

  pendingEl.innerHTML   = '';
  extractedEl.innerHTML = '';
  inferredEl.innerHTML  = '';

  if (!claves || !claves.length) {
    pendingEl.innerHTML = '<span class="text-muted text-xs" style="padding:8px;">[ sin claves ]</span>';
    return;
  }

  claves.forEach(item => {
    const hasMd      = item.has_md_estudio || item.has_md_resumen || item.has_md_resolutivo;
    const hasInf     = item.has_inference;
    const hasPdf     = item.has_pdf_estudio || item.has_pdf_resumen || item.has_pdf_resolutivo;

    const state = hasInf ? 'inferred' : hasMd ? 'extracted' : 'pending';

    const badge = (has, label, cls) =>
      `<span class="kanban-badge ${has ? cls : 'kanban-badge--dim'}">${label}</span>`;

    const card = document.createElement('div');
    card.className = `kanban-card kanban-card--${state}`;
    card.setAttribute('title', item.project_name || item.clave);
    card.innerHTML = `
      <span class="kanban-card__clave">${escHtml(item.clave)}</span>
      <span class="kanban-card__name">${escHtml((item.project_name || 'Sin nombre').substring(0, 42))}</span>
      <div class="kanban-card__badges">
        ${badge(hasPdf, 'PDF', 'kanban-badge--info')}
        ${badge(hasMd,  'MD',  'kanban-badge--info')}
        ${badge(hasInf, 'INF', 'kanban-badge--ok')}
      </div>
    `;

    if (state === 'inferred')  inferredEl.appendChild(card);
    else if (state === 'extracted') extractedEl.appendChild(card);
    else pendingEl.appendChild(card);
  });
}

function initWorkflowKanbanToggle() {
  const btnTable  = $('#wf-view-table');
  const btnKanban = $('#wf-view-kanban');
  const tableView = $('#wf-table-view');
  const kanbanView = $('#wf-kanban-view');
  if (!btnTable || !btnKanban) return;

  btnTable.addEventListener('click', () => {
    tableView.style.display = '';
    kanbanView.style.display = 'none';
    btnTable.classList.add('btn--primary');
    btnKanban.classList.remove('btn--primary');
  });

  btnKanban.addEventListener('click', () => {
    tableView.style.display = 'none';
    kanbanView.style.display = '';
    btnKanban.classList.add('btn--primary');
    btnTable.classList.remove('btn--primary');
    // Render kanban from last loaded data
    const gaceta = $('#wf-selected-gaceta')?.textContent;
    if (gaceta && gaceta !== 'Ninguna Gaceta Seleccionada') {
      const year = $('#scraper-year')?.value || 2026;
      fetch(`/api/scraper/gaceta-keys?gaceta_name=${encodeURIComponent(gaceta)}&year=${year}`)
        .then(r => r.json())
        .then(d => { if (d.claves) renderKanbanBoard(d.claves); })
        .catch(() => {});
    }
  });
}

/* =========================================================================
   AUTOMATED DATA WAREHOUSE & QUALITY AUDITOR
   ========================================================================= */

// Escape HTML utility to prevent XSS
function escHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
}

async function loadDataWarehouseStatus() {
  const connDot = $('#dw-db-conn-dot');
  const connText = $('#dw-db-conn-text');
  const connBox = $('#dw-db-conn-box');
  
  const cntSemarnatProjects = $('#count-semarnat-projects');
  const cntProjectEvaluations = $('#count-project-evaluations');
  
  const qualityNoReport = $('#dw-quality-no-report');
  const qualityContainer = $('#dw-quality-container');
  const qualityGrid = $('#dw-quality-grid');
  const qualityDatasets = $('#dw-quality-datasets');

  try {
    const res = await fetch('/api/dw/status');
    const data = await res.json();
    
    // 1. Render DB Status
    if (data.db.connected) {
      if (connDot) { connDot.style.background = 'var(--color-green)'; connDot.style.boxShadow = '0 0 8px var(--color-green)'; }
      if (connText) connText.textContent = `Conectado (${data.db.latency_ms}ms)`;
      if (connBox) connBox.style.borderColor = 'rgba(39, 174, 96, 0.4)';
    } else {
      if (connDot) { connDot.style.background = 'var(--color-red)'; connDot.style.boxShadow = 'none'; }
      if (connText) connText.textContent = `Error de Conexión`;
      if (connBox) connBox.style.borderColor = 'rgba(231, 76, 60, 0.4)';
      console.error("DB Status error:", data.db.error);
    }
    
    // Render counts
    if (cntSemarnatProjects) cntSemarnatProjects.textContent = data.db.tables?.semarnat_projects?.count !== undefined ? `${data.db.tables.semarnat_projects.count} filas` : '─ filas';
    if (cntProjectEvaluations) cntProjectEvaluations.textContent = data.db.tables?.project_evaluations?.count !== undefined ? `${data.db.tables.project_evaluations.count} filas` : '─ filas';

    // 2. Render Quality Auditor
    const qKeys = Object.keys(data.quality || {});
    if (qKeys.length === 0) {
      if (qualityNoReport) qualityNoReport.style.display = 'block';
      if (qualityContainer) qualityContainer.style.display = 'none';
      return;
    }
    
    if (qualityNoReport) qualityNoReport.style.display = 'none';
    if (qualityContainer) qualityContainer.style.display = 'flex';

    // Aggregates
    const datasets = data.quality;
    let totalRows = 0;
    let totalDuplicates = 0;
    let totalRemoved = 0;
    let totalIngested = 0;

    Object.values(datasets).forEach((d) => {
      totalRows += d.total_rows || 0;
      totalDuplicates += d.duplicate_rows || 0;
      totalRemoved += d.rows_removed || 0;
      totalIngested += ((d.total_rows || 0) - (d.rows_removed || 0));
    });

    if (qualityGrid) {
      qualityGrid.innerHTML = `
        <div style="background:var(--bg-surface-dim); border:1px solid var(--border-color); padding:10px 12px; font-family:var(--font-mono);">
          <span style="font-size:9px; color:var(--text-muted); display:block; text-transform:uppercase;">Datasets Auditados</span>
          <span style="font-size:16px; font-weight:bold; color:var(--color-amber);">${qKeys.length}</span>
        </div>
        <div style="background:var(--bg-surface-dim); border:1px solid var(--border-color); padding:10px 12px; font-family:var(--font-mono);">
          <span style="font-size:9px; color:var(--text-muted); display:block; text-transform:uppercase;">Registros Totales</span>
          <span style="font-size:16px; font-weight:bold; color:var(--color-blue);">${totalRows}</span>
        </div>
        <div style="background:var(--bg-surface-dim); border:1px solid var(--border-color); padding:10px 12px; font-family:var(--font-mono);">
          <span style="font-size:9px; color:var(--text-muted); display:block; text-transform:uppercase;">Duplicados Auditados</span>
          <span style="font-size:16px; font-weight:bold; color:var(--color-red);">${totalDuplicates}</span>
        </div>
        <div style="background:var(--bg-surface-dim); border:1px solid var(--border-color); padding:10px 12px; font-family:var(--font-mono);">
          <span style="font-size:9px; color:var(--text-muted); display:block; text-transform:uppercase;">Registros Ingeridos</span>
          <span style="font-size:16px; font-weight:bold; color:var(--color-green);">${totalIngested}</span>
        </div>
      `;
    }

    // Breakdown lists
    if (qualityDatasets) {
      qualityDatasets.innerHTML = '';
      Object.entries(datasets).forEach(([name, d]) => {
        const ingested = d.total_rows - d.rows_removed;
        const ingestedPct = d.total_rows > 0 ? (ingested / d.total_rows) * 100 : 0;
        const removedPct = d.total_rows > 0 ? (d.rows_removed / d.total_rows) * 100 : 0;
        
        const card = document.createElement('div');
        card.style.cssText = 'border:1px solid var(--border-color); background:var(--bg-surface-dim); padding:12px; display:flex; flex-direction:column; gap:10px;';
        
        let nullsHtml = '';
        if (d.missing_values && Object.keys(d.missing_values).length > 0) {
          nullsHtml = `
            <div style="margin-top:6px;">
              <span style="font-size:8px; color:var(--text-muted); text-transform:uppercase; font-family:var(--font-mono); font-weight:bold; display:block; margin-bottom:4px;">Nulos Detectados</span>
              <div style="display:flex; flex-direction:column; gap:4px; font-family:var(--font-mono); font-size:9px; background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.05); padding:6px 10px;">
                ${Object.entries(d.missing_values).map(([col, info]) => `
                  <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span style="color:var(--color-red);">⚠️ ${escHtml(col)}</span>
                    <span style="color:var(--text-muted);">${info.count} (${info.percentage}%)</span>
                  </div>
                `).join('')}
              </div>
            </div>
          `;
        } else {
          nullsHtml = `
            <div style="margin-top:6px; font-family:var(--font-mono); font-size:9px; color:var(--color-green); background:rgba(39,174,96,0.1); border:1px solid rgba(39,174,96,0.2); padding:6px 10px; display:flex; align-items:center; gap:6px;">
              <span>✓ No se detectaron valores nulos o vacíos.</span>
            </div>
          `;
        }

        card.innerHTML = `
          <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid rgba(255,255,255,0.08); padding-bottom:6px;">
            <span style="font-family:var(--font-mono); font-weight:bold; font-size:11px; color:var(--color-amber);">${escHtml(name)}</span>
            <span style="font-family:var(--font-mono); font-size:9px; padding:2px 6px; border:1px solid ${removedPct > 10 ? 'var(--color-red)' : 'var(--color-green)'}; color:${removedPct > 10 ? 'var(--color-red)' : 'var(--color-green)'}; background:rgba(0,0,0,0.2);">
              ${removedPct > 10 ? 'ALTA ANOMALÍA' : 'SLA VERIFICADO'}
            </span>
          </div>

          <!-- Progress Bar -->
          <div style="display:flex; flex-direction:column; gap:4px;">
            <div style="display:flex; justify-content:space-between; font-family:var(--font-mono); font-size:9px;">
              <span class="text-muted">Limpieza de Datos:</span>
              <span style="color:var(--color-green); font-weight:bold;">${ingestedPct.toFixed(1)}%</span>
            </div>
            <div class="progress-bar" style="height:10px; border:1px solid rgba(0,0,0,0.3); background:rgba(255,255,255,0.05); display:flex; overflow:hidden;">
              <div style="width:${ingestedPct}%; background:var(--color-green); height:100%;"></div>
              <div style="width:${removedPct}%; background:var(--color-red); height:100%;"></div>
            </div>
            <div style="display:flex; justify-content:space-between; font-family:var(--font-mono); font-size:8px; color:var(--text-muted); text-transform:uppercase;">
              <span>${ingested} ingeridos</span>
              <span>${d.rows_removed} descartados</span>
            </div>
          </div>

          <!-- Core metrics -->
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:6px; font-family:var(--font-mono); font-size:9px; margin-top:4px;">
            <div style="background:rgba(0,0,0,0.15); border:1px solid rgba(255,255,255,0.03); padding:4px 8px; display:flex; justify-content:space-between;">
              <span class="text-muted">Total:</span>
              <span>${d.total_rows}</span>
            </div>
            <div style="background:rgba(0,0,0,0.15); border:1px solid rgba(255,255,255,0.03); padding:4px 8px; display:flex; justify-content:space-between;">
              <span class="text-muted">Duplicados:</span>
              <span>${d.duplicate_rows}</span>
            </div>
            <div style="background:rgba(0,0,0,0.15); border:1px solid rgba(255,255,255,0.03); padding:4px 8px; display:flex; justify-content:space-between;">
              <span class="text-muted">Rangos ERR:</span>
              <span>${d.range_violations}</span>
            </div>
            <div style="background:rgba(0,0,0,0.15); border:1px solid rgba(255,255,255,0.03); padding:4px 8px; display:flex; justify-content:space-between;">
              <span class="text-muted">Formatos ERR:</span>
              <span>${d.regex_violations}</span>
            </div>
          </div>

          <!-- Null breakdown -->
          ${nullsHtml}
        `;
        qualityDatasets.appendChild(card);
      });
    }

  } catch (err) {
    console.error("loadDataWarehouseStatus error:", err);
  }
}

function initWorkflowSubTabs() {
  const tabGacetas = $('#subtab-gacetas');
  const tabDw = $('#subtab-dw');
  const panelGacetas = $('#wf-gacetas-subpanel');
  const panelDw = $('#wf-dw-subpanel');

  if (tabGacetas && tabDw && panelGacetas && panelDw) {
    tabGacetas.onclick = () => {
      tabGacetas.classList.add('active');
      tabGacetas.style.background = 'var(--bg-surface)';
      tabGacetas.style.color = 'var(--accent-color)';
      tabGacetas.style.borderColor = 'var(--border-color)';
      
      tabDw.classList.remove('active');
      tabDw.style.background = 'none';
      tabDw.style.color = 'var(--text-muted)';
      tabDw.style.borderColor = 'transparent';
      
      panelGacetas.style.display = 'flex';
      panelDw.style.display = 'none';
    };

    tabDw.onclick = () => {
      tabDw.classList.add('active');
      tabDw.style.background = 'var(--bg-surface)';
      tabDw.style.color = 'var(--accent-color)';
      tabDw.style.borderColor = 'var(--border-color)';
      
      tabGacetas.classList.remove('active');
      tabGacetas.style.background = 'none';
      tabGacetas.style.color = 'var(--text-muted)';
      tabGacetas.style.borderColor = 'transparent';
      
      panelGacetas.style.display = 'none';
      panelDw.style.display = 'flex';
      
      loadDataWarehouseStatus();
    };
  }

  // Hook run-dw button
  const btnRunDw = $('#btn-run-dw');
  if (btnRunDw) {
    btnRunDw.onclick = () => runDataWarehousePipeline();
  }
}

function runDataWarehousePipeline() {
  const btnRunDw = $('#btn-run-dw');
  const progressContainer = $('#dw-progress-container');
  const progressFill = $('#dw-progress .progress-bar__fill');
  const stageText = $('#dw-stage-text');
  const pctText = $('#dw-pct-text');
  const logEl = $('#dw-log');

  if (btnRunDw) btnRunDw.disabled = true;
  if (progressContainer) progressContainer.style.display = 'flex';
  if (progressFill) progressFill.style.width = '0%';
  if (pctText) pctText.textContent = '0%';
  if (stageText) stageText.textContent = 'Iniciando Pipeline...';
  
  if (logEl) {
    logEl.innerHTML = '';
    appendLog(logEl, 'Iniciando conexión con el pipeline de ingesta...', 'info');
  }

  const es = new EventSource('/api/dw/run-pipeline');
  
  es.onmessage = e => {
    const evt = JSON.parse(e.data);
    
    if (evt.status === 'progress') {
      if (progressFill) progressFill.style.width = `${evt.pct}%`;
      if (pctText) pctText.textContent = `${evt.pct}%`;
      if (stageText) stageText.textContent = evt.msg || evt.stage;
      appendLog(logEl, `[STAGE: ${evt.stage.toUpperCase()}] ${evt.msg}`, 'info');
    } else if (evt.status === 'log') {
      let level = 'info';
      if (evt.msg.includes('[Error]') || evt.msg.includes('Error')) level = 'error';
      else if (evt.msg.includes('[Warning]') || evt.msg.includes('Warning')) level = 'warn';
      else if (evt.msg.includes('SUCCESSFULLY') || evt.msg.includes('initialized')) level = 'ok';
      
      appendLog(logEl, evt.msg, level);
    } else if (evt.status === 'complete' || evt.status === 'error') {
      es.close();
      if (btnRunDw) btnRunDw.disabled = false;
      
      if (evt.status === 'complete') {
        showToast('✓ Pipeline DW completado con éxito', 'ok');
        appendLog(logEl, 'PIPELINE COMPLETADO CON ÉXITO.', 'ok');
        if (progressContainer) progressContainer.style.display = 'none';
        loadDataWarehouseStatus();
      } else {
        showToast('Error en pipeline DW', 'error');
        appendLog(logEl, `PIPELINE DETENIDO POR ERROR: ${evt.msg}`, 'error');
      }
    }
  };

  es.onerror = () => {
    es.close();
    if (btnRunDw) btnRunDw.disabled = false;
    appendLog(logEl, 'Conexión SSE perdida con el servidor.', 'error');
  };
}


/* =========================================================================
   TAB 7 — MODEL_CHAT
   ========================================================================= */
const CHAT_STORAGE_KEY = 'zohar_chat_history';
let isChatInit = false;

function initModelChatActions() {
  if (isChatInit) return;
  isChatInit = true;

  // Restore session history
  try {
    const saved = sessionStorage.getItem(CHAT_STORAGE_KEY);
    if (saved) State.chatHistory = JSON.parse(saved);
  } catch (_) {}

  const btnSend  = $('#btn-chat-send');
  const inputChat = $('#chat-user-input');
  const btnClear = $('#btn-chat-clear');

  if (btnSend && inputChat) {
    btnSend.onclick = () => sendChatMessage();
    inputChat.onkeydown = (e) => {
      if (e.key === 'Enter') sendChatMessage();
    };
  }

  if (btnClear) {
    btnClear.addEventListener('click', () => {
      State.chatHistory = [];
      try { sessionStorage.removeItem(CHAT_STORAGE_KEY); } catch (_) {}
      const messagesLog = $('#chat-messages-log');
      if (messagesLog) {
        messagesLog.innerHTML = '';
        const msg = document.createElement('div');
        msg.className = 'log-line log-line--info';
        msg.innerHTML = '<span class="log-line__ts">[SISTEMA]</span> <span class="log-line__msg text-muted">Historial de sesión borrado.</span>';
        messagesLog.appendChild(msg);
      }
      showToast('Historial de chat borrado', 'info');
    });
  }
}

async function activateModelChatTab() {
  initModelChatActions();
  await loadModelChatStatus();
  await loadModelChatTools();
  await populateChatClaveSelect();
  await populateChatEvalSelect();
}

async function loadModelChatStatus() {
  const provVal = $('#chat-provider-val');
  const modVal = $('#chat-model-val');
  const metaEl = $('#chat-model-meta');

  try {
    const res = await fetch('/api/model/status');
    if (!res.ok) throw new Error('API status failure');
    const data = await res.json();
    if (provVal) provVal.textContent = data.provider.toUpperCase();
    if (modVal) modVal.textContent = data.model;
    if (metaEl) metaEl.textContent = `Proveedor: ${data.provider.toUpperCase()} (${data.model})`;
  } catch (err) {
    console.error('loadModelChatStatus error:', err);
  }
}

async function loadModelChatTools() {
  const toolsList = $('#chat-tools-list');
  if (!toolsList) return;
  toolsList.innerHTML = '<span class="text-muted">[ Cargando herramientas... ]</span>';

  try {
    const res = await fetch('/api/model/tools');
    const data = await res.json();
    toolsList.innerHTML = '';
    
    if (data.tools) {
      data.tools.forEach(tool => {
        const item = document.createElement('div');
        item.style.cssText = 'background:var(--bg-surface-dim); border:1px solid var(--border-color); padding:8px 10px; font-family:var(--font-mono); font-size:10px; margin-bottom:8px;';
        item.innerHTML = `
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; font-weight:bold; color:var(--color-amber);">
            <span>⚡ ${tool.name}()</span>
            <span style="font-size:8px; border:1px solid var(--color-green); color:var(--color-green); padding:0 4px; background:rgba(39, 174, 96, 0.1);">DISPONIBLE</span>
          </div>
          <span style="color:var(--text-muted); display:block; font-family:var(--font-sans); line-height:1.3;">${tool.description}</span>
        `;
        toolsList.appendChild(item);
      });
    }
  } catch (err) {
    toolsList.innerHTML = '<span class="text-alert">[ Error cargando herramientas ]</span>';
  }
}

async function populateChatEvalSelect() {
  const select = $('#chat-eval-select');
  const inputChat = $('#chat-user-input');
  if (!select) return;

  try {
    const res = await fetch('/api/eval/questions');
    const d = await res.json();
    if (d.questions) {
      d.questions.forEach(q => {
        const opt = document.createElement('option');
        opt.value = q.question;
        opt.textContent = q.label || q.question.substring(0, 30);
        select.appendChild(opt);
      });
    }

    select.onchange = () => {
      if (select.value && inputChat) {
        inputChat.value = select.value;
      }
    };
  } catch (err) {
    console.error('populateChatEvalSelect error:', err);
  }
}

async function populateChatClaveSelect() {
  const select = $('#chat-clave-select');
  if (!select) return;

  const firstOpt = select.options[0];
  select.innerHTML = '';
  select.appendChild(firstOpt);

  try {
    const res = await fetch('/api/second_brain/notes');
    const d = await res.json();
    
    if (d.notes) {
      const entities = d.notes.filter(n => n.category === '02_Entities');
      entities.sort((a, b) => a.title.localeCompare(b.title));
      entities.forEach(entity => {
        const opt = document.createElement('option');
        opt.value = entity.title;
        opt.textContent = entity.title;
        select.appendChild(opt);
      });
    }
  } catch (err) {
    console.error('populateChatClaveSelect error:', err);
  }
}

async function sendChatMessage() {
  const inputChat = $('#chat-user-input');
  const messagesLog = $('#chat-messages-log');
  const btnSend = $('#btn-chat-send');
  const claveSelect = $('#chat-clave-select');

  if (!inputChat || !messagesLog || !btnSend) return;

  const text = inputChat.value.trim();
  if (!text) return;

  inputChat.value = '';
  btnSend.disabled = true;

  // Render User Message
  const userDiv = document.createElement('div');
  userDiv.className = 'log-line log-line--info';
  userDiv.style.marginBottom = '12px';
  userDiv.innerHTML = `<span class="log-line__ts">[USUARIO]</span> <span class="log-line__msg" style="color:var(--color-amber);">${escHtml(text)}</span>`;
  messagesLog.appendChild(userDiv);
  messagesLog.scrollTop = messagesLog.scrollHeight;

  // Add typing placeholder
  const systemDiv = document.createElement('div');
  systemDiv.className = 'log-line';
  systemDiv.style.marginBottom = '12px';
  systemDiv.innerHTML = `<span class="log-line__ts">[ZOHAR-AI]</span> <span class="log-line__msg cursor-blink">... analizando ...</span>`;
  messagesLog.appendChild(systemDiv);
  messagesLog.scrollTop = messagesLog.scrollHeight;

  const clave = claveSelect ? claveSelect.value : '';

  try {
    const payload = {
      message: text,
      clave: clave,
      history: State.chatHistory
    };

    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    
    // Remove placeholder
    messagesLog.removeChild(systemDiv);

    // Render System Response
    if (data.tool_calls && data.tool_calls.length > 0) {
      data.tool_calls.forEach(call => {
        const toolDiv = document.createElement('div');
        toolDiv.className = 'log-line';
        toolDiv.style.cssText = 'border-left: 2px solid var(--color-amber); padding-left: var(--space-2); margin-bottom: 12px; background: rgba(243, 156, 18, 0.05);';
        const argsStr = typeof call.arguments === 'object' ? JSON.stringify(call.arguments) : String(call.arguments);
        toolDiv.innerHTML = `
          <span class="log-line__ts" style="color:var(--color-amber);">[TOOL_CALL]</span>
          <span class="log-line__msg" style="font-family:var(--font-mono); font-size:10px; color:var(--color-amber); font-weight:bold;">⚡ ${call.name}(${escHtml(argsStr)})</span>
          <div style="font-family:var(--font-mono); font-size:9px; color:var(--text-muted); margin-top:4px; max-height:120px; overflow-y:auto; white-space:pre-wrap; border:1px solid var(--border-color-dim); padding:6px; background:rgba(0,0,0,0.3);">
[RESULTADO]:
${escHtml(call.result)}
          </div>
        `;
        messagesLog.appendChild(toolDiv);
      });
    }

    const responseDiv = document.createElement('div');
    responseDiv.className = 'log-line';
    responseDiv.style.marginBottom = '12px';
    responseDiv.innerHTML = `
      <span class="log-line__ts">[ZOHAR-AI]</span>
      <span class="log-line__msg" style="white-space:pre-wrap;">${escHtml(data.response)}</span>
      <div style="font-size:9px; color:var(--text-muted); margin-top:4px; font-family:var(--font-mono);">
        [ Modelo: ${data.model} ]
      </div>
    `;
    messagesLog.appendChild(responseDiv);
    messagesLog.scrollTop = messagesLog.scrollHeight;

    // Update history + persist to sessionStorage
    State.chatHistory.push({ role: 'user', content: text });
    State.chatHistory.push({ role: 'assistant', content: data.response });
    try { sessionStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(State.chatHistory)); } catch (_) {}

  } catch (err) {
    if (messagesLog.contains(systemDiv)) {
      messagesLog.removeChild(systemDiv);
    }
    const errDiv = document.createElement('div');
    errDiv.className = 'log-line log-line--error';
    errDiv.style.marginBottom = '12px';
    errDiv.innerHTML = `<span class="log-line__ts">[ERROR]</span> <span class="log-line__msg">Error de comunicación: ${err.message}</span>`;
    messagesLog.appendChild(errDiv);
    messagesLog.scrollTop = messagesLog.scrollHeight;
  } finally {
    btnSend.disabled = false;
  }
}


/* =========================================================================
   LIVE UPDATES SSE WATCHER
   ========================================================================= */
function initLiveUpdates() {
  const sseUrl = '/api/events/live-updates';
  console.log('Iniciando suscripción a eventos en tiempo real:', sseUrl);
  
  let es = new EventSource(sseUrl);

  es.onmessage = (e) => {
    try {
      const evt = JSON.parse(e.data);
      if (evt.type === 'ping') return; // Keep-alive

      console.log('Evento en tiempo real recibido:', evt);
      showToast(`Actualización detectada: ${evt.type}`, 'info');

      if (evt.type === 'pdfs_updated') {
        // Recargar PDFs
        if (typeof loadCorpusPdfs === 'function') loadCorpusPdfs();
        if (typeof loadWorkflowGacetas === 'function') loadWorkflowGacetas();
        if (typeof loadStatus === 'function') loadStatus();
      } else if (evt.type === 'extractions_updated') {
        // Recargar Markdown extractions y Second Brain
        if (typeof loadExtractions === 'function') loadExtractions();
        if (typeof loadWikiNotesList === 'function') loadWikiNotesList();
        if (typeof loadStatus === 'function') loadStatus();
      } else if (evt.type === 'inferences_updated') {
        // Recargar inferencias analíticas
        if (typeof loadInferences === 'function') loadInferences();
        if (typeof loadDataWarehouseStatus === 'function') loadDataWarehouseStatus();
        if (typeof loadStatus === 'function') loadStatus();
      }
    } catch (err) {
      console.error('Error parseando evento SSE:', err);
    }
  };

  es.onerror = () => {
    console.warn('Conexión SSE de Live Updates interrumpida. Reintentando en 5 segundos...');
    es.close();
    setTimeout(initLiveUpdates, 5000);
  };
}

window.initLiveUpdates = initLiveUpdates;


/* =========================================================================
   INIT
   ========================================================================= */
document.addEventListener('DOMContentLoaded', () => {
  startClock();
  initTabs();
  initCorpusActions();
  initMdLabActions();
  initScraperActions();
  initLlamaServerActions();
  initSecondBrainActions();
  initRSIActions();
  initRsiScraperActions();
  initWorkflowSubTabs();
  initWorkflowKanbanToggle();
  initBatchInference();
  initGraphDetailPanel();
  initLiveUpdates(); // Suscripción activa a SSE para cambios en archivos
  initTelemetryStream(); // Suscripción activa a telemetría en tiempo real y salud de servidores

  activateTab('CORPUS_PDF');

  loadStatus();
  setInterval(loadStatus, 30_000);
});

let rsiPollHandle = null;

async function toggleAtomicRSI(enable) {
  try {
    const res = await fetch('/api/rsi/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enable: enable })
    });
    const data = await res.json();
    const toggleEl = document.getElementById('toggle-atomic-rsi');
    if (toggleEl) toggleEl.checked = data.active;
    showToast(data.msg, data.active ? 'ok' : 'info');
  } catch (err) {
    showToast(`Error de conexión al cambiar Toggle RSI: ${err.message}`, 'error');
  }
}

async function syncAtomicRSIStatus() {
  try {
    const res = await fetch('/api/rsi/toggle-status');
    const data = await res.json();
    const toggleEl = document.getElementById('toggle-atomic-rsi');
    if (toggleEl) toggleEl.checked = data.active;
  } catch (err) {
    console.warn('Error obteniendo estado del Toggle RSI:', err);
  }
}

window.toggleAtomicRSI = toggleAtomicRSI;

async function toggleRSI() {
  const status = await fetch('/api/rsi/status').then(r => r.json());
  if (status.running) {
    await fetch('/api/rsi/stop', { method: 'POST' });
  } else {
    const iterations = document.getElementById('rsi-iterations').value || 15;
    await fetch(`/api/rsi/start?iterations=${iterations}`, { method: 'POST' });
  }
  refreshRSIStatus();
}

async function refreshRSIStatus() {
  const s = await fetch('/api/rsi/status').then(r => r.json());
  const meta = document.getElementById('rsi-status-meta');
  const btn = document.getElementById('rsi-toggle-btn');
  if (!meta || !btn) return;
  meta.textContent = s.running ? `EJECUTANDO (pid ${s.pid})` : '-- DETENIDO --';
  btn.textContent = s.running ? '\u25A0 DETENER RSI' : '\u25B6 INICIAR RSI';
}

function initRSIActions() {
  refreshRSIStatus();
  syncAtomicRSIStatus();
  if (rsiPollHandle) clearInterval(rsiPollHandle);
  rsiPollHandle = setInterval(refreshRSIStatus, 3000);
}


function initRsiScraperActions() {
  const btnRun = $('#btn-run-rsi-scraper');
  const logEl  = $('#rsi-scraper-log');
  const progEl = $('#rsi-scraper-progress');
  const pctEl  = $('#rsi-scraper-pct');

  if (!btnRun) return;

  btnRun.addEventListener('click', () => {
    const cycles = $('#rsi-scraper-cycles')?.value || 3;
    const dryRun = $('#rsi-scraper-dryrun')?.checked ? 'true' : 'false';

    if (State.rsiSseSource) {
      State.rsiSseSource.close();
      State.rsiSseSource = null;
    }

    appendLog(logEl, `Iniciando RSI Scraper (ciclos=${cycles}, dry_run=${dryRun})...`, 'info');
    setProgress(progEl, 0);
    if (pctEl) pctEl.textContent = '0%';
    btnRun.disabled = true;

    const url = `/api/rsi/run?cycles=${cycles}&dry_run=${dryRun}`;
    const es = new EventSource(url);
    State.rsiSseSource = es;

    es.onmessage = (e) => {
      try {
        const evt = JSON.parse(e.data);
        if (evt.pct !== undefined) {
          setProgress(progEl, evt.pct);
          if (pctEl) pctEl.textContent = `${evt.pct}%`;
        }

        const level = (evt.status === 'error' || evt.status === 'cycle_rollback') ? 'error'
                    : (evt.status === 'warning') ? 'warn'
                    : (evt.status === 'cycle_success' || evt.status === 'complete') ? 'ok'
                    : 'info';

        if (evt.msg) {
          appendLog(logEl, evt.msg, level);
        }

        if (evt.status === 'diff' && evt.diff) {
          appendLog(logEl, `Diff: +${evt.diff.added_lines} / -${evt.diff.removed_lines} líneas`, 'info');
        }

        if (evt.status === 'complete' || evt.status === 'error') {
          es.close();
          btnRun.disabled = false;
          showToast(evt.msg || 'RSI Finalizado', level === 'error' ? 'error' : 'ok');
        }
      } catch (err) {
        appendLog(logEl, `Parse error: ${err.message}`, 'error');
      }
    };

    es.onerror = () => {
      appendLog(logEl, 'Conexión SSE perdida con RSI', 'error');
      es.close();
      btnRun.disabled = false;
    };
  });
}

/* =========================================================================
   TELEMETRÍA EN TIEMPO REAL & GESTIÓN DE SERVIDORES
   ========================================================================= */
let telemetrySse = null;

function initTelemetryStream() {
  const sseUrl = '/api/telemetry/stream';
  if (telemetrySse) telemetrySse.close();

  telemetrySse = new EventSource(sseUrl);

  telemetrySse.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data.status !== 'telemetry') return;

      // 1. Actualizar Badges Llama-Server
      const llamaBadge = $('#srv-llama-badge');
      const llamaLat = $('#srv-llama-lat');
      if (llamaBadge) {
        llamaBadge.textContent = data.llama.status.toUpperCase();
        llamaBadge.style.color = data.llama.status === 'online' ? 'var(--color-green)' : 'var(--color-red)';
      }
      if (llamaLat) {
        llamaLat.textContent = data.llama.status === 'online' ? `${data.llama.latency_ms} ms` : '─ ms';
      }

      // 2. Actualizar Badges Postgres
      const dbBadge = $('#srv-db-badge');
      const pgVal = $('#srv-pg-val');
      const dbCount = $('#srv-db-count');
      if (dbBadge) {
        dbBadge.textContent = data.postgres.status.toUpperCase();
        dbBadge.style.color = data.postgres.status === 'online' ? 'var(--color-green)' : 'var(--color-red)';
      }
      if (pgVal) {
        pgVal.textContent = data.postgres.status.toUpperCase();
        pgVal.style.color = data.postgres.status === 'online' ? 'var(--color-green)' : 'var(--color-red)';
      }
      if (dbCount && data.postgres.total_proyectos !== undefined) {
        dbCount.textContent = data.postgres.total_proyectos;
      }

      // 3. Actualizar Badges RSI Worker
      const rsiBadge = $('#srv-rsi-badge');
      const rsiPid = $('#srv-rsi-pid');
      if (rsiBadge) {
        rsiBadge.textContent = data.rsi.running ? 'RUNNING' : 'IDLE';
        rsiBadge.style.color = data.rsi.running ? 'var(--color-amber)' : 'var(--color-text-muted)';
      }
      if (rsiPid) {
        rsiPid.textContent = data.rsi.running ? data.rsi.pid : '─';
      }

      // 4. Actualizar Banner de Anomalía de Emergencia
      const banner = $('#srv-anomaly-banner');
      const anomalyTitle = $('#srv-anomaly-title');
      const anomalyDesc = $('#srv-anomaly-desc');
      if (data.anomaly) {
        if (banner) banner.style.display = 'flex';
        if (anomalyTitle) anomalyTitle.textContent = `🚨 ALERTA: ${data.anomaly.type?.toUpperCase()}`;
        if (anomalyDesc) anomalyDesc.textContent = `Error en ciclo ${data.anomaly.cycle || '?'}: ${data.anomaly.error || 'Fallo detectado'}`;
      } else {
        if (banner) banner.style.display = 'none';
      }

      // 5. Append logs al visor unificado SERVER_LOGS_STREAM
      const streamLog = $('#srv-logs-stream');
      if (streamLog && data.recent_logs && data.recent_logs.length > 0) {
        const lastLog = data.recent_logs[data.recent_logs.length - 1];
        const lastMsgEl = streamLog.querySelector('.log-line:last-child .log-line__msg');
        if (!lastMsgEl || lastMsgEl.textContent !== lastLog) {
          let level = 'info';
          if (lastLog.includes('syntax_error') || lastLog.includes('error')) level = 'error';
          else if (lastLog.includes('warning')) level = 'warn';
          else if (lastLog.includes('cycle_success')) level = 'ok';

          appendLog(streamLog, lastLog, level);
        }
      }

    } catch (err) {
      console.error('Error parseando telemetría SSE:', err);
    }
  };

  telemetrySse.onerror = () => {
    telemetrySse.close();
    setTimeout(initTelemetryStream, 5000);
  };
}

async function manageServerAction(action, extraPayload = {}) {
  try {
    showToast(`Ejecutando acción: ${action}...`, 'info');
    const res = await fetch('/api/server/manage', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, ...extraPayload })
    });
    const data = await res.json();
    if (res.ok) {
      showToast(`Acción ${action} enviada correctamente`, 'ok');
    } else {
      showToast(`Error: ${data.detail || 'Fallo en la acción'}`, 'error');
    }
  } catch (err) {
    showToast(`Error de conexión: ${err.message}`, 'error');
  }
}

window.manageServerAction = manageServerAction;
window.initTelemetryStream = initTelemetryStream;

async function runHarnessBootstrap() {
  const btn = $('#btn-run-harness');
  const streamLog = $('#srv-logs-stream');
  try {
    if (btn) btn.disabled = true;
    showToast('Ejecutando Harness de Maniobra Única...', 'info');
    if (streamLog) appendLog(streamLog, '🚀 [HARNESS] Iniciando diagnóstico y sanity check...', 'info');

    const res = await fetch('/api/harness/run', { method: 'POST' });
    const data = await res.json();

    if (res.ok && data.green_light) {
      showToast('🟢 Green Light: Todo el stack está operativo y listo!', 'ok');
      if (streamLog) appendLog(streamLog, `🟢 [GREEN LIGHT STATUS] ${data.timestamp} - Inferencia Gemma 4 E2B en ${data.services?.LLM_Sanity_Test?.latency_ms || '?'}ms`, 'ok');
    } else {
      showToast('🔴 Red Light: Algunos servicios requieren atención', 'error');
      if (streamLog) appendLog(streamLog, `🔴 [RED LIGHT STATUS] Atendiendo servicios caídos...`, 'error');
    }
  } catch (err) {
    showToast(`Error al ejecutar harness: ${err.message}`, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

window.runHarnessBootstrap = runHarnessBootstrap;

// ═════════════════════════════════════════════════════════════════════════════
// FASE 6 — FUNCIONES DE MOTOR RAG & BÚSQUEDA SEMÁNTICA VECTORIAL
// ═════════════════════════════════════════════════════════════════════════════

async function executeRAGQuery() {
  const queryInput = $('#rag-search-input');
  const claveFilter = $('#rag-clave-filter');
  const btn = $('#btn-rag-query');
  const answerBody = $('#rag-answer-body');
  const sourcesList = $('#rag-sources-list');
  const contextPill = $('#rag-context-used-pill');

  const query = queryInput ? queryInput.value.trim() : '';
  if (!query) {
    showToast('Ingresa una consulta para el Agente RAG', 'warning');
    return;
  }

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" aria-hidden="true">⏳</span> CONSULTANDO...';
  answerBody.innerHTML = '<span class="text-muted" style="font-style:italic;">Buscando fuentes vectoriales y sintetizando respuesta con citas...</span>';

  try {
    const payload = { query: query, top_k: 5 };
    if (claveFilter && claveFilter.value.trim()) {
      payload.filters = { clave: claveFilter.value.trim() };
    }

    const res = await fetch('/api/rag/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    answerBody.textContent = data.answer || 'Sin respuesta generada.';
    contextPill.textContent = `${data.context_used || 0} FUENTES`;

    sourcesList.innerHTML = '';
    if (data.sources && data.sources.length > 0) {
      data.sources.forEach((s, idx) => {
        const item = document.createElement('div');
        item.className = 'file-item';
        item.style.flexDirection = 'column';
        item.style.alignItems = 'flex-start';
        item.style.padding = '8px';
        item.style.gap = '4px';
        item.style.background = 'rgba(0,0,0,0.3)';
        item.style.border = '1px solid var(--border-color)';

        item.innerHTML = `
          <div style="display:flex; justify-content:space-between; width:100%; font-family:var(--font-mono); font-size:10px; color:var(--color-amber);">
            <span>[${escHtml(s.clave)} | ${escHtml(s.section_title)}]</span>
            <span class="badge badge--info">${s.pct || 0}% SIMILITUD</span>
          </div>
          <div style="font-size:11px; color:var(--text-muted); line-height:1.4; max-height:80px; overflow-y:auto;">
            ${escHtml(s.chunk_text)}
          </div>
        `;
        sourcesList.appendChild(item);
      });
    } else {
      sourcesList.innerHTML = '<div class="text-xs text-muted" style="font-style:italic;">No se recuperaron fuentes.</div>';
    }

    showToast('Consulta RAG completada', 'success');
  } catch (err) {
    answerBody.innerHTML = `<span class="text-alert">[ ERROR RAG: ${escHtml(String(err))} ]</span>`;
    showToast('Error en consulta RAG', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<span aria-hidden="true">▸</span> CONSULTAR RAG';
  }
}

async function reindexRAGCorpus() {
  showToast('Iniciando re-indexación RAG...', 'info');
  try {
    const res = await fetch('/api/rag/reindex', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ limit: 100 })
    });
    const data = await res.json();
    showToast(`Corpus RAG re-indexado (${data.total} documentos)`, 'success');
  } catch (err) {
    showToast(`Error al re-indexar corpus: ${err}`, 'error');
  }
}

window.executeRAGQuery = executeRAGQuery;
window.reindexRAGCorpus = reindexRAGCorpus;


/* =========================================================================
   GRAPH DETAIL PANEL
   ========================================================================= */
function initGraphDetailPanel() {
  const panel   = $('#graph-detail-panel');
  const closeBtn = $('#gdp-close');
  const gotoInf = $('#gdp-goto-inference');
  if (!panel) return;

  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      panel.classList.remove('graph-detail-panel--open');
    });
  }

  if (gotoInf) {
    gotoInf.addEventListener('click', () => {
      const clave = gotoInf.dataset.clave;
      if (!clave) return;
      activateTab('INFERENCE_LAB');
      // Try to select the matching item in the inference list
      setTimeout(() => {
        const inferenceList = $('#inference-list');
        if (!inferenceList) return;
        const target = inferenceList.querySelector(`[data-clave="${CSS.escape(clave)}"]`);
        if (target) { target.scrollIntoView({ behavior: 'smooth', block: 'center' }); target.click(); }
      }, 300);
    });
  }
}

function showGraphNodeDetail(nodeData) {
  const panel = $('#graph-detail-panel');
  if (!panel) return;

  const set = (id, val) => { const el = $(`#${id}`); if (el) el.textContent = val ?? '─'; };

  set('gdp-title',  nodeData.id || nodeData.label || '─');
  set('gdp-type',   nodeData.type || nodeData.group || '─');
  set('gdp-year',   nodeData.year || nodeData.anio || '─');
  set('gdp-degree', nodeData.degree ?? nodeData.connections ?? '─');
  set('gdp-com',    nodeData.community ?? nodeData.cluster ?? '─');

  const gotoBtn = $('#gdp-goto-inference');
  if (gotoBtn) gotoBtn.dataset.clave = nodeData.id || '';

  panel.classList.add('graph-detail-panel--open');
}

// Expose so renderGraph() can call it
window.showGraphNodeDetail = showGraphNodeDetail;

/* =========================================================================
   BATCH INFERENCE — Pool de 3 concurrentes
   ========================================================================= */
function initBatchInference() {
  const btn = $('#btn-run-batch-inference');
  if (!btn) return;
  btn.addEventListener('click', runBatchInference);
}

async function runBatchInference() {
  const btn      = $('#btn-run-batch-inference');
  const progBar  = $('#inference-batch-progress');
  const progFill = progBar?.querySelector('.progress-bar__fill');
  const pctEl    = $('#inference-batch-pct');
  const logEl    = $('#inference-batch-log');

  if (!btn) return;
  btn.disabled = true;
  if (progBar)  { progBar.classList.remove('hidden'); }
  if (logEl)    { logEl.classList.remove('hidden'); logEl.innerHTML = ''; }
  if (pctEl)    { pctEl.classList.remove('hidden'); pctEl.textContent = '0%'; }

  appendLog(logEl, 'Cargando lista de estudios con MD listo...', 'info');

  try {
    // 1. Fetch all inferenceable items
    const r = await fetch('/api/corpus/md-list');
    const d = await r.json();
    const items = (d.files || []).filter(f => f.md_ready && !f.has_inference);

    if (!items.length) {
      appendLog(logEl, 'No hay estudios pendientes de inferencia.', 'warn');
      showToast('No hay estudios nuevos para inferir', 'warn');
      return;
    }

    appendLog(logEl, `${items.length} estudios a inferir — pool 3 concurrentes`, 'info');
    let done = 0;
    const total = items.length;
    const CONCURRENCY = 3;

    // 2. Process in sliding window of CONCURRENCY
    async function processItem(item) {
      try {
        const res = await fetch('/api/inference/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ clave: item.clave, file: item.name })
        });
        const data = await res.json();
        const status = res.ok ? 'ok' : 'error';
        appendLog(logEl, `[${status.toUpperCase()}] ${item.clave}: ${data.msg || data.status || '✓'}`, status);
      } catch (err) {
        appendLog(logEl, `[ERROR] ${item.clave}: ${err.message}`, 'error');
      } finally {
        done++;
        const pct = Math.round((done / total) * 100);
        if (progFill) progFill.style.width = `${pct}%`;
        if (pctEl)    pctEl.textContent = `${pct}%`;
        if (progBar)  progBar.setAttribute('aria-valuenow', pct);
      }
    }

    // Sliding pool
    const queue = [...items];
    const running = new Set();

    while (queue.length || running.size) {
      while (running.size < CONCURRENCY && queue.length) {
        const item = queue.shift();
        const p = processItem(item).then(() => running.delete(p));
        running.add(p);
      }
      if (running.size) await Promise.race(running);
    }

    showToast(`✓ Batch completado: ${total} estudios`, 'ok');
    appendLog(logEl, `Batch finalizado — ${done}/${total} procesados`, 'ok');

    // Reload inference list
    if (typeof loadInferenceList === 'function') loadInferenceList();

  } catch (err) {
    appendLog(logEl, `Error fatal: ${err.message}`, 'error');
    showToast('Error en batch inference', 'error');
  } finally {
    btn.disabled = false;
  }
}
