"""TouchPad para Windows: instalar, activar (QR), desactivar y desinstalar.

Se empaqueta como un único TouchPad.exe con PyInstaller (ver .github/workflows/build.yml).
Argumentos: --background (arranque con Windows: oculto y activado), --uninstall.
"""
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import webview

import server
from icon import draw_icon

VERSION = "1.0.0"
APP = "TouchPad"
IS_WIN = os.name == "nt"
FROZEN = getattr(sys, "frozen", False)

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
APPDATA = Path(os.environ.get("APPDATA", str(Path.home())))
INSTALL_DIR = LOCALAPPDATA / APP
INSTALLED_EXE = INSTALL_DIR / "TouchPad.exe"
START_MENU = APPDATA / "Microsoft" / "Windows" / "Start Menu" / "Programs"
STARTUP = START_MENU / "Startup"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\TouchPad"
SETTINGS_FILE = INSTALL_DIR / "settings.json"
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
    if not IS_WIN:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP)
            return True
    except OSError:
        return False


def set_autostart(on):
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
    """True si Windows está en modo oscuro para las aplicaciones."""
    if not IS_WIN:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def same_path(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


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
        self.installed = (not FROZEN) or same_path(sys.executable, INSTALLED_EXE)
        self.srv = server.TouchpadServer()
        self.window = None
        self.tray = None
        self.qr = ""
        self.error = ""
        self.public_nets = []
        self.py_versions = []
        self.tray_hint_shown = False
        self.quitting = False

    # ---------- estado para la interfaz ----------
    def state(self):
        active = self.srv.running
        s = {
            "version": VERSION,
            "installed": self.installed,
            "is_update": (not self.installed) and INSTALLED_EXE.exists(),
            "active": active,
            "clients": self.srv.clients_list() if active else [],
            "autostart": get_autostart() if self.installed else True,
            "public_networks": [n.get("Name", "Red") for n in self.public_nets],
            "python_versions": self.py_versions,
            "want_uninstall": self.want_uninstall,
            "error": self.error,
            "theme": get_theme(),
        }
        if active:
            s.update(url=self.srv.url, ip=self.srv.ip, qr=self.qr)
        self.want_uninstall = False
        self.error = ""
        return s

    # ---------- activar / desactivar ----------
    def activate(self):
        try:
            self.srv.start()
        except OSError:
            return {"ok": False, "msg": "El puerto 8765 está ocupado. Cierra otras copias de "
                                        "TouchPad (o la versión antigua) y vuelve a intentarlo."}
        self.qr = server.qr_svg(self.srv.url)
        self.update_tray()
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

    # ---------- desinstalar ----------
    def uninstall(self, remove_python):
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

    # ---------- ventana y bandeja ----------
    def show(self):
        if self.window:
            self.window.show()
            self.window.restore()

    def minimize(self):
        self.window.minimize()

    def close(self):
        """Botón cerrar: si está activo se queda en la bandeja; si no, se cierra."""
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
        if self.srv.running and self.tray and self.installed:
            self.window.hide()
            return False
        self.quitting = True
        self._shutdown()
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
        self.tray = pystray.Icon("TouchPad", draw_icon(64, False), "TouchPad: desactivado", menu)
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

    def minimize(self):
        self._app.minimize()

    def close(self):
        self._app.close()


def main():
    args = sys.argv[1:]
    background = "--background" in args
    want_uninstall = "--uninstall" in args
    is_installed_copy = (not FROZEN) or same_path(sys.executable, INSTALLED_EXE)

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
    theme = get_theme()
    dark = theme == "dark" or (theme == "auto" and system_is_dark())
    window = webview.create_window(
        "TouchPad", url=str(server.resource("ui.html")), js_api=Api(app),
        width=440, height=680, resizable=False, frameless=True, easy_drag=False,
        hidden=background and app.installed, background_color="#000000" if dark else "#F2F2F7")
    app.window = window
    window.events.closing += app.on_closing
    webview.start(app.on_start)
    app.release_lock()


if __name__ == "__main__":
    main()
