// static/admin.js
// Endpoints JSON ahora viven bajo /admin/* (sin /api/admin)
const API_BASE = "/admin";

/* ---------------- Utils ---------------- */
async function fetchJson(url, opts = {}) {
  const res = await fetch(url, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      ...(opts.headers || {}),
    },
  });

  if (res.status === 401 || res.status === 403) {
    // No autenticado / no autorizado -> ve a login
    window.location.href = "/login";
    return;
  }

  if (!res.ok) {
    const txt = await res.text();
    throw new Error(`${res.status} ${txt}`);
  }
  // DELETE puede no traer body
  const ct = res.headers.get("content-type") || "";
  if (!ct.includes("application/json")) return {};
  return res.json();
}

/* ---------------- Users ---------------- */
async function loadUsers() {
  try {
    const j = await fetchJson(`${API_BASE}/users`);
    const tb = document.getElementById("users_tbody");
    tb.innerHTML = "";
    for (const u of j.data) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${u.username}</td>
        <td>${u.role}</td>
        <td>${u.active ? "✔️" : "❌"}</td>
        <td class="muted">${(u.warehouses || []).join(", ")}</td>
        <td>
          <button class="warn" onclick="toggleActive('${u.username}', ${!u.active})">
            ${u.active ? "Desactivar" : "Activar"}
          </button>
          <button class="ok" onclick="resetPassword('${u.username}')">Reset pass</button>
          <button class="danger" onclick="deleteUser('${u.username}')">Eliminar</button>
        </td>
      `;
      tb.appendChild(tr);
    }
  } catch (e) {
    console.error(e);
    alert("Error cargando usuarios: " + e.message);
  }
}

async function createUser() {
  const username = document.getElementById("u_username").value.trim();
  const role     = document.getElementById("u_role").value;
  const password = document.getElementById("u_password").value;
  const whsRaw   = document.getElementById("u_whs").value;
  const whs      = whsRaw.split(",").map(s => s.trim()).filter(Boolean);

  const msg = document.getElementById("u_msg");
  msg.textContent = "Enviando...";
  try {
    const r = await fetchJson(`${API_BASE}/users`, {
      method: "POST",
      body: JSON.stringify({ username, role, password, warehouses: whs }),
    });
    msg.textContent = `OK. Password: ${r.tempPassword}`;
    // limpia campos
    document.getElementById("u_password").value = "";
    loadUsers();
  } catch (e) {
    msg.textContent = e.message;
  }
}

async function toggleActive(username, active) {
  try {
    await fetchJson(`${API_BASE}/users/${encodeURIComponent(username)}`, {
      method: "PATCH",
      body: JSON.stringify({ active }),
    });
    loadUsers();
  } catch (e) { alert(e.message); }
}

async function resetPassword(username) {
  const p = prompt(`Nueva contraseña para ${username} (deja vacío para cancelar):`, "");
  if (!p) return;
  try {
    await fetchJson(`${API_BASE}/users/${encodeURIComponent(username)}`, {
      method: "PATCH",
      body: JSON.stringify({ password: p }),
    });
    alert("Password actualizado.");
    loadUsers();
  } catch (e) { alert(e.message); }
}

async function deleteUser(username) {
  if (!confirm(`¿Eliminar usuario "${username}"? Esta acción no se puede deshacer.`)) return;
  try {
    await fetchJson(`${API_BASE}/users/${encodeURIComponent(username)}`, {
      method: "DELETE",
    });
    loadUsers();
  } catch (e) {
    alert(e.message);
  }
}

/* ---------------- Warehouses ---------------- */
async function loadWarehouses() {
  try {
    const j = await fetchJson(`${API_BASE}/warehouses`);
    const tb = document.getElementById("whs_tbody");
    tb.innerHTML = "";
    for (const w of j.data) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${w.whscode}</td>
        <td>${w.cardcode || ""}</td>
        <td>${w.whsdesc || ""}</td>
        <td>
          <button class="danger" onclick="deleteWarehouse('${w.whscode}')">Eliminar</button>
        </td>
      `;
      tb.appendChild(tr);
    }
  } catch (e) {
    console.error(e);
    alert("Error cargando almacenes: " + e.message);
  }
}

async function upsertWarehouse() {
  const whscode  = document.getElementById("w_whscode").value.trim();
  const cardcode = document.getElementById("w_cardcode").value.trim();
  const whsdesc  = document.getElementById("w_whsdesc").value.trim();

  const msg = document.getElementById("w_msg");
  msg.textContent = "Guardando...";
  try {
    await fetchJson(`${API_BASE}/warehouses`, {
      method: "POST",
      body: JSON.stringify({ whscode, cardcode, whsdesc }),
    });
    msg.textContent = "OK";
    loadWarehouses();
  } catch (e) {
    msg.textContent = e.message;
  }
}

async function deleteWarehouse(whscode) {
  if (!confirm(`¿Eliminar almacén "${whscode}"? Esta acción no se puede deshacer.`)) return;
  try {
    await fetchJson(`${API_BASE}/warehouses/${encodeURIComponent(whscode)}`, {
      method: "DELETE",
    });
    loadWarehouses();
  } catch (e) {
    // En caso de 409 por relaciones (FKs), el backend ya responde con mensaje claro.
    alert(e.message);
  }
}

/* ---------------- Init ---------------- */
window.addEventListener("DOMContentLoaded", async () => {
  // La cookie HttpOnly "token" se envía automáticamente en mismo origen.
  // Si no está o no es válida, el servidor responderá 401/403 y te mandamos a /login.
  await loadUsers();
  await loadWarehouses();

  // Bind de botones si los tienes en el HTML
  const btnCreate = document.getElementById("u_btn_create");
  if (btnCreate) btnCreate.addEventListener("click", createUser);

  const btnSaveWhs = document.getElementById("w_btn_save");
  if (btnSaveWhs) btnSaveWhs.addEventListener("click", upsertWarehouse);
});
