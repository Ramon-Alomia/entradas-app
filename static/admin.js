const API = "/api/admin";

function getToken() {
  let t = localStorage.getItem("recepciones_token") || "";
  if (!t) return null;
  if (t.toLowerCase().startsWith("bearer ")) return t.slice(7);
  return t;
}
function authHeader() {
  const t = getToken();
  return t ? { "Authorization": "Bearer " + t } : {};
}
function showTokenBox(show) {
  document.getElementById("tokenBox").style.display = show ? "block" : "none";
}
function saveToken() {
  let v = document.getElementById("tokenInput").value.trim();
  if (v.toLowerCase().startsWith("bearer ")) v = v.slice(7);
  if (!v) { alert("Pega un token"); return; }
  localStorage.setItem("recepciones_token", v);
  location.reload();
}

async function fetchJson(url, opts={}) {
  const res = await fetch(url, {
    ...opts,
    headers: { "Content-Type":"application/json", ...authHeader(), ...(opts.headers||{}) }
  });
  if (!res.ok) {
    const txt = await res.text();
    throw new Error(`${res.status} ${txt}`);
  }
  return res.json();
}

/* ------- Users ------- */
async function loadUsers() {
  try {
    const j = await fetchJson(`${API}/users`);
    const tb = document.getElementById("users_tbody");
    tb.innerHTML = "";
    for (const u of j.data) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${u.username}</td>
        <td>${u.role}</td>
        <td>${u.active ? "✔️" : "❌"}</td>
        <td class="muted">${(u.warehouses||[]).join(", ")}</td>
        <td>
          <button class="warn" onclick="toggleActive('${u.username}', ${!u.active})">${u.active?"Desactivar":"Activar"}</button>
          <button class="ok" onclick="resetPassword('${u.username}')">Reset pass</button>
        </td>
      `;
      document.getElementById("users_tbody").appendChild(tr);
    }
  } catch (e) {
    console.error(e);
    if (String(e).includes("401")) showTokenBox(true);
  }
}
async function createUser() {
  const username = document.getElementById("u_username").value.trim();
  const role     = document.getElementById("u_role").value;
  const password = document.getElementById("u_password").value;
  const whs      = document.getElementById("u_whs").value.split(",").map(s=>s.trim()).filter(Boolean);
  document.getElementById("u_msg").textContent = "Enviando...";
  try {
    const r = await fetchJson(`${API}/users`, { method:"POST", body: JSON.stringify({username, role, password, warehouses: whs}) });
    document.getElementById("u_msg").textContent = `OK. Password: ${r.tempPassword}`;
    loadUsers();
  } catch (e) {
    document.getElementById("u_msg").textContent = e.message;
  }
}
async function toggleActive(username, active) {
  try {
    await fetchJson(`${API}/users/${encodeURIComponent(username)}`, { method:"PATCH", body: JSON.stringify({active}) });
    loadUsers();
  } catch (e) { alert(e.message); }
}
async function resetPassword(username) {
  const p = prompt(`Nueva contraseña para ${username} (deja vacío para cancelar):`, "");
  if (!p) return;
  try {
    await fetchJson(`${API}/users/${encodeURIComponent(username)}`, { method:"PATCH", body: JSON.stringify({password: p}) });
    alert("Password actualizado.");
    loadUsers();
  } catch (e) { alert(e.message); }
}

/* ------- Warehouses ------- */
async function loadWarehouses() {
  try {
    const j = await fetchJson(`${API}/warehouses`);
    const tb = document.getElementById("whs_tbody");
    tb.innerHTML = "";
    for (const w of j.data) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${w.whscode}</td><td>${w.cardcode||""}</td><td>${w.whsdesc||""}</td>`;
      tb.appendChild(tr);
    }
  } catch (e) {
    console.error(e);
  }
}
async function upsertWarehouse() {
  const whscode  = document.getElementById("w_whscode").value.trim();
  const cardcode = document.getElementById("w_cardcode").value.trim();
  const whsdesc  = document.getElementById("w_whsdesc").value.trim();
  document.getElementById("w_msg").textContent = "Guardando...";
  try {
    await fetchJson(`${API}/warehouses`, { method:"POST", body: JSON.stringify({whscode, cardcode, whsdesc}) });
    document.getElementById("w_msg").textContent = "OK";
    loadWarehouses();
  } catch (e) {
    document.getElementById("w_msg").textContent = e.message;
  }
}

/* ------- Init ------- */
window.addEventListener("DOMContentLoaded", async () => {
  if (!getToken()) showTokenBox(true);
  await loadUsers();
  await loadWarehouses();
});
