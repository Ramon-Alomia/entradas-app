// static/app.js
const state = {
  base: '',
  token: null,
  user: null,
  currentDoc: null,
  currentWhs: null,
  lines: [],
  page: 1,
  pageSize: 20,
  total: 0
};

function $(id) { return document.getElementById(id); }
function fmtDate(dt) { return dt.toISOString().slice(0,10); }

function setText(id, text) { const el = $(id); if (el) el.textContent = text; }

async function api(path, opts = {}) {
  const url = `${state.base}${path}`;
  const headers = opts.headers || {};
  if (state.token) headers['Authorization'] = `Bearer ${state.token}`;
  if (opts.json) {
    headers['Content-Type'] = 'application/json; charset=utf-8';
    opts.body = JSON.stringify(opts.json);
    delete opts.json;
  }
  const res = await fetch(url, { ...opts, headers });
  if (!res.ok) {
    const txt = await res.text();
    let msg = txt;
    try { msg = JSON.parse(txt); } catch {}
    throw new Error(typeof msg === 'string' ? msg : (msg.error?.message || JSON.stringify(msg)));
  }
  const ct = res.headers.get('content-type') || '';
  return ct.includes('application/json') ? res.json() : res.text();
}

function setLoggedIn(user, token) {
  state.token = token;
  state.user = user;
  sessionStorage.setItem('recep_token', token);
  sessionStorage.setItem('recep_user', JSON.stringify(user));
  $('loginCard').classList.add('hidden');
  $('dashCard').classList.remove('hidden');

  // almacenes del token
  const whsSel = $('whsSelect'); whsSel.innerHTML = '';
  (user.warehouses || []).forEach(w => {
    const opt = document.createElement('option'); opt.value=w; opt.textContent=w; whsSel.appendChild(opt);
  });
  if (user.warehouses?.length) {
    state.currentWhs = user.warehouses[0]; whsSel.value = state.currentWhs;
  }

  // fechas por defecto
  const now = new Date();
  const first = new Date(now.getFullYear(), now.getMonth(), 1);
  const next  = new Date(now.getFullYear(), now.getMonth()+1, 1);
  $('dueFrom').value = fmtDate(first);
  $('dueTo').value   = fmtDate(next);

  // busca
  loadOrders().catch(err => setText('ordersMsg', err.message));
}

async function doLogin() {
  setText('loginMsg', '');
  const username = $('username').value.trim();
  const password = $('password').value;
  if (!username || !password) { setText('loginMsg','Ingresa usuario y contraseña'); return; }
  try {
    const data = await api('/api/login', { method:'POST', json:{ username, password } });
    setLoggedIn({ sub:data.username, role:data.role, warehouses:data.warehouses }, data.token);
  } catch (e) { setText('loginMsg', 'Error: ' + e.message); }
}

function buildPagination() {
  const totalPages = Math.max(1, Math.ceil((state.total || 0) / state.pageSize));
  const cont = document.createElement('div');
  cont.style.display = 'flex';
  cont.style.justifyContent = 'space-between';
  cont.style.alignItems = 'center';
  cont.style.marginTop = '8px';

  const left = document.createElement('div');
  left.textContent = `Página ${state.page} de ${totalPages} — ${state.total} resultados`;
  const right = document.createElement('div');

  const prev = document.createElement('button');
  prev.textContent = '← Anterior';
  prev.className = 'secondary';
  prev.disabled = state.page <= 1;
  prev.onclick = () => { if (state.page>1) { state.page--; loadOrders().catch(e=>setText('ordersMsg', e.message)); } };

  const next = document.createElement('button');
  next.textContent = 'Siguiente →';
  next.className = 'secondary';
  next.style.marginLeft = '8px';
  next.disabled = state.page >= totalPages;
  next.onclick = () => { if (state.page<totalPages) { state.page++; loadOrders().catch(e=>setText('ordersMsg', e.message)); } };

  right.appendChild(prev); right.appendChild(next);
  cont.appendChild(left); cont.appendChild(right);
  return cont;
}

async function loadOrders() {
  setText('ordersMsg', 'Cargando...');
  const vendor = $('vendorCode').value.trim();
  const dueFrom= $('dueFrom').value;
  const dueTo  = $('dueTo').value;
  const whs    = $('whsSelect').value;
  state.currentWhs = whs;

  const params = new URLSearchParams({ page:String(state.page), pageSize:String(state.pageSize), due_from:dueFrom, due_to:dueTo });
  if (vendor) params.set('vendorCode', vendor);
  if (whs)    params.set('whsCode', whs);

  const out = await api(`/api/orders?${params.toString()}`);
  const tbody = $('ordersTbl').querySelector('tbody');
  tbody.innerHTML = '';
  (out.data || []).forEach(row => {
    const tr = document.createElement('tr');
    const btn = document.createElement('button');
    btn.textContent = 'Ver detalle';
    btn.className = 'secondary';
    btn.onclick = () => loadDetail(row.docEntry).catch(err => { setText('detailMsg', err.message); $('detailCard').classList.remove('hidden'); });

    tr.innerHTML = `
      <td>${row.docNum}</td>
      <td>${row.vendorCode} — ${row.vendorName}</td>
      <td>${row.docDueDate}</td>
      <td>${row.totalOpenQty ?? '-'}</td>
      <td></td>`;
    tr.children[4].appendChild(btn);
    tbody.appendChild(tr);
  });
  state.total = out.total || 0;

  // render paginación
  const card = $('dashCard');
  // elimina paginadores previos
  [...card.querySelectorAll('.pager')].forEach(n => n.remove());
  const pager = buildPagination(); pager.classList.add('pager');
  card.appendChild(pager);

  setText('ordersMsg', `${out.data?.length || 0} resultados`);
}

async function loadDetail(docEntry) {
  setText('detailMsg', 'Cargando...');
  const whs = state.currentWhs || $('whsSelect').value;
  const params = new URLSearchParams(); if (whs) params.set('whsCode', whs);
  const data = await api(`/api/orders/${docEntry}?${params.toString()}`);
  state.currentDoc = docEntry; state.lines = data.lines || [];
  $('detailTitle').textContent = `DocEntry ${docEntry}`;
  const tbody = $('linesTbl').querySelector('tbody'); tbody.innerHTML = '';
  state.lines.forEach(line => {
    const max = Number(line.openQty || 0); const inputId = `q_${line.lineNum}`;
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${line.lineNum}</td>
      <td>${line.itemCode}</td>
      <td>${line.description || ''}</td>
      <td>${line.orderedQty}</td>
      <td>${line.receivedQty}</td>
      <td>${line.openQty}</td>
      <td><input id="${inputId}" type="number" min="0" max="${max}" step="1" value="${max}"></td>
    `;
    tbody.appendChild(tr);
    const inp = $(inputId);
    inp.addEventListener('input', () => {
      const v = Number(inp.value);
      if (v < 0) inp.value = 0;
      if (v > max) inp.value = max;
    });
  });
  $('detailCard').classList.remove('hidden');
  setText('detailMsg', '');
}

async function postReceipt() {
  setText('detailMsg','');
  if (!state.currentDoc) { setText('detailMsg','Selecciona primero una OC'); return; }
  const whs = state.currentWhs || $('whsSelect').value;
  const supplierRef = $('supplierRef').value.trim() || undefined;

  const selected = [];
  for (const line of state.lines) {
    const inp = $(`q_${line.lineNum}`); if (!inp) continue;
    const qty = Number(inp.value);
    if (qty > 0) {
      if (qty > Number(line.openQty)) { setText('detailMsg', `Cantidad en línea ${line.lineNum} excede OpenQty`); return; }
      selected.push({ lineNum: line.lineNum, quantity: qty });
    }
  }
  if (selected.length === 0) { setText('detailMsg', 'No hay cantidades > 0 para registrar.'); return; }

  try {
    const payload = { docEntry: state.currentDoc, whsCode: whs, lines: selected };
    if (supplierRef) payload.supplierRef = supplierRef;
    const res = await api('/api/receipts', { method:'POST', json: payload });
    setText('detailMsg', `✅ GRPO creado: DocEntry ${res.grpoDocEntry}`);
    await loadDetail(state.currentDoc);
  } catch (e) {
    setText('detailMsg', `❌ ${e.message}`);
  }
}

function restoreSession() {
  state.base = `${location.origin}`;
  try {
    const t = sessionStorage.getItem('recep_token');
    const u = sessionStorage.getItem('recep_user');
    if (t && u) {
      state.token = t; state.user = JSON.parse(u);
      $('loginCard').classList.add('hidden'); $('dashCard').classList.remove('hidden');
      const whsSel = $('whsSelect'); whsSel.innerHTML = '';
      (state.user.warehouses || []).forEach(w => { const o=document.createElement('option'); o.value=w; o.textContent=w; whsSel.appendChild(o); });
      if (state.user.warehouses?.length) { state.currentWhs = state.user.warehouses[0]; whsSel.value = state.currentWhs; }
      const now = new Date(); const first = new Date(now.getFullYear(), now.getMonth(), 1); const next = new Date(now.getFullYear(), now.getMonth()+1, 1);
      $('dueFrom').value = fmtDate(first); $('dueTo').value = fmtDate(next);
      loadOrders().catch(err => setText('ordersMsg', err.message));
    }
  } catch {}
}

window.addEventListener('DOMContentLoaded', () => {
  $('btnLogin').addEventListener('click', doLogin);
  $('btnSearch').addEventListener('click', () => { state.page=1; loadOrders().catch(err => setText('ordersMsg', err.message)); });
  $('btnPost').addEventListener('click', postReceipt);
  $('whsSelect').addEventListener('change', () => { state.currentWhs = $('whsSelect').value; state.page=1; loadOrders().catch(e=>setText('ordersMsg', e.message)); });
  restoreSession();
});
