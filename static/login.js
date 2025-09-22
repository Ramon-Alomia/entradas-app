// static/login.js

function $(id) { return document.getElementById(id); }

async function submitLogin(ev) {
  ev.preventDefault();
  const username = $("username")?.value.trim();
  const password = $("password")?.value || "";
  const msg = $("loginMsg");
  const btn = $("btnLogin");

  if (msg) { msg.textContent = ""; msg.classList.remove("error"); }

  if (!username || !password) {
    if (msg) {
      msg.textContent = "Usuario y contraseña son requeridos";
      msg.classList.add("error");
    }
    return;
  }

  if (btn) btn.disabled = true;
  if (msg) { msg.textContent = "Autenticando..."; msg.classList.remove("error"); }

  try {
    const res = await fetch("/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password })
    });

    const contentType = res.headers.get("content-type") || "";
    let payload = null;
    if (contentType.includes("application/json")) {
      payload = await res.json();
    } else {
      payload = await res.text();
    }

    if (!res.ok) {
      const message = (payload && payload.error && payload.error.message) || payload?.message || payload || "Error al iniciar sesión";
      throw new Error(message);
    }

    if (msg) {
      msg.textContent = "Acceso concedido, redirigiendo...";
      msg.classList.remove("error");
    }

    setTimeout(() => { window.location.href = "/"; }, 200);
  } catch (err) {
    if (msg) {
      msg.textContent = `❌ ${err.message || err}`;
      msg.classList.add("error");
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

window.addEventListener("DOMContentLoaded", () => {
  const form = $("loginForm");
  form?.addEventListener("submit", submitLogin);
  $("username")?.focus();
});
