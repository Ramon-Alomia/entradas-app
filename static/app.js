// static/app.js
// Backends de negocio siguen en /api (orders, receipts). Si los mueves, cambia API_BASE = ''.
const API_BASE = "/api";

const state = {
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
  // Cookies HttpOnly se envían automático en mismo origen
  const url = `${location.origin}${path.startsWith("/") ? path : `/${path}`}`;
  const headers = opts.headers || {};
  if (opts.json) {
    headers["Content-Type"] = "application/json; charset=utf-8";
    opts.body = JSON.stringify(opts.json);
    delete opts.json;
  }
  const res = await fetch(url, { ...opts, headers, credentials: "same-origin" });

  if (res.status === 401 || res.status === 403) {
    // No autenticado o no autorizado → manda a /login
    window.location.href = "/login";
    throw new Error("AUTH");
  }
  if (!res.ok) {
    const txt = await res.text();
    try {
      const j = JSON.parse(txt);
      throw new Error(j?.error?.message || txt);
    } catch {
      throw new Error(txt);
    }
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

/* ---------- Sesión / Usuario ---------- */
async function fetchMe() {
  // Devuelve payload con username/role/warehouses desde cookie JWT
  const me = await api("/me", { method: "GET" });
  return me?.user || null;
}

function applyUserToUI(user) {
  state.user = user;

  // Llenar combo de almacenes
  const whsSel = $("whsSelect");
  if (whsSel) {
    whsSel.innerHTML = "";
    (user.warehouses || []).forEach(w => {
      const opt = document.createElement("option");
      opt.value = w; opt.textContent = w;
      whsSel.appendChild(opt);
    });
    if (user.warehouses?.length) {
      state.currentWhs = user.warehouses[0];
      whsSel.value = state.currentWhs;
    }
  }

  // Rango de fechas por defecto (mes actual)
  const now = new Date();
  const first = new Date(now.getFullYear(), now.getMonth(), 1);
  const next  = new Date(now.getFullYear(), now.getMonth()+1, 1);
  if ($("dueFrom")) $("dueFrom").value = fmtDate(first);
  if ($("dueTo"))   $("dueTo").value   = fmtDate(next);
}

/* ---------- Paginación ---------- */
function buildPagination() {
  const totalPages = Math.max(1, Math.ceil((state.total || 0) / state.pageSize));
  const cont = document.createElement("div");
  cont.className = "pager";
  cont.style.display = "flex";
  cont.style.justifyContent = "space-between";
  cont.style.alignItems = "center";
  cont.style.marginTop = "8px";

  const left = document.createElement("div");
  left.textContent = `Página ${state.page} de ${totalPages} — ${state.total} resultados`;
  const right = document.createElement("div");

  const prev = document.createElement("button");
  prev.textContent = "← Anterior";
  prev.className = "secondary";
  prev.disabled = state.page <= 1;
  prev.onclick = () => { if (state.page > 1) { state.page--; loadOrders().catch(e=>setText("ordersMsg", e.message)); } };

  const next = document.createElement("button");
  next.textContent = "Siguiente →";
  next.className = "secondary";
  next.style.marginLeft = "8px";
  next.disabled = state.page >= totalPages;
  next.onclick = () => { if (state.page < totalPages) { state.page++; loadOrders().catch(e=>setText("ordersMsg", e.message)); } };

  right.appendChild(prev); right.appendChild(next);
  cont.appendChild(left); cont.appendChild(right);
  return cont;
}

/* ---------- Pedidos / Detalle / Recepciones ---------- */
async function loadOrders() {
  setText("ordersMsg", "Cargando...");

  const vendor = $("vendorCode")?.value.trim();
  const dueFrom= $("dueFrom")?.value;
  const dueTo  = $("dueTo")?.value;
  const whsSel = $("whsSelect");
  const whs    = whsSel ? whsSel.value : state.currentWhs;
  state.currentWhs = whs || state.currentWhs;

  const params = new URLSearchParams({
    page: String(state.page),
    pageSize: String(state.pageSize),
    due_from: dueFrom || "",
    due_to: dueTo || ""
  });
  if (vendor) params.set("vendorCode", vendor);
  if (whs)    params.set("whsCode", whs);

  const out = await api(`${API_BASE}/orders?${params.toString()}`, { method: "GET" });

  const tbody = $("ordersTbl")?.querySelector("tbody");
  if (tbody) {
    tbody.innerHTML = "";
    (out.data || []).forEach(row => {
      const tr = document.createElement("tr");
      const btn = document.createElement("button");
      btn.textContent = "Ver detalle";
      btn.className = "secondary";
      btn.onclick = () => loadDetail(row.docEntry)
        .catch(err => { setText("detailMsg", err.message); $("detailCard")?.classList.remove("hidden"); });

      tr.innerHTML = `
        <td>${row.docNum}</td>
        <td>${row.vendorCode} — ${row.vendorName}</td>
        <td>${row.docDueDate}</td>
        <td>${row.totalOpenQty ?? "-"}</td>
        <td></td>`;
      tr.children[4].appendChild(btn);
      tbody.appendChild(tr);
    });
  }
  state.total = out.total || 0;

  // render paginación
  const card = $("dashCard");
  if (card) {
    [...card.querySelectorAll(".pager")].forEach(n => n.remove());
    const pager = buildPagination();
    card.appendChild(pager);
  }

  setText("ordersMsg", `${out.data?.length || 0} resultados`);
}

async function loadDetail(docEntry) {
  setText("detailMsg", "Cargando...");
  const whs = state.currentWhs || $("whsSelect")?.value || "";

  const params = new URLSearchParams();
  if (whs) params.set("whsCode", whs);

  const data = await api(`${API_BASE}/orders/${docEntry}?${params.toString()}`, { method:"GET" });

  state.currentDoc = docEntry;
  state.lines = data.lines || [];

  const title = $("detailTitle");
  if (title) title.textContent = `DocEntry ${docEntry}`;

  const tbody = $("linesTbl")?.querySelector("tbody");
  if (tbody) {
    tbody.innerHTML = "";
    state.lines.forEach(line => {
      const max = Number(line.openQty || 0);
      const inputId = `q_${line.lineNum}`;
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${line.lineNum}</td>
        <td>${line.itemCode}</td>
        <td>${line.description || ""}</td>
        <td>${line.orderedQty}</td>
        <td>${line.receivedQty}</td>
        <td>${line.openQty}</td>
        <td><input id="${inputId}" type="number" min="0" max="${max}" step="1" value="${max}"></td>
      `;
      tbody.appendChild(tr);
      const inp = $(inputId);
      inp?.addEventListener("input", () => {
        const v = Number(inp.value);
        if (v < 0) inp.value = 0;
        if (v > max) inp.value = max;
      });
    });
  }

  $("detailCard")?.classList.remove("hidden");
  setText("detailMsg", "");
}

async function postReceipt() {
  setText("detailMsg", "");
  if (!state.currentDoc) { setText("detailMsg","Selecciona primero una OC"); return; }

  const whs = state.currentWhs || $("whsSelect")?.value || "";
  const supplierRef = $("supplierRef")?.value.trim() || undefined;

  const selected = [];
  for (const line of state.lines) {
    const inp = $(`q_${line.lineNum}`); if (!inp) continue;
    const qty = Number(inp.value);
    if (qty > 0) {
      if (qty > Number(line.openQty)) { setText("detailMsg", `Cantidad en línea ${line.lineNum} excede OpenQty`); return; }
      selected.push({ lineNum: line.lineNum, quantity: qty });
    }
  }
  if (selected.length === 0) { setText("detailMsg", "No hay cantidades > 0 para registrar."); return; }

  try {
    const payload = { docEntry: state.currentDoc, whsCode: whs, lines: selected };
    if (supplierRef) payload.supplierRef = supplierRef;

    const res = await api(`${API_BASE}/receipts`, { method:"POST", json: payload });
    setText("detailMsg", `✅ GRPO creado: DocEntry ${res.grpoDocEntry}`);
    await loadDetail(state.currentDoc); // refrescar openQty
  } catch (e) {
    setText("detailMsg", `❌ ${e.message}`);
  }
}

/* ---------- Init ---------- */
async function init() {
  try {
    const me = await fetchMe(); // 401 → /login
    applyUserToUI(me);
    await loadOrders();
  } catch (e) {
    // Si no es AUTH ya habremos sido redirigidos; mostraremos el error si quedó en esta página
    if (String(e.message) !== "AUTH") {
      setText("ordersMsg", e.message || "Error cargando");
    }
  }
}

window.addEventListener("DOMContentLoaded", () => {
  $("btnSearch")?.addEventListener("click", () => { state.page = 1; loadOrders().catch(e=>setText("ordersMsg", e.message)); });
  $("btnPost")?.addEventListener("click", postReceipt);
  $("whsSelect")?.addEventListener("change", () => { state.currentWhs = $("whsSelect").value; state.page=1; loadOrders().catch(e=>setText("ordersMsg", e.message)); });

  init();
});
