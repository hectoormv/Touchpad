"""TouchPad para Windows y Mac: instalar, activar (QR), desactivar y desinstalar.

Se empaqueta como un único TouchPad.exe con PyInstaller (ver .github/workflows/build.yml).
Argumentos: --background (arranque con Windows: oculto y activado), --uninstall.
"""
import base64
import json
import os
import plistlib
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import webview

import server
import tray_ui
from icon import draw_icon

VERSION = "1.2.0"
RELEASES_API = "https://api.github.com/repos/hectoormv/touchpad/releases/latest"
RELEASES_PAGE = "https://github.com/hectoormv/touchpad/releases/latest"
APP = "TouchPad"
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
FROZEN = getattr(sys, "frozen", False)

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
APPDATA = Path(os.environ.get("APPDATA", str(Path.home())))
INSTALL_DIR = LOCALAPPDATA / APP
INSTALLED_EXE = INSTALL_DIR / "TouchPad.exe"
START_MENU = APPDATA / "Microsoft" / "Windows" / "Start Menu" / "Programs"
STARTUP = START_MENU / "Startup"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\TouchPad"
# --- Mac ---
MAC_SUPPORT = Path.home() / "Library" / "Application Support" / APP
MAC_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.hectoormv.touchpad.plist"
MAC_ACCESSIBILITY_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"

SETTINGS_FILE = (MAC_SUPPORT if IS_MAC else INSTALL_DIR) / "settings.json"
LOCK_PORT = 47823
NO_WINDOW = 0x08000000 if IS_WIN else 0
DETACHED = 0x00000008 if IS_WIN else 0

if IS_WIN:
    import winreg


# --------------------------------------------------------------------------
# PowerShell y permisos de administrador
# --------------------------------------------------------------------------
def q(s):
    """Escapa texto para meterlo entre comillas simples en PowerShell."""
    return str(s).replace("'", "''")


def run_ps(cmd, timeout=60):
    try:
        return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                              capture_output=True, text=True, timeout=timeout,
                              creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None


def encode_ps(script):
    return base64.b64encode(script.encode("utf-16-le")).decode()


def run_elevated(script):
    """Ejecuta un script como administrador (sale el aviso de Windows). True si se aceptó."""
    outer = ("try { Start-Process powershell -ArgumentList '-NoProfile -WindowStyle Hidden "
             f"-EncodedCommand {encode_ps(script)}' -Verb RunAs -WindowStyle Hidden -Wait "
             "-ErrorAction Stop; exit 0 } catch { exit 1 }")
    r = run_ps(outer, timeout=300)
    return bool(r) and r.returncode == 0


FIREWALL_ADD = ("Remove-NetFirewallRule -DisplayName 'TouchPad' -ErrorAction SilentlyContinue; "
                "New-NetFirewallRule -DisplayName 'TouchPad' -Direction Inbound -Action Allow "
                f"-Protocol TCP -LocalPort {server.HTTP_PORT},{server.WS_PORT} -Profile Private | Out-Null; ")
FIREWALL_REMOVE = "Remove-NetFirewallRule -DisplayName 'TouchPad' -ErrorAction SilentlyContinue"


# --------------------------------------------------------------------------
# Sistema: accesos directos, registro, redes, Python
# --------------------------------------------------------------------------
def desktop_dir():
    r = run_ps("[Environment]::GetFolderPath('Desktop')")
    if r and r.stdout.strip():
        return Path(r.stdout.strip())
    return Path.home() / "Desktop"


def make_shortcut(lnk, target, args=""):
    target = Path(target)
    run_ps(f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{q(lnk)}');"
           f"$s.TargetPath='{q(target)}';$s.Arguments='{q(args)}';"
           f"$s.WorkingDirectory='{q(target.parent)}';$s.IconLocation='{q(target)},0';"
           "$s.Description='TouchPad: usa el movil como trackpad';$s.Save()")


def unlink(p):
    try:
        Path(p).unlink()
    except OSError:
        pass


def get_autostart():
    if IS_MAC:
        return MAC_AGENT.exists()
    if not IS_WIN:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP)
            return True
    except OSError:
        return False


def set_autostart(on):
    if IS_MAC:
        mac_set_autostart(on)
        return
    if not IS_WIN:
        return
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if on:
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, f'"{INSTALLED_EXE}" --background')
        else:
            try:
                winreg.DeleteValue(k, APP)
            except OSError:
                pass


def register_uninstall():
    """Hace que TouchPad aparezca en Configuración > Aplicaciones."""
    if not IS_WIN:
        return
    size_kb = INSTALLED_EXE.stat().st_size // 1024 if INSTALLED_EXE.exists() else 0
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, val in [("DisplayName", "TouchPad"), ("DisplayVersion", VERSION),
                          ("Publisher", "hectoormv"), ("DisplayIcon", f"{INSTALLED_EXE},0"),
                          ("InstallLocation", str(INSTALL_DIR)),
                          ("UninstallString", f'"{INSTALLED_EXE}" --uninstall')]:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)


def unregister_uninstall():
    if not IS_WIN:
        return
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass


def public_networks():
    r = run_ps("Get-NetConnectionProfile | Where-Object NetworkCategory -eq 'Public' | "
               "Select-Object Name,InterfaceIndex | ConvertTo-Json -Compress")
    if not r or not r.stdout.strip():
        return []
    try:
        data = json.loads(r.stdout)
    except ValueError:
        return []
    return data if isinstance(data, list) else [data]


def python_versions():
    """Versiones de Python instaladas en el sistema (p. ej. ['3.12'])."""
    if not IS_WIN:
        return []
    found = set()
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r"Software\Python\PythonCore", 0,
                                    winreg.KEY_READ | view) as k:
                    i = 0
                    while True:
                        try:
                            tag = winreg.EnumKey(k, i)
                        except OSError:
                            break
                        i += 1
                        parts = tag.split(".")
                        if len(parts) == 2 and parts[0] == "3" and parts[1].isdigit():
                            found.add(tag)
            except OSError:
                pass
    return sorted(found, key=lambda v: int(v.split(".")[1]))


def uninstall_python(versions):
    """Desinstala Python con winget. Devuelve True si se pudo lanzar."""
    if not shutil.which("winget"):
        os.startfile("ms-settings:appsfeatures")
        return False
    ids = [f"Python.Python.{v}" for v in versions] + ["Python.Launcher"]
    for pkg in ids:
        try:
            subprocess.run(["winget", "uninstall", "--id", pkg, "-e", "--silent",
                            "--accept-source-agreements"],
                           capture_output=True, timeout=900, creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return True


def cleanup_old_version():
    """Quita la versión anterior (script de Python + .bat) si estaba instalada."""
    run_ps("Get-CimInstance Win32_Process -Filter \"Name LIKE 'python%'\" | "
           "Where-Object { $_.CommandLine -like '*touchpad_host.py*' } | "
           "ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null }")
    unlink(STARTUP / "TouchPad.lnk")
    for name in ("touchpad_host.py", "TouchPad.bat", "desinstalar.ps1"):
        unlink(INSTALL_DIR / name)


def schedule_self_delete():
    """Borra la carpeta del programa unos segundos después de cerrarse."""
    script = (f"Start-Sleep -Seconds 3; Remove-Item -LiteralPath '{q(INSTALL_DIR)}' "
              "-Recurse -Force -ErrorAction SilentlyContinue")
    subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden",
                      "-EncodedCommand", encode_ps(script)],
                     creationflags=NO_WINDOW | DETACHED, close_fds=True)


def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_setting(key, value):
    data = load_settings()
    data[key] = value
    try:
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def get_theme():
    t = load_settings().get("theme", "auto")
    return t if t in ("auto", "light", "dark") else "auto"


def system_is_dark():
    """True si el sistema (Windows o Mac) está en modo oscuro."""
    if IS_MAC:
        try:
            r = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                               capture_output=True, text=True, timeout=5)
            return "Dark" in r.stdout
        except (OSError, subprocess.TimeoutExpired):
            return False
    if not IS_WIN:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


# --------------------------------------------------------------------------
# Mac
# --------------------------------------------------------------------------
def mac_current_bundle():
    """Carpeta TouchPad.app desde la que se está ejecutando (o None)."""
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


def mac_install_target():
    apps = Path("/Applications")
    base = apps if os.access(apps, os.W_OK) else Path.home() / "Applications"
    return base / "TouchPad.app"


def mac_installed_bundle():
    for base in (Path("/Applications"), Path.home() / "Applications"):
        b = base / "TouchPad.app"
        if b.exists():
            return b
    return None


def mac_is_installed_copy():
    cur = mac_current_bundle()
    return cur is not None and cur.parent in (Path("/Applications"), Path.home() / "Applications")


def mac_set_autostart(on):
    if on:
        bundle = mac_current_bundle() if mac_is_installed_copy() else mac_installed_bundle()
        if not bundle:
            return
        MAC_AGENT.parent.mkdir(parents=True, exist_ok=True)
        with open(MAC_AGENT, "wb") as f:
            plistlib.dump({"Label": "com.hectoormv.touchpad",
                           "ProgramArguments": [str(bundle / "Contents" / "MacOS" / "TouchPad"),
                                                "--background"],
                           "RunAtLoad": True}, f)
    else:
        unlink(MAC_AGENT)


def mac_trusted(prompt=False):
    """¿Tiene TouchPad permiso de Accesibilidad (necesario para mover el ratón)?"""
    if not IS_MAC:
        return True
    try:
        try:
            from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
        except ImportError:
            from HIServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: bool(prompt)}))
    except Exception:  # noqa: BLE001 - si no se puede comprobar, no bloqueamos
        return True


def mac_open_accessibility():
    subprocess.Popen(["open", MAC_ACCESSIBILITY_URL])


def same_path(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


# --------------------------------------------------------------------------
# Windows: colocar el menú de cristal junto al reloj
# --------------------------------------------------------------------------
TRAY_TITLE = "TouchPad Menu"


def win_place_popup(title, style=True):
    """Coloca la ventana del menú junto al cursor (sobre la barra de tareas), por encima
    de todo, sin botón en la barra de tareas y con esquinas redondeadas en Windows 11."""
    if not IS_WIN:
        return False
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.FindWindowW.restype = wintypes.HWND
    hwnd = u.FindWindowW(None, title)
    if not hwnd:
        return False
    if style:
        get = getattr(u, "GetWindowLongPtrW", u.GetWindowLongW)
        setl = getattr(u, "SetWindowLongPtrW", u.SetWindowLongW)
        get.restype = ctypes.c_ssize_t
        get.argtypes = [wintypes.HWND, ctypes.c_int]
        setl.restype = ctypes.c_ssize_t
        setl.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        ex = get(hwnd, -20)                                   # GWL_EXSTYLE
        setl(hwnd, -20, (ex | 0x80) & ~0x40000)               # TOOLWINDOW, sin APPWINDOW
        try:
            pref = ctypes.c_int(2)                            # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
        except Exception:  # noqa: BLE001 - Windows 10 no lo tiene
            pass

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    pt = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(pt))
    rect = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    u.MonitorFromPoint.restype = wintypes.HANDLE
    u.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(mi)
    u.GetMonitorInfoW(u.MonitorFromPoint(pt, 2), ctypes.byref(mi))
    wk = mi.rcWork
    x = max(wk.left + 8, min(pt.x - w // 2, wk.right - w - 8))
    y = pt.y - h - 12
    if y < wk.top + 8:            # barra de tareas arriba: abrir hacia abajo
        y = pt.y + 12
    y = max(wk.top + 8, min(y, wk.bottom - h - 8))
    # HWND_TOPMOST, SWP_NOSIZE | SWP_SHOWWINDOW | SWP_FRAMECHANGED
    u.SetWindowPos(hwnd, wintypes.HWND(-1), x, y, 0, 0, 0x0001 | 0x0040 | 0x0020)
    u.SetForegroundWindow(hwnd)
    return True


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3])


def check_latest_release():
    req = urllib.request.Request(RELEASES_API, headers={"User-Agent": "TouchPad",
                                                        "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.load(r)
    return str(data.get("tag_name", "")).lstrip("vV"), data.get("html_url") or RELEASES_PAGE


# --------------------------------------------------------------------------
# Una sola instancia
# --------------------------------------------------------------------------
def acquire_lock():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(4)
        return s
    except OSError:
        s.close()
        return None


def send_command(cmd):
    try:
        with socket.create_connection(("127.0.0.1", LOCK_PORT), timeout=2) as c:
            c.sendall(cmd.encode())
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------
# La aplicación
# --------------------------------------------------------------------------
class App:
    def __init__(self, lock, background, want_uninstall):
        self.lock = lock
        self.background = background
        self.want_uninstall = want_uninstall
        if not FROZEN:
            self.installed = True
        elif IS_MAC:
            self.installed = mac_is_installed_copy()
        else:
            self.installed = same_path(sys.executable, INSTALLED_EXE)
        self.asked_permission = False
        self.srv = server.TouchpadServer()
        self.window = None
        self.tray = None
        self.qr = ""
        self.error = ""
        self.public_nets = []
        self.py_versions = []
        self.tray_hint_shown = False
        self.quitting = False
        self.tray_win = None          # menú de cristal (Windows)
        self._tray_visible = False
        self._tray_hidden_at = 0.0
        self._tray_styled = False
        self.go_to = None             # pantalla a abrir en la ventana principal
        self.srv.set_config(load_settings().get("trackpad", {}))

    # ---------- estado para la interfaz ----------
    def state(self):
        active = self.srv.running
        s = {
            "version": VERSION,
            "installed": self.installed,
            "is_update": (not self.installed) and bool(
                mac_installed_bundle() if IS_MAC else INSTALLED_EXE.exists()),
            "platform": "mac" if IS_MAC else "windows",
            "needs_permission": IS_MAC and self.installed and not mac_trusted(False),
            "active": active,
            "clients": self.srv.clients_list() if active else [],
            "autostart": get_autostart() if self.installed else True,
            "public_networks": [n.get("Name", "Red") for n in self.public_nets],
            "python_versions": self.py_versions,
            "want_uninstall": self.want_uninstall,
            "error": self.error,
            "settings": dict(self.srv.config),
            "go_to": self.go_to,
        }
        if active:
            s.update(url=self.srv.url, ip=self.srv.ip, qr=self.qr)
        self.want_uninstall = False
        self.error = ""
        self.go_to = None
        return s

    def state_tray(self):
        active = self.srv.running
        return {"version": VERSION, "active": active, "autostart": get_autostart(),
                "clients": self.srv.clients_list() if active else []}

    # ---------- ajustes del trackpad ----------
    def set_setting(self, key, value):
        if key not in server.DEFAULT_CONFIG:
            return {"ok": False}
        cfg = dict(self.srv.config)
        cfg[key] = value
        self.srv.set_config(cfg)
        save_setting("trackpad", self.srv.config)
        return {"ok": True, "settings": dict(self.srv.config)}

    # ---------- actualizaciones ----------
    def check_updates(self):
        try:
            latest, url = check_latest_release()
        except Exception:  # noqa: BLE001
            return {"ok": False, "msg": "No se pudo comprobar. ¿Hay conexión a internet?"}
        newer = version_tuple(latest) > version_tuple(VERSION)
        if newer:
            webbrowser.open(url)
        return {"ok": True, "latest": latest, "newer": newer}

    # ---------- menú de cristal de la bandeja (Windows) ----------
    def show_tray_popup(self):
        w = self.tray_win
        if not w:
            return
        if self._tray_visible or time.monotonic() - self._tray_hidden_at < 0.35:
            self.hide_tray_popup()        # un segundo clic en el icono lo cierra
            return
        w.show()
        win_place_popup(TRAY_TITLE, style=not self._tray_styled)
        self._tray_styled = True
        self._tray_visible = True
        try:
            w.evaluate_js("window.onShown && window.onShown()")
        except Exception:  # noqa: BLE001
            pass

    def hide_tray_popup(self):
        if self.tray_win and self._tray_visible:
            self._tray_visible = False
            self._tray_hidden_at = time.monotonic()
            try:
                self.tray_win.hide()
            except Exception:  # noqa: BLE001
                pass

    def show_main(self, screen=None):
        self.hide_tray_popup()
        if screen == "qr" and not self.srv.running:
            r = self.activate()
            if not r["ok"]:
                self.error = r["msg"]
        self.go_to = "settings" if screen == "settings" else "main"
        self.show()
        try:
            self.window.evaluate_js(f"window.goTo && window.goTo({json.dumps(self.go_to)})")
        except Exception:  # noqa: BLE001
            pass

    # ---------- activar / desactivar ----------
    def activate(self):
        try:
            self.srv.start()
        except OSError:
            return {"ok": False, "msg": "El puerto 8765 está ocupado. Cierra otras copias de "
                                        "TouchPad (o la versión antigua) y vuelve a intentarlo."}
        self.qr = server.qr_svg(self.srv.url)
        self.update_tray()
        if IS_MAC and not self.asked_permission and not mac_trusted(False):
            self.asked_permission = True
            mac_trusted(prompt=True)   # macOS muestra su aviso de Accesibilidad
        return {"ok": True}

    def deactivate(self):
        self.srv.stop()
        self.update_tray()
        return {"ok": True}

    def toggle(self):
        if self.srv.running:
            self.deactivate()
        else:
            r = self.activate()
            if not r["ok"]:
                self.error = r["msg"]
                self.show()

    # ---------- instalar ----------
    def install(self, autostart, make_private):
        if IS_MAC:
            return self.install_mac(autostart)
        try:
            cleanup_old_version()
            INSTALL_DIR.mkdir(parents=True, exist_ok=True)
            if FROZEN:
                for _ in range(20):           # la versión anterior puede estar cerrándose
                    try:
                        shutil.copy2(sys.executable, INSTALLED_EXE)
                        break
                    except PermissionError:
                        time.sleep(0.5)
                else:
                    return {"ok": False, "msg": "No se pudo reemplazar la versión instalada. "
                                                "Ciérrala desde la bandeja y vuelve a intentarlo."}
            make_shortcut(desktop_dir() / "TouchPad.lnk", INSTALLED_EXE)
            make_shortcut(START_MENU / "TouchPad.lnk", INSTALLED_EXE)
            register_uninstall()
            set_autostart(autostart)

            script = FIREWALL_ADD
            if make_private:
                for n in self.public_nets:
                    idx = int(n.get("InterfaceIndex", -1))
                    if idx >= 0:
                        script += f"Set-NetConnectionProfile -InterfaceIndex {idx} -NetworkCategory Private; "
            firewall_ok = run_elevated(script)

            if FROZEN:
                self.release_lock()
                subprocess.Popen([str(INSTALLED_EXE)], cwd=str(INSTALL_DIR),
                                 creationflags=DETACHED, close_fds=True)
                threading.Timer(1.0, self.quit).start()
            else:
                self.installed = True
            return {"ok": True, "firewall": firewall_ok}
        except Exception as e:  # noqa: BLE001 - se muestra al usuario
            return {"ok": False, "msg": f"Error al instalar: {e}"}

    def install_mac(self, autostart):
        try:
            src = mac_current_bundle()
            if not FROZEN or src is None:
                self.installed = True
                return {"ok": True}
            target = mac_install_target()
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(src, target, symlinks=True)
            # Quita la marca de "descargado de internet" para que abra sin avisos
            subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(target)], capture_output=True)
            MAC_SUPPORT.mkdir(parents=True, exist_ok=True)
            if autostart:
                MAC_AGENT.parent.mkdir(parents=True, exist_ok=True)
                with open(MAC_AGENT, "wb") as f:
                    plistlib.dump({"Label": "com.hectoormv.touchpad",
                                   "ProgramArguments": [str(target / "Contents" / "MacOS" / "TouchPad"),
                                                        "--background"],
                                   "RunAtLoad": True}, f)
            else:
                unlink(MAC_AGENT)
            self.release_lock()
            subprocess.Popen(["open", "-n", str(target)])
            threading.Timer(1.0, self.quit).start()
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "msg": f"Error al instalar: {e}"}

    # ---------- desinstalar ----------
    def uninstall(self, remove_python):
        if IS_MAC:
            return self.uninstall_mac()
        self.srv.stop()
        set_autostart(False)
        for lnk in (desktop_dir() / "TouchPad.lnk", START_MENU / "TouchPad.lnk",
                    STARTUP / "TouchPad.lnk"):
            unlink(lnk)
        unregister_uninstall()
        server.delete_token()
        run_elevated(FIREWALL_REMOVE)
        python_ok = uninstall_python(self.py_versions) if (remove_python and self.py_versions) else None
        if FROZEN:
            schedule_self_delete()
        threading.Timer(2.5, self.quit).start()
        return {"ok": True, "python": python_ok}

    def uninstall_mac(self):
        self.srv.stop()
        unlink(MAC_AGENT)
        server.delete_token()
        shutil.rmtree(MAC_SUPPORT, ignore_errors=True)
        bundle = mac_current_bundle() if FROZEN else None
        if bundle:
            subprocess.Popen(["/bin/sh", "-c", f"sleep 3; rm -rf {shlex.quote(str(bundle))}"],
                             start_new_session=True)
        threading.Timer(2.5, self.quit).start()
        return {"ok": True, "python": None}

    # ---------- ventana y bandeja ----------
    def show(self):
        if self.window:
            self.window.show()
            self.window.restore()

    def minimize(self):
        self.window.minimize()

    def close(self):
        """Botón cerrar: si está activo se queda en la bandeja (o en el Dock en Mac)."""
        if IS_MAC and self.srv.running:
            self.window.minimize()
            return
        if self.srv.running and self.tray and self.installed:
            self.window.hide()
            if not self.tray_hint_shown:
                self.tray_hint_shown = True
                try:
                    self.tray.notify("Sigue activo. Lo tienes en la bandeja, junto al reloj.", "TouchPad")
                except Exception:  # noqa: BLE001
                    pass
        else:
            self.quit()

    def on_closing(self):
        if self.quitting:
            return True
        if IS_MAC and self.srv.running:
            self.window.minimize()
            return False
        if self.srv.running and self.tray and self.installed:
            self.window.hide()
            return False
        self.quitting = True
        self._shutdown()
        if self.tray_win:   # si no, la ventana oculta del menú mantendría vivo el programa
            tw = self.tray_win
            threading.Timer(0.1, lambda: tw.destroy()).start()
        return True

    def _shutdown(self):
        self.srv.stop()
        if self.tray:
            try:
                self.tray.stop()
            except Exception:  # noqa: BLE001
                pass
        self.release_lock()

    def quit(self):
        if self.quitting:
            return
        self.quitting = True
        self._shutdown()
        if self.tray_win:
            try:
                self.tray_win.destroy()
            except Exception:  # noqa: BLE001
                pass
        if self.window:
            self.window.destroy()

    def release_lock(self):
        if self.lock:
            try:
                self.lock.close()
            except OSError:
                pass
            self.lock = None

    def start_tray(self):
        try:
            import pystray
        except ImportError:
            return
        menu = pystray.Menu(
            pystray.MenuItem("Abrir TouchPad", lambda: self.show(), default=True),
            pystray.MenuItem(lambda item: "Desactivar" if self.srv.running else "Activar",
                             lambda: self.toggle()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Salir", lambda: self.quit()),
        )
        IconClass = pystray.Icon
        if self.tray_win is not None:
            try:
                from pystray import _win32
                app = self
                if hasattr(_win32.Icon, "_on_notify"):
                    class GlassIcon(_win32.Icon):
                        def _on_notify(self, wparam, lparam):
                            if lparam == 0x0205:          # WM_RBUTTONUP: menú de cristal
                                app.show_tray_popup()
                                return None
                            return super()._on_notify(wparam, lparam)
                    IconClass = GlassIcon
            except Exception:  # noqa: BLE001 - si falla, se usa el menú normal de Windows
                IconClass = pystray.Icon
        self.tray = IconClass("TouchPad", draw_icon(64, False), "TouchPad: desactivado", menu)
        self.tray.run_detached()

    def update_tray(self):
        if not self.tray:
            return
        active = self.srv.running
        self.tray.icon = draw_icon(64, active)
        self.tray.title = "TouchPad: activo" if active else "TouchPad: desactivado"
        try:
            self.tray.update_menu()
        except Exception:  # noqa: BLE001
            pass

    def listen_commands(self):
        lock = self.lock
        while lock and not self.quitting:
            try:
                conn, _ = lock.accept()
            except OSError:
                return
            with conn:
                try:
                    cmd = conn.recv(64).decode(errors="ignore").strip()
                except OSError:
                    continue
            if cmd == "show":
                self.show()
            elif cmd == "uninstall":
                self.want_uninstall = True
                self.show()
            elif cmd == "quit":
                self.quit()
                return

    def on_start(self):
        threading.Thread(target=self.listen_commands, daemon=True).start()
        if IS_WIN:
            def gather():
                self.public_nets = public_networks()
                self.py_versions = python_versions()
            threading.Thread(target=gather, daemon=True).start()
        if self.installed:
            if IS_WIN:
                self.start_tray()
            if self.background:
                r = self.activate()
                if not r["ok"]:
                    self.error = r["msg"]


class Api:
    """Funciones que la interfaz (ui.html) puede llamar."""
    def __init__(self, app):
        self._app = app

    def get_state(self):
        return self._app.state()

    def install(self, opts):
        opts = opts or {}
        return self._app.install(bool(opts.get("autostart", True)), bool(opts.get("private", False)))

    def activate(self):
        return self._app.activate()

    def deactivate(self):
        return self._app.deactivate()

    def set_autostart(self, on):
        set_autostart(bool(on))
        return {"ok": True}

    def set_theme(self, theme):
        if theme in ("auto", "light", "dark"):
            save_setting("theme", theme)
        return {"ok": True}

    def uninstall(self, remove_python):
        return self._app.uninstall(bool(remove_python))

    def get_state_tray(self):
        return self._app.state_tray()

    def set_active(self, on):
        return self._app.activate() if on else self._app.deactivate()

    def set_setting(self, key, value):
        return self._app.set_setting(str(key), value)

    def check_updates(self):
        return self._app.check_updates()

    def show_main(self, screen=None):
        self._app.show_main(screen)

    def hide_tray(self):
        self._app.hide_tray_popup()

    def quit_app(self):
        self._app.hide_tray_popup()
        threading.Timer(0.1, self._app.quit).start()

    def open_accessibility(self):
        mac_open_accessibility()
        return {"ok": True}

    def minimize(self):
        self._app.minimize()

    def close(self):
        self._app.close()


def main():
    args = sys.argv[1:]
    background = "--background" in args
    want_uninstall = "--uninstall" in args
    if not FROZEN:
        is_installed_copy = True
    elif IS_MAC:
        is_installed_copy = mac_is_installed_copy()
    else:
        is_installed_copy = same_path(sys.executable, INSTALLED_EXE)

    lock = acquire_lock()
    if lock is None:
        if is_installed_copy:
            # Ya está abierto: lo mostramos y salimos.
            send_command("uninstall" if want_uninstall else ("show" if not background else ""))
            return
        # Es el instalador/actualizador: cerramos la copia instalada para poder reemplazarla.
        send_command("quit")
        for _ in range(30):
            time.sleep(0.2)
            lock = acquire_lock()
            if lock:
                break
        if lock is None:
            return

    app = App(lock, background, want_uninstall)
    # En Mac se usa la barra de título nativa (con los botones de colores) y, al arrancar
    # con el sistema, la ventana empieza minimizada en el Dock en vez de oculta.
    window = webview.create_window(
        "TouchPad", url=str(server.resource("ui.html")), js_api=Api(app),
        width=440, height=680 if not IS_MAC else 730, resizable=False,
        frameless=not IS_MAC, easy_drag=False,
        hidden=background and app.installed and not IS_MAC,
        minimized=background and app.installed and IS_MAC,
        background_color="#0B1220")
    app.window = window
    window.events.closing += app.on_closing
    if IS_WIN and app.installed:
        tray_win = webview.create_window(
            TRAY_TITLE, html=tray_ui.HTML, js_api=Api(app), width=284, height=380,
            resizable=False, frameless=True, easy_drag=False, on_top=True, hidden=True,
            background_color="#0F1828")
        app.tray_win = tray_win

        def tray_closing():
            if app.quitting:
                return True
            app.hide_tray_popup()
            return False
        tray_win.events.closing += tray_closing
    webview.start(app.on_start)
    app.release_lock()


if __name__ == "__main__":
    main()
