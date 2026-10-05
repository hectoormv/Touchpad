"""Menú de la bandeja con estilo cristal (Windows). Va como texto para que PyInstaller
lo incluya solo, sin tener que tocar la compilación."""

HTML = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>TouchPad Menu</title>
<style>
:root{--panel:#0F1828;--card:#0E1830;--line:rgba(120,170,255,.16);--text:#EAF1FF;--muted:#8C9AB5;--blue:#2F96FF;--green:#34D17A;color-scheme:dark}
*{box-sizing:border-box}
html,body{margin:0;height:100%;overflow:hidden;background:var(--panel);color:var(--text);
  font-family:"Segoe UI Variable Text","Segoe UI",system-ui,sans-serif;font-size:13.5px;-webkit-user-select:none;user-select:none}
body{padding:8px;box-shadow:inset 0 0 0 1px rgba(47,150,255,.45);border-radius:8px}
button,input{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--blue);outline-offset:1px}
.status{position:relative;height:72px;border-radius:12px;overflow:hidden;background-color:var(--card);
  background-image:radial-gradient(circle,rgba(120,170,255,.16) 1.1px,transparent 1.5px);background-size:11px 11px;
  display:flex;align-items:center;gap:12px;padding:0 14px;box-shadow:inset 0 0 0 1px var(--line)}
.status .lit{position:absolute;inset:0;background-image:radial-gradient(circle,rgba(47,150,255,.9) 1.8px,transparent 2.3px);background-size:11px 11px;
  -webkit-mask-image:radial-gradient(circle 70px at 30px 50%,#000,transparent);mask-image:radial-gradient(circle 70px at 30px 50%,#000,transparent);transition:opacity .3s}
.status.off .lit{opacity:.15}
.ic{position:relative;width:38px;height:38px;border-radius:50%;background:rgba(47,150,255,.18);color:var(--blue);display:grid;place-items:center;flex:none}
.status.off .ic{background:rgba(140,154,181,.15);color:var(--muted)}
.txt{position:relative;display:flex;flex-direction:column;gap:2px;min-width:0}
.txt b{display:flex;align-items:center;gap:7px;font-size:14px}
.txt b i{width:8px;height:8px;border-radius:50%;background:#5D6B86}
.status.on .txt b i{background:var(--green);box-shadow:0 0 8px var(--green)}
.txt span{font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row{width:100%;height:36px;display:flex;align-items:center;gap:10px;padding:0 10px;border:0;border-radius:8px;background:transparent;text-align:left;cursor:pointer}
.row:hover{background:rgba(47,150,255,.12)}
.row .i{width:18px;display:grid;place-items:center;color:var(--muted)}
.row .grow{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row small{font-size:11px;color:var(--muted)}
.sep{height:1px;background:var(--line);margin:6px 4px}
.sw{appearance:none;-webkit-appearance:none;position:relative;width:40px;height:22px;border-radius:11px;background:#24314B;margin:0;flex:none;cursor:pointer;box-shadow:inset 0 0 0 1px var(--line)}
.sw::before{content:"";position:absolute;top:3px;left:3px;width:16px;height:16px;border-radius:50%;background:#C9D6F0;transition:transform .2s}
.sw:checked{background:var(--blue);box-shadow:0 0 12px rgba(47,150,255,.55)}
.sw:checked::before{transform:translateX(18px);background:#fff}
label.row{justify-content:space-between}
</style>
</head>
<body>
<div class="status off" id="status">
  <div class="lit"></div>
  <div class="ic"><svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="2" width="12" height="20" rx="3"/><path d="M11 18h2"/></svg></div>
  <div class="txt"><b><i></i><span id="st1" style="color:var(--text);font-size:14px">Desactivado</span></b><span id="st2">Actívalo para conectar el móvil</span></div>
</div>
<div style="height:6px"></div>
<label class="row"><span class="grow">Activo</span><input type="checkbox" class="sw" id="active"></label>
<label class="row"><span class="grow">Iniciar con Windows</span><input type="checkbox" class="sw" id="autostart"></label>
<div class="sep"></div>
<button class="row" id="qrBtn"><span class="i"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3M21 14v7h-7"/></svg></span><span class="grow">Mostrar código QR</span></button>
<button class="row" id="openBtn"><span class="i"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="14" rx="2"/><path d="M8 21h8"/></svg></span><span class="grow">Abrir TouchPad</span></button>
<button class="row" id="setBtn"><span class="i"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg></span><span class="grow">Ajustes</span></button>
<button class="row" id="updBtn"><span class="i"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg></span><span class="grow" id="updText">Buscar actualizaciones</span><small id="ver"></small></button>
<div class="sep"></div>
<button class="row" id="quitBtn"><span class="i"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 3v9"/><path d="M6.3 6.8a8 8 0 1 0 11.4 0"/></svg></span><span class="grow">Salir</span></button>
<script>
"use strict";
const $ = id => document.getElementById(id);
const api = () => window.pywebview.api;
let S = {};
function render(){
  const n = S.clients ? S.clients.length : 0;
  const st = $("status");
  st.classList.toggle("on", !!S.active); st.classList.toggle("off", !S.active);
  if (!S.active){ $("st1").textContent = "Desactivado"; $("st2").textContent = "Actívalo para conectar el móvil"; }
  else if (n){ $("st1").textContent = "Conectado"; $("st2").textContent = (n === 1 ? S.clients[0].device : n + " móviles") + ", por Wi-Fi"; }
  else { $("st1").textContent = "Esperando al móvil"; $("st2").textContent = "Escanea el código QR"; }
  $("active").checked = !!S.active;
  $("autostart").checked = !!S.autostart;
  $("ver").textContent = "v" + (S.version || "");
}
async function refresh(){ try { S = await api().get_state_tray(); render(); } catch (e) {} }
window.onShown = () => { $("updText").textContent = "Buscar actualizaciones"; refresh(); };
$("active").onchange = async e => { await api().set_active(e.target.checked); refresh(); };
$("autostart").onchange = e => api().set_autostart(e.target.checked);
$("qrBtn").onclick = () => api().show_main("qr");
$("openBtn").onclick = () => api().show_main("main");
$("setBtn").onclick = () => api().show_main("settings");
$("quitBtn").onclick = () => api().quit_app();
$("updBtn").onclick = async () => {
  $("updText").textContent = "Buscando…";
  try {
    const r = await api().check_updates();
    $("updText").textContent = !r.ok ? "Sin conexión" : (r.newer ? "Nueva: v" + r.latest : "Estás al día");
  } catch (e) { $("updText").textContent = "Sin conexión"; }
};
window.addEventListener("blur", () => { try { api().hide_tray(); } catch (e) {} });
document.addEventListener("keydown", e => { if (e.key === "Escape") api().hide_tray(); });
window.addEventListener("pywebviewready", () => { refresh(); setInterval(() => { if (document.hasFocus()) refresh(); }, 1000); });
</script>
</body>
</html>
"""
