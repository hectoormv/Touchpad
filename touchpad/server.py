"""Servidor de TouchPad: sirve la web del móvil y convierte sus gestos en ratón/teclado."""
import asyncio
import json
import os
import platform
import re
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import websockets

HTTP_PORT = 8765
WS_PORT = 8766
TOKEN_FILE = Path.home() / ".touchpad_host_token"   # mismo que la versión anterior


def resource(name):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / name


# --------------------------------------------------------------------------
# Inyección de ratón y teclado
# --------------------------------------------------------------------------
KEYMAP = {
    "next":      [(0x27, True)],
    "prev":      [(0x25, True)],
    "volup":     [(0xAF, False)],
    "voldown":   [(0xAE, False)],
    "mute":      [(0xAD, False)],
    "playpause": [(0xB3, False)],
    "start":     [(0x74, False)],                 # F5
    "esc":       [(0x1B, False)],
    "black":     [(0x42, False)],                 # B
    "laser":     [(0x11, False), (0x4C, False)],  # Ctrl+L
}


class WindowsInput:
    MOVE, LDOWN, LUP, RDOWN, RUP, MDOWN, MUP = 0x1, 0x2, 0x4, 0x8, 0x10, 0x20, 0x40
    WHEEL, HWHEEL = 0x0800, 0x01000
    KEYUP, EXTENDED = 0x2, 0x1

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct = ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        ULONG_PTR = ctypes.c_size_t

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ULONG_PTR)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                        ("wParamH", wintypes.WORD)]

        class _U(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        self.INPUT, self.MOUSEINPUT, self.KEYBDINPUT = INPUT, MOUSEINPUT, KEYBDINPUT
        user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        user32.SendInput.restype = wintypes.UINT
        self.user32 = user32
        self._fx = self._fy = self._wy = self._wx = 0.0

    def _send(self, *inputs):
        arr = (self.INPUT * len(inputs))(*inputs)
        self.user32.SendInput(len(inputs), arr, self.ct.sizeof(self.INPUT))

    def _mouse(self, flags, dx=0, dy=0, data=0):
        inp = self.INPUT(type=0)
        inp.mi = self.MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
        return inp

    def _key(self, vk, up=False, ext=False):
        inp = self.INPUT(type=1)
        inp.ki = self.KEYBDINPUT(vk, 0, (self.KEYUP if up else 0) | (self.EXTENDED if ext else 0), 0, 0)
        return inp

    def move(self, dx, dy):
        self._fx += dx
        self._fy += dy
        ix, iy = int(self._fx), int(self._fy)
        if ix or iy:
            self._fx -= ix
            self._fy -= iy
            self._send(self._mouse(self.MOVE, ix, iy))

    def button(self, b, down):
        flags = {"l": (self.LDOWN, self.LUP), "r": (self.RDOWN, self.RUP),
                 "m": (self.MDOWN, self.MUP)}.get(b, (self.LDOWN, self.LUP))
        self._send(self._mouse(flags[0] if down else flags[1]))

    def click(self, b):
        self.button(b, True)
        self.button(b, False)

    def scroll(self, dy, dx):
        self._wy += dy
        self._wx += dx
        out = []
        if abs(self._wy) >= 30:
            v = int(self._wy)
            self._wy -= v
            out.append(self._mouse(self.WHEEL, data=v))
        if abs(self._wx) >= 30:
            v = int(self._wx)
            self._wx -= v
            out.append(self._mouse(self.HWHEEL, data=v))
        if out:
            self._send(*out)

    def key(self, name):
        combo = KEYMAP.get(name)
        if not combo:
            return
        downs = [self._key(vk, False, ext) for vk, ext in combo]
        ups = [self._key(vk, True, ext) for vk, ext in reversed(combo)]
        self._send(*downs, *ups)


# Teclas en Mac: (código de tecla, modificadores) o ("media", código NX)
# Iniciar presentación = Cmd+Shift+Intro (PowerPoint y Google Slides; en Keynote es Cmd+Opción+P)
MAC_KEYMAP = {
    "next":      (124, ()),
    "prev":      (123, ()),
    "esc":       (53, ()),
    "black":     (11, ()),                    # B
    "start":     (36, ("cmd", "shift")),      # Intro
    "laser":     (37, ("cmd",)),              # L
    "volup":     ("media", 0),
    "voldown":   ("media", 1),
    "mute":      ("media", 7),
    "playpause": ("media", 16),
}


class MacInput:
    """Ratón y teclado en macOS con Quartz (necesita permiso de Accesibilidad)."""

    def __init__(self):
        import Quartz as Q
        self.Q = Q
        self._down = {"l": False, "r": False, "m": False}
        self._wy = self._wx = 0.0
        self._last = (0.0, None, 0)   # (hora, botón, nº de clics) para el doble clic
        self._flags = {"cmd": Q.kCGEventFlagMaskCommand, "shift": Q.kCGEventFlagMaskShift,
                       "alt": Q.kCGEventFlagMaskAlternate, "ctrl": Q.kCGEventFlagMaskControl}

    def _pos(self):
        return self.Q.CGEventGetLocation(self.Q.CGEventCreate(None))

    def _clamp(self, x, y):
        Q = self.Q
        err, ids, n = Q.CGGetActiveDisplayList(16, None, None)
        if err or not n:
            return x, y
        rects = [Q.CGDisplayBounds(d) for d in ids[:n]]
        for r in rects:   # si el punto está en alguna pantalla, vale
            if r.origin.x <= x < r.origin.x + r.size.width and r.origin.y <= y < r.origin.y + r.size.height:
                return x, y
        minx = min(r.origin.x for r in rects)
        miny = min(r.origin.y for r in rects)
        maxx = max(r.origin.x + r.size.width for r in rects) - 1
        maxy = max(r.origin.y + r.size.height for r in rects) - 1
        return max(minx, min(maxx, x)), max(miny, min(maxy, y))

    def _post(self, ev):
        self.Q.CGEventPost(self.Q.kCGHIDEventTap, ev)

    def move(self, dx, dy):
        Q = self.Q
        p = self._pos()
        x, y = self._clamp(p.x + dx, p.y + dy)
        if self._down["l"]:
            t, b = Q.kCGEventLeftMouseDragged, Q.kCGMouseButtonLeft
        elif self._down["r"]:
            t, b = Q.kCGEventRightMouseDragged, Q.kCGMouseButtonRight
        else:
            t, b = Q.kCGEventMouseMoved, Q.kCGMouseButtonLeft
        self._post(Q.CGEventCreateMouseEvent(None, t, (x, y), b))

    def button(self, b, down):
        Q = self.Q
        kinds = {"l": (Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp, Q.kCGMouseButtonLeft),
                 "r": (Q.kCGEventRightMouseDown, Q.kCGEventRightMouseUp, Q.kCGMouseButtonRight),
                 "m": (Q.kCGEventOtherMouseDown, Q.kCGEventOtherMouseUp, Q.kCGMouseButtonCenter)}
        dn, up, btn = kinds.get(b, kinds["l"])
        if down:
            now = time.monotonic()
            last_t, last_b, count = self._last
            count = count + 1 if (last_b == b and now - last_t < 0.45) else 1
            self._last = (now, b, count)
        clicks = self._last[2] if self._last[1] == b else 1
        ev = Q.CGEventCreateMouseEvent(None, dn if down else up, self._pos(), btn)
        Q.CGEventSetIntegerValueField(ev, Q.kCGMouseEventClickState, clicks)  # Mac necesita esto para el doble clic
        self._post(ev)
        self._down[b if b in self._down else "l"] = down

    def click(self, b):
        self.button(b, True)
        self.button(b, False)

    def scroll(self, dy, dx):
        # Llegan en "unidades de rueda" de Windows (120 = una muesca); en Mac usamos píxeles.
        self._wy += dy / 4
        self._wx += dx / 4
        iy, ix = int(self._wy), int(self._wx)
        if iy or ix:
            self._wy -= iy
            self._wx -= ix
            Q = self.Q
            self._post(Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitPixel, 2, iy, ix))

    def _media(self, code):
        from AppKit import NSEvent
        for down in (True, False):
            ev = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                14, (0, 0), 0xa00 if down else 0xb00, 0, 0, None, 8,
                (code << 16) | ((0xa if down else 0xb) << 8), -1)
            self._post(ev.CGEvent())

    def key(self, name):
        spec = MAC_KEYMAP.get(name)
        if not spec:
            return
        code, mods = spec
        if code == "media":
            self._media(mods)
            return
        Q = self.Q
        flags = 0
        for m in mods:
            flags |= self._flags[m]
        for down in (True, False):
            ev = Q.CGEventCreateKeyboardEvent(None, code, down)
            Q.CGEventSetFlags(ev, flags)
            self._post(ev)


class DryRunInput:
    """Fuera de Windows: imprime los eventos (para pruebas)."""
    def move(self, dx, dy): print(f"  move {dx:+.2f} {dy:+.2f}")
    def button(self, b, down): print(f"  button {b} {'down' if down else 'up'}")
    def click(self, b): print(f"  click {b}")
    def scroll(self, dy, dx): print(f"  scroll {dy:+.1f} {dx:+.1f}")
    def key(self, name): print(f"  key {name}" if name in KEYMAP else f"  key ignorada {name!r}")


def make_input():
    system = platform.system()
    if system == "Windows":
        return WindowsInput()
    if system == "Darwin":
        try:
            return MacInput()
        except ImportError:
            pass
    return DryRunInput()


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def num(v, lim=2000.0):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f != f:
        return 0.0
    return max(-lim, min(lim, f))


def local_ips():
    ips = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ips.append(s.getsockname()[0])
    except OSError:
        pass
    finally:
        s.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips or ["127.0.0.1"]


def load_token(new=False):
    if not new:
        try:
            tok = TOKEN_FILE.read_text().strip()
            if len(tok) >= 16:
                return tok
        except OSError:
            pass
    tok = secrets.token_urlsafe(16)
    try:
        TOKEN_FILE.write_text(tok)
        if os.name != "nt":
            TOKEN_FILE.chmod(0o600)
    except OSError:
        pass
    return tok


def delete_token():
    try:
        TOKEN_FILE.unlink()
    except OSError:
        pass


def qr_svg(text):
    import qrcode
    qr = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(text)
    qr.make(fit=True)
    m = qr.get_matrix()
    n = len(m)
    d = "".join(f"M{x} {y}h1v1h-1z" for y, row in enumerate(m) for x, v in enumerate(row) if v)
    return (f'<svg viewBox="0 0 {n} {n}" width="100%" height="100%" shape-rendering="crispEdges" '
            f'xmlns="http://www.w3.org/2000/svg"><path d="{d}" fill="#0B0B0C"/></svg>')


def device_name(ua):
    if not ua:
        return "Móvil"
    if "iPhone" in ua:
        return "iPhone"
    if "iPad" in ua:
        return "iPad"
    m = re.search(r"Android [\d.]+; ([^;)]+)", ua)
    if m:
        model = m.group(1).split(" Build")[0].strip()
        if model and model not in ("K", "wv"):
            return model
    if "Android" in ua:
        return "Android"
    return "Móvil"


# --------------------------------------------------------------------------
# Servidor (se puede arrancar y parar desde la app)
# --------------------------------------------------------------------------
class TouchpadServer:
    def __init__(self, inp=None):
        self.inp = inp or make_input()
        self.token = load_token()
        self.httpd = None
        self.loop = None
        self._stop_fut = None
        self._thread = None
        self._clients = {}
        self._lock = threading.Lock()
        self.ip = None

    @property
    def running(self):
        return self.httpd is not None

    @property
    def url(self):
        return f"http://{self.ip}:{HTTP_PORT}/?t={self.token}"

    def clients_list(self):
        with self._lock:
            return [dict(c) for c in self._clients.values()]

    # ---- HTTP: entrega la página del móvil ----
    def _http_handler(self):
        token = self.token
        page = resource("phone.html").read_text(encoding="utf-8").replace(
            "__WS_PORT__", str(WS_PORT)).encode("utf-8")

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                u = urlparse(self.path)
                if u.path != "/" or not secrets.compare_digest(
                        parse_qs(u.query).get("t", [""])[0], token):
                    self.send_response(403)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write("Enlace no válido. Escanea el QR de TouchPad en el PC.".encode())
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(page)

            def log_message(self, *args):
                pass
        return Handler

    # ---- WebSocket: recibe los gestos ----
    async def _ws_handler(self, ws):
        req = getattr(ws, "request", None)
        path = req.path if req is not None else getattr(ws, "path", "")
        if not secrets.compare_digest(parse_qs(urlparse(path).query).get("t", [""])[0], self.token):
            await ws.close(code=4001, reason="token")
            return
        headers = req.headers if req is not None else getattr(ws, "request_headers", {})
        ip = ws.remote_address[0] if ws.remote_address else "?"
        key = id(ws)
        with self._lock:
            self._clients[key] = {"ip": ip, "device": device_name(headers.get("User-Agent", ""))}
        inp = self.inp
        dragging = False
        try:
            async for raw in ws:
                try:
                    m = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(m, dict):
                    continue
                t = m.get("t")
                if t == "m":
                    inp.move(num(m.get("x"), 500), num(m.get("y"), 500))
                elif t == "s":
                    inp.scroll(num(m.get("y")), num(m.get("x")))
                elif t == "c":
                    inp.click(m.get("b") if m.get("b") in ("l", "r", "m") else "l")
                elif t == "dn":
                    inp.button("l", True)
                    dragging = True
                elif t == "up":
                    inp.button("l", False)
                    dragging = False
                elif t == "k":
                    inp.key(str(m.get("k")))
        except websockets.ConnectionClosed:
            pass
        finally:
            if dragging:
                inp.button("l", False)
            with self._lock:
                self._clients.pop(key, None)

    # ---- Arrancar / parar ----
    def start(self):
        if self.running:
            return
        self.ip = local_ips()[0]
        httpd = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), self._http_handler())  # OSError si está ocupado
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()
        err = []

        def run():
            asyncio.set_event_loop(self.loop)

            async def main():
                self._stop_fut = self.loop.create_future()
                try:
                    server = await websockets.serve(self._ws_handler, "0.0.0.0", WS_PORT,
                                                    max_size=4096, compression=None)
                except OSError as e:
                    err.append(e)
                    ready.set()
                    return
                ready.set()
                await self._stop_fut
                server.close()
                await server.wait_closed()

            self.loop.run_until_complete(main())
            self.loop.close()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        ready.wait(10)
        if err:
            httpd.server_close()
            raise err[0]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.httpd = httpd

    def stop(self):
        if not self.running:
            return
        httpd, self.httpd = self.httpd, None
        httpd.shutdown()
        httpd.server_close()
        if self.loop and self._stop_fut and not self._stop_fut.done():
            self.loop.call_soon_threadsafe(
                lambda: self._stop_fut.done() or self._stop_fut.set_result(None))
        if self._thread:
            self._thread.join(5)
        with self._lock:
            self._clients.clear()
