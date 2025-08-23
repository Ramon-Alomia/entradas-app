// static/app.js
const state = {
  base: '',           // se detecta automáticamente
  token: null,
  user: null,
  currentDoc: null,
  currentWhs: null,
  lines: []
};

function $(id) { return document.getElementById(id); }
function fmtDate(dt) { return dt.toISOString().slice(0,10); }

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

  // llena almacenes del token
  const whsSel = $('whsSelect');
  whsSel.innerHTML = '';
  (user.warehouses || []).forEach(w => {
    const opt = document.createElement('option');
    opt.value = w; opt.textContent = w;
    whsSel.appendChild(opt);
  });
  if (user.warehouses?.length) {
    state.currentWhs = user.warehouses[0];
    whsSel.value = state.currentWhs;
  }

  // fechas por defecto: mes actual
  const now = new Date();
  const first = new Date(now.getFullYear(), now.getMonth(), 1);
  const next = new Date(now.getFullYear(), now.getMonth()+1, 1);
  $('dueFrom').value = fmtDate(first);
  $('dueTo').value = fmtDate(next);

  // búsqueda inicial
  loadOrders().catch(err => $('ordersMsg').textContent = err.message);
}

async function doLogin() {
  $('loginMsg').textContent = '';
  const username = $('username').value.trim();
  const password = $('password').value;
  if (!username || !password) {
    $('loginMsg').textContent = 'Ingresa usuario y contraseña';
    return;
  }
  try {
    const data = await api('/api/login', { method: 'POST', json: { username, password } });
    setLoggedIn({ sub:data.username, role:data.role, warehouses:data.warehouses }, data.token);
    $('loginMsg').textContent = '';
  } catch (e) {
    $('loginMsg').textContent = 'Error: ' + e.message;
  }
}

async function loadOrders() {
  $('ordersMsg').textContent = 'Cargando...';
  const vendor = $('vendorCode').value.trim();
  const dueFrom = $('dueFrom').value;
  const dueTo   = $('dueTo').value;
  const whs     = $('whsSelect').value;
  state.currentWhs = whs;

  const params = new URLSearchParams({ page:'1', pageSize:'25', due_from:dueFrom, due_to:dueTo });
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
    btn.onclick = () => loadDetail(row.docEntry);

    tr.innerHTML = `
      <td>${row.docNum}</td>
      <td>${row.vendorCode} — ${row.vendorName}</td>
      <td>${row.docDueDate}</td>
      <td>${row.totalOpenQty ?? '-'}</td>
      <td></td>`;
    tr.children[4].appendChild(btn);
    tbody.appendChild(tr);
  });
  $('ordersMsg').textContent = `${out.data?.length || 0} resultados`;
}

async function loadDetail(docEntry) {
  $('detailMsg').textContent = 'Cargando...';
  const whs = state.currentWhs || $('whsSelect').value;
  const params = new URLSearchParams();
  if (whs) params.set('whsCode', whs);
  const data = await api(`/api/orders/${docEntry}?${params.toString()}`);
  state.currentDoc = docEntry;
  state.lines = data.lines || [];
  $('detailTitle').textContent = `DocEntry ${docEntry}`;
  const tbody = $('linesTbl').querySelector('tbody');
  tbody.innerHTML = '';
  state.lines.forEach(line => {
    const max = Number(line.openQty || 0);
    const inputId = `q_${line.lineNum}`;
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
  $('detailMsg').textContent = '';
}

async function postReceipt() {
  $('detailMsg').textContent = '';
  if (!state.currentDoc) {
    $('detailMsg').textContent = 'Selecciona primero una OC';
    return;
  }
  const whs = state.currentWhs || $('whsSelect').value;
  const supplierRef = $('supplierRef').value.trim() || undefined;

  const selected = [];
  for (const line of state.lines) {
    const input = $(`q_${line.lineNum}`);
    if (!input) continue;
    const qty = Number(input.value);
    if (qty > 0) {
      if (qty > Number(line.openQty)) {
        $('detailMsg').textContent = `Cantidad en línea ${line.lineNum} excede OpenQty`;
        return;
      }
      selected.push({ lineNum: line.lineNum, quantity: qty });
    }
  }
  if (selected.length === 0) {
    $('detailMsg').textContent = 'No hay cantidades > 0 para registrar.';
    return;
  }

  try {
    const payload = { docEntry: state.currentDoc, whsCode: whs, lines: selected };
    if (supplierRef) payload.supplierRef = supplierRef;
    const res = await api('/api/receipts', { method:'POST', json: payload });
    $('detailMsg').innerHTML = `<span class="ok">✅ GRPO creado: DocEntry ${res.grpoDocEntry}</span>`;
    // refrescar el detalle para ver nuevos OpenQty
    await loadDetail(state.currentDoc);
  } catch (e) {
    $('detailMsg').innerHTML = `<span class="error">❌ ${e.message}</span>`;
  }
}

function restoreSession() {
  state.base = `${location.origin}`;
  try {
    const t = sessionStorage.getItem('recep_token');
    const u = sessionStorage.getItem('recep_user');
    if (t && u) {
      state.token = t;
      state.user = JSON.parse(u);
      $('loginCard').classList.add('hidden');
      $('dashCard').classList.remove('hidden');
      // volver a pintar almacenes
      const whsSel = $('whsSelect');
      whsSel.innerHTML = '';
      (state.user.warehouses || []).forEach(w => {
        const opt = document.createElement('option'); opt.value = w; opt.textContent = w; whsSel.appendChild(opt);
      });
      if (state.user.warehouses?.length) {
        state.currentWhs = state.user.warehouses[0];
        whsSel.value = state.currentWhs;
      }
      // fechas default
      const now = new Date();
      const first = new Date(now.getFullYear(), now.getMonth(), 1);
      const next = new Date(now.getFullYear(), now.getMonth()+1, 1);
      $('dueFrom').value = fmtDate(first);
      $('dueTo').value = fmtDate(next);
      loadOrders().catch(err => $('ordersMsg').textContent = err.message);
    }
  } catch {}
}

window.addEventListener('DOMContentLoaded', () => {
  $('btnLogin').addEventListener('click', doLogin);
  $('btnSearch').addEventListener('click', () => loadOrders().catch(err => $('ordersMsg').textContent = err.message));
  $('btnPost').addEventListener('click', postReceipt);
  $('whsSelect').addEventListener('change', () => { state.currentWhs = $('whsSelect').value; });
  restoreSession();
});
