"""Serve the WebXR scene, keep the headset, the laptop and the scene files in sync, and run the game server.

    python serve.py            # HTTP on port 8000: the laptop, phones, the join screen, the Quest over USB
    python serve.py --https    # also HTTPS on port 8443, for opening on the Quest over Wi-Fi

Phones use plain HTTP (no certificate warning; the audience app needs no secure context).
WebXR only runs in a "secure context": localhost, or HTTPS. The laptop browser is fine
over plain HTTP on localhost. The Quest browser either needs the HTTPS listener (a self-signed
certificate is generated into .certs/; accept the warning in the headset), or a USB cable plus
`adb reverse tcp:8000 tcp:8000` so that the Quest's http://localhost:8000 reaches this server.
All listeners share one scene and one game.

The address phones should use is worked out automatically (in WSL2 with NAT networking, by
asking Windows for its Wi-Fi address, and printing the port forward Windows still needs).
Override it with --join-host or WORLDTINDER_JOIN_HOST.

The chat box posts to /api/chat, handled by scene_agent.py with the Claude API. Set your key in
the shell before starting the server (export ANTHROPIC_API_KEY=...); it stays on this machine.
Optional: WORLDTINDER_MODEL (default claude-opus-5-5) and WORLDTINDER_EFFORT (default low).

One headset, many viewers
-------------------------
The page in the Quest's browser is the headset: the one interactive copy of the scene. Every other page
(e.g. http://localhost:8000 on the laptop) is a viewer. Pages talk to this server over a WebSocket (/ws).

  headset -> server   its pose 30 times a second, and its full scene state 10 times a second. The server
                      keeps the latest of each (that's what GET /api/state returns) and relays them to viewers,
                      which move their copies of the headset's objects to match.
  server -> all pages scene edits, which pages apply as they arrive, without reloading.

Scene edits are an ordered log. An edit is either
  - commands: the add_object / update_object / remove_object calls the chat box uses, or
  - a patch: a named snippet of JavaScript run in each page (see setPatch() in index.html for what it can use).
    Sending a patch with an existing name replaces the old one.

Scenes
------
  scenes/master.json  the frozen starting scene. /api/reset goes back to it. A patch in it can point to a
                      file ("file": "patches/x.js") instead of holding its code. Only changed on purpose, by
                      editing it or with POST /api/master.
  scenes/live.json    the current edit log, rewritten after every edit, so a restarted server picks up where
                      it left off. Delete it (or POST /api/reset) to start again from the master.

Scene HTTP API, e.g. with curl from this machine:
  GET    /api/state            the headset's latest scene state and pose, its performance (frame rate, GPU time)
                               if adb can reach it, recent edits and page errors (?scene=0 leaves out the scene)
  POST   /api/patch?id=NAME    body: the patch's JavaScript; replies with whether it ran on the headset
  DELETE /api/patch?id=NAME    remove a patch
  POST   /api/commands         body: {"commands": [{"name": "add_object", "input": {...}}, ...]}
  POST   /api/reload           reload every page (the edit log is replayed)
  POST   /api/reset            replace the live scene with the master, and reload every page
  POST   /api/master           make the live scene the new master (overwrites scenes/master.json)

Game server
-----------
The game server (game/) handles the other /api/ routes (see game/http_api.py); the audience web app is at
/audience/, the host view at /host/, and the join screen (a QR code for the projector) at /join/. Phones join
over the network, so the server listens on every interface. /control/ puts the live scene and the host view side by side on the laptop; GET /api/links gives pages the
audience URL (with this machine's network address).

To reach phones and the Quest over the internet instead (when the Wi-Fi keeps devices apart), run
`--public-port 8080 --public-url https://<this machine>.<tailnet>.ts.net:8443` and point a tunnel at port 8080:
`tailscale funnel --bg --https=8443 http://127.0.0.1:8080`. Never point a tunnel at the main port: its requests
come from this machine, which the main port trusts with the scene routes; the public door doesn't.

Patches run code in the pages, so /ws, /api/chat and the scene routes above only answer requests from this
machine (which includes the Quest over USB with adb reverse), or ones carrying the host key: an X-Host-Key
header, or ?key= on the WebSocket. Open the scene with #key=HOST_KEY to pass it from the Quest over Wi-Fi.
The host key is WORLDTINDER_HOST_KEY, or a random one printed at startup.
"""

import argparse
import base64
import collections
import functools
import hashlib
import hmac
import http.server
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import struct
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from game.catalog import Catalog
from game.engine import GameEngine
from game.events import EventHub
from game.http_api import GameAPI

ROOT = Path(__file__).resolve().parent
CERT_DIR = ROOT / ".certs"
MASTER = ROOT / "scenes" / "master.json"
LIVE = ROOT / "scenes" / "live.json"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # fixed by the WebSocket spec (RFC 6455)
SCENE_POST_ROUTES = {"/api/chat", "/api/patch", "/api/commands", "/api/reload", "/api/reset", "/api/master"}  # the rest are the game's


class WebSocket:
    """Just enough of RFC 6455 for JSON text messages between this server and a browser."""

    def __init__(self, rfile, wfile, role, device):
        self.rfile, self.wfile, self.role, self.device = rfile, wfile, role, device
        self.connected_at = time.time()
        self.last_pose = 0  # when it last sent a pose; a sleeping Quest stays connected but stops sending
        self.presenting = False  # whether the page is in VR, from its pose messages
        self._send_lock = threading.Lock()

    def recv(self):
        """Return the next text message, or None once the connection is closed."""
        parts = []
        while True:
            head = self.rfile.read(2)
            if len(head) < 2:
                return None
            final, opcode, length = head[0] & 0x80, head[0] & 0x0F, head[1] & 0x7F
            if length == 126:
                length = struct.unpack(">H", self.rfile.read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self.rfile.read(8))[0]
            mask = self.rfile.read(4) if head[1] & 0x80 else None
            data = self.rfile.read(length)
            if len(data) < length:
                return None
            if mask:  # browsers mask what they send: XOR with the repeating 4-byte key
                key = (mask * (length // 4 + 1))[:length]
                data = (int.from_bytes(data, "big") ^ int.from_bytes(key, "big")).to_bytes(length, "big")
            if opcode == 0x8:  # close
                return None
            if opcode == 0x9:  # ping
                self._send(0xA, data)
            elif opcode in (0x0, 0x1):  # continuation, text
                parts.append(data)
                if final:
                    return b"".join(parts).decode()

    def send(self, message):
        """Send a dict as JSON. Returns False if the connection is gone."""
        try:
            self._send(0x1, json.dumps(message).encode())
            return True
        except OSError:
            return False

    def _send(self, opcode, data):
        n = len(data)
        if n < 126:
            header = struct.pack(">BB", 0x80 | opcode, n)
        elif n < 1 << 16:
            header = struct.pack(">BBH", 0x80 | opcode, 126, n)
        else:
            header = struct.pack(">BBQ", 0x80 | opcode, 127, n)
        with self._send_lock:
            self.wfile.write(header + data)


def describe_edit(op):
    if op["kind"] == "patch":
        what = f"patch {op['id']}" + (" removed" if op["code"] is None else "")
    else:
        what = ", ".join(f"{c.get('name')} {c.get('input', {}).get('name', '')}".strip() for c in op["commands"])
    return {"seq": op["seq"], "time": op.get("time"), "by": op.get("by"), "edit": what}


class Hub:
    """The edit log, the open pages, and the latest state reported by the headset."""

    def __init__(self):
        self.lock = threading.RLock()
        self.sockets = []
        self.pose = None  # latest pose message from the headset, plus "received" (time.time())
        self.scene = None  # latest scene state from the headset, plus "received"
        self.perf = None  # latest performance stats from the Quest itself, see watch_headset()
        self.errors = collections.deque(maxlen=30)
        self.pending = {}  # request id -> {"event", "from": the page whose reply counts, "reply"}
        self.load(LIVE if LIVE.exists() else MASTER)

    # --- the edit log ---

    def load(self, path):
        """Start a new session from a scene file: the live cache (to resume) or the master (to reset)."""
        ops = json.loads(path.read_text())["ops"] if path.exists() else []
        with self.lock:
            # A new session id makes open pages reload, so they replay the new log from scratch.
            self.session, self.seq, self.ops = uuid.uuid4().hex[:8], 0, []
            self.source = path.name
            for op in ops:
                op = dict(op)
                if "file" in op:
                    op["code"] = (ROOT / op.pop("file")).read_text()
                self.seq += 1
                self.ops.append({**op, "seq": self.seq, "by": op.get("by", path.stem)})
            self.save()
            self.broadcast({"type": "reload"})

    def save(self):
        """Rewrite scenes/live.json (via a temporary file, so a crash can't leave half of it)."""
        LIVE.parent.mkdir(exist_ok=True)
        tmp = LIVE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"saved": time.strftime("%Y-%m-%d %H:%M:%S"), "ops": self.ops}))
        os.replace(tmp, LIVE)

    def save_master(self):
        with self.lock:
            ops = [{k: v for k, v in o.items() if k != "seq"} for o in self.ops]
        MASTER.write_text(json.dumps({"ops": ops}, indent=2) + "\n")

    def append(self, op, by, rid=None):
        """Add an edit to the log, save it, and send it to every page."""
        with self.lock:  # held while sending too, so every page gets edits in sequence order
            self.seq += 1
            op = {"seq": self.seq, **op, "by": by, "time": time.strftime("%H:%M:%S")}
            if op["kind"] == "patch":  # only the newest version of a patch needs replaying
                self.ops = [o for o in self.ops if not (o["kind"] == "patch" and o["id"] == op["id"])]
            self.ops.append(op)
            self.save()
            self.broadcast({"type": "op", "op": {**op, "rid": rid}})
        return op

    def edit(self, op, by, timeout):
        """Append an edit, then wait for the headset to report how applying it went."""
        target = self.headset()
        if target is None:
            op = self.append(op, by)
            return {"seq": op["seq"], "note": "The headset isn't connected; the edit applies when it is."}
        result = {}
        reply = self.ask(target, lambda rid: result.update(seq=self.append(op, by, rid)["seq"]), timeout)
        if reply is None:
            result["note"] = f"The headset didn't report back within {timeout} s."
        else:
            result["applied"] = {k: v for k, v in reply.items() if k not in ("type", "rid")}
        return result

    # --- pages ---

    def headset(self):
        """The headset page. If several are open: one that's streaming, preferably in VR, else the newest."""
        with self.lock:
            candidates = [s for s in self.sockets if s.role == "headset"]
        now = time.time()
        return max(candidates, key=lambda s: (now - s.last_pose < 2, s.presenting, s.connected_at), default=None)

    def serve(self, ws):
        """Run one page's connection until it closes."""
        with self.lock:  # registering and sending the log together, so no edit is missed in between
            self.sockets.append(ws)
            ws.send({"type": "init", "session": self.session, "ops": self.ops})
            if ws.role == "viewer" and self.scene:
                ws.send({"type": "state", **self.scene})
        try:
            while (text := ws.recv()) is not None:
                try:
                    msg = json.loads(text)
                except ValueError:
                    continue
                self.handle(ws, msg)
        finally:
            with self.lock:
                self.sockets.remove(ws)

    def handle(self, ws, msg):
        kind = msg.get("type")
        if kind in ("pose", "state"):
            if kind == "pose":
                ws.presenting, ws.last_pose = bool(msg.get("presenting")), time.time()
            if ws is not self.headset():
                return
            entry = {**msg, "received": time.time()}
            if kind == "pose":
                self.pose = entry
            else:
                self.scene = entry
            self.relay(msg)
        elif kind == "commands":  # edits made on a page, like the trigger dropping a book
            self.append({"kind": "commands", "commands": msg.get("commands", [])}, by=ws.role)
        elif kind == "reply":
            entry = self.pending.get(msg.get("rid"))
            if entry and entry["from"] is ws:
                entry["reply"] = msg
                entry["event"].set()
        elif kind == "error":
            self.errors.append({"time": time.strftime("%H:%M:%S"), "page": ws.role, "message": msg.get("message")})

    def broadcast(self, message):
        with self.lock:
            for s in self.sockets:
                s.send(message)

    def relay(self, message):
        """Pass a message from the headset on to the viewers."""
        with self.lock:
            viewers = [s for s in self.sockets if s.role == "viewer"]
        for viewer in viewers:
            viewer.send(message)

    def ask(self, ws, send, timeout):
        """Call send(rid) to message a page, then wait for its reply to that rid (None on timeout)."""
        rid = uuid.uuid4().hex[:8]
        entry = self.pending[rid] = {"event": threading.Event(), "from": ws, "reply": None}
        send(rid)
        entry["event"].wait(timeout)
        self.pending.pop(rid, None)
        return entry["reply"]

    # --- what the laptop sees ---

    def state(self, include_scene):
        now = time.time()

        def aged(entry):  # an entry without its bookkeeping, plus how old it is
            if not entry:
                return None
            return {**{k: v for k, v in entry.items() if k not in ("type", "received")}, "age_ms": round((now - entry["received"]) * 1000)}

        headset = self.headset()
        with self.lock:
            viewers = sum(s.role == "viewer" for s in self.sockets)
            patches = [o["id"] for o in self.ops if o["kind"] == "patch" and o["code"] is not None]
            recent = [describe_edit(o) for o in self.ops[-15:]]
        return {
            "session": self.session,
            "seq": self.seq,
            "headset": {
                "connected": headset is not None,
                "device": headset and headset.device,
                "streaming": bool(headset and now - headset.last_pose < 2),
                "in_vr": bool(headset and headset.presenting),
                "performance": aged(self.perf),  # the Quest's own stats, whichever page is the headset
            },
            "viewers": viewers,
            "pose": aged(self.pose),
            "scene": aged(self.scene) if include_scene else None,
            "patches": patches,
            "recent_edits": recent,
            "errors": list(self.errors),
        }

    def chat_scene(self, fallback):
        """The scene state the chat model sees: the headset's, when there is one."""
        if not self.scene:
            return fallback
        return {k: self.scene[k] for k in ("room", "user", "shapes", "animations", "objects") if k in self.scene}


def adb(*args):
    """Run an adb command and return its output ("" if it fails); None if there's no adb on this machine."""
    try:
        return subprocess.run(["adb", *args], capture_output=True, text=True, timeout=10).stdout
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return ""


def adb_devices():
    """Serials of the connected, authorized devices. The same Quest can be listed twice (USB and Wi-Fi)."""
    return [line.split()[0] for line in (adb("devices") or "").splitlines()[1:] if line.endswith("\tdevice")]


def refresh_quest_forward(port, previous_serials):
    """One headset can have two transports, but its reverse listener is shared.

    A listed listener may still belong to a dead Wi-Fi transport. Recreate it when
    transports change; prefer USB while plugged in, then Wi-Fi after unplugging.
    """
    serials = tuple(sorted(adb_devices()))
    if serials:
        serial = next((s for s in serials if ":" not in s), serials[0])
        mapping = f"tcp:{port} tcp:{port}"
        if serials != previous_serials or mapping not in (adb("-s", serial, "reverse", "--list") or ""):
            adb("-s", serial, "reverse", "--remove", f"tcp:{port}")
            adb("-s", serial, "reverse", f"tcp:{port}", f"tcp:{port}")
    return serials


def keep_quest_forwarded(port):
    """Keep the Quest's localhost:PORT forwarded to this server (adb reverse), over USB or Wi-Fi. adb drops the
    forward whenever it re-makes its connection (cable unplugged, Wi-Fi blip, Quest waking up), so check often.
    It also forgets Wi-Fi devices when its own server restarts, so reconnect any it has listed before."""
    if adb("version") is None:
        return
    wifi = set()  # Wi-Fi serials ("ip:port") seen so far, including offline entries
    previous_serials = None
    while True:
        # Repair the working transport before a potentially slow Wi-Fi reconnect.
        previous_serials = refresh_quest_forward(port, previous_serials)
        listed = (adb("devices") or "").splitlines()[1:]
        wifi.update(line.split()[0] for line in listed if line.split() and ":" in line.split()[0])
        for serial in wifi - set(previous_serials):
            adb("connect", serial)
        time.sleep(3)


def watch_headset(hub):
    """Keep the Quest's latest performance stats, which its VR runtime logs once a second, while adb can reach it.
    They describe whatever is in front of the user: our page while it's in VR, otherwise the Quest's home screen."""
    if adb("version") is None:
        return  # no adb on this machine
    while True:
        serials = adb_devices()  # adb refuses to pick between two, so name one
        if not serials:
            time.sleep(5)
            continue
        log = subprocess.Popen(["adb", "-s", serials[0], "logcat", "-T", "1", "-s", "VrApi:I"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        for line in log.stdout:
            if "FPS=" not in line:
                continue
            # e.g. "... VrApi : FPS=90/90,Prd=20ms,...,App=0.64ms,...,GPU%=0.16,CPU%=0.20(W0.21),..."
            fields = dict(f.split("=", 1) for f in line.split(": ", 1)[-1].strip().split(",") if "=" in f)
            hub.perf = {
                "received": time.time(),
                "fps": fields.get("FPS"),  # achieved/target
                "app_gpu_time": fields.get("App"),  # GPU time per frame; the budget is 1000/target fps ms
                "gpu_utilization": fields.get("GPU%"),  # 0-1, at the current clock level
                "cpu_utilization": fields.get("CPU%"),
                "clock_levels_cpu_gpu": fields.get("CPU4/GPU"),  # raised by the Quest under load
                "stale_frames": fields.get("Stale"),  # frames that weren't ready in time, in the last second
                "temperature": fields.get("Temp"),
            }
        log.wait()
        time.sleep(5)  # the headset went away; try again


hub = None  # created in main()


class Handler(http.server.SimpleHTTPRequestHandler):
    game_api = None  # set in main()
    host_key = None  # set in main()
    links = {}  # set in main()
    trust_local = True  # requests from this machine get the scene routes without the host key
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".gltf": "model/gltf+json",
        ".glb": "model/gltf-binary",
        ".bin": "application/octet-stream",
        ".js": "text/javascript",
    }

    def log_message(self, format, *args):
        # EventSource and the WebSocket put the audience token or host key in the URL; keep them out of the log.
        super().log_message(format, *(re.sub(r"\b(token|key)=[^&\s\"]+", r"\1=…", a) if isinstance(a, str) else a for a in args))

    def end_headers(self):
        # Browsers may keep files, but must check they're unchanged before using them (a cheap 304 Not Modified,
        # via Last-Modified), so edits show up on reload and the 27 MB room isn't downloaded again every time.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def same_origin(self):
        """Refuse pages on other sites: WebSockets and simple POSTs aren't covered by CORS, and patches run code."""
        origin = self.headers.get("Origin")
        return origin is None or urlparse(origin).netloc == self.headers.get("Host")

    def scene_allowed(self, url):
        """Scene routes run code in the pages: only this machine (and the Quest over adb reverse), or the host key."""
        if self.trust_local and self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return True
        key = self.headers.get("X-Host-Key") or parse_qs(url.query).get("key", [""])[0]
        return bool(self.host_key) and hmac.compare_digest(key.encode(), self.host_key.encode())

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/ws", "/api/state") and not self.scene_allowed(url):
            return self.send_error(403, "Host key required")
        if url.path == "/ws":
            self.open_websocket(url)
        elif url.path == "/api/state":
            self.send_json(hub.state(include_scene=parse_qs(url.query).get("scene") != ["0"]))
        elif url.path == "/api/links":
            self.send_json(self.links)
        elif url.path.startswith("/api/"):
            if not self.game_api.handle(self, "GET"):
                self.send_error(404)
        else:
            super().do_GET()

    def do_POST(self):
        url = urlparse(self.path)
        if url.path not in SCENE_POST_ROUTES:
            if not self.game_api.handle(self, "POST"):
                self.send_error(404)
            return
        if not self.same_origin():
            return self.send_error(403, "Cross-origin request refused")
        if not self.scene_allowed(url):
            return self.send_error(403, "Host key required")
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if url.path == "/api/chat":
            self.chat(body)
        elif url.path == "/api/patch":
            self.patch(url, body.decode())
        elif url.path == "/api/commands":
            try:
                commands = json.loads(body)["commands"]
            except (ValueError, KeyError, TypeError):
                return self.send_json({"error": 'Send {"commands": [...]}'}, 400)
            self.send_json(hub.edit({"kind": "commands", "commands": commands}, by="api", timeout=3))
        elif url.path == "/api/reload":
            hub.broadcast({"type": "reload"})
            self.send_json({"ok": True})
        elif url.path == "/api/reset":
            hub.load(MASTER)
            self.send_json({"ok": True, "edits": len(hub.ops)})
        elif url.path == "/api/master":
            hub.save_master()
            self.send_json({"ok": True, "saved": str(MASTER.relative_to(ROOT)), "edits": len(hub.ops)})
        else:
            self.send_error(404)

    def do_DELETE(self):
        url = urlparse(self.path)
        if not self.same_origin():
            return self.send_error(403, "Cross-origin request refused")
        if url.path != "/api/patch":
            return self.send_error(404)
        if not self.scene_allowed(url):
            return self.send_error(403, "Host key required")
        self.patch(url, None)

    def patch(self, url, code):
        patch_id = parse_qs(url.query).get("id", [""])[0].strip()
        if not patch_id:
            return self.send_json({"error": "Name the patch with ?id=NAME"}, 400)
        self.send_json(hub.edit({"kind": "patch", "id": patch_id, "code": code}, by="api", timeout=20))

    def chat(self, body):
        try:
            request = json.loads(body)
            # Imported lazily so the viewer still works without the anthropic package or an API key.
            import scene_agent
            result, status = scene_agent.handle_chat(request.get("message", ""), hub.chat_scene(request.get("scene", {}))), 200
            if result["commands"]:
                hub.append({"kind": "commands", "commands": result["commands"]}, by="chat")  # every page applies them
        except ImportError:
            result, status = {"error": "The anthropic package isn't installed (pip install anthropic)."}, 500
        except Exception as err:  # surfaced in the chat box; the server keeps running
            message = f"{type(err).__name__}: {err}"
            if "authentication method" in str(err):
                message = "No Claude API key found. Restart the server with ANTHROPIC_API_KEY set (see serve.py)."
            result, status = {"error": message}, 500
        self.send_json(result, status)

    def open_websocket(self, url):
        key = self.headers.get("Sec-WebSocket-Key")
        if not key or self.headers.get("Upgrade", "").lower() != "websocket" or not self.same_origin():
            return self.send_error(400, "Expected a same-origin WebSocket upgrade")
        self.protocol_version = "HTTP/1.1"  # browsers expect the 101 response as HTTP/1.1
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode())
        self.end_headers()
        role = "headset" if parse_qs(url.query).get("role") == ["headset"] else "viewer"
        device = "Quest" if "OculusBrowser" in self.headers.get("User-Agent", "") else "desktop"
        hub.serve(WebSocket(self.rfile, self.wfile, role, device))
        self.close_connection = True

    def send_json(self, data, status=200):
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class PublicHandler(Handler):
    """The public door (--public-port), for a tunnel such as Tailscale Funnel to point at. A tunnel's requests
    come from this machine, so here they aren't trusted: the scene routes need the host key like any other."""

    trust_local = False


def lan_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))  # no packets are sent; this just picks the outgoing interface
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def wsl_nat():
    """True in WSL2 with NAT networking: this machine's address is private to Windows, not on the Wi-Fi."""
    try:
        if "microsoft" not in Path("/proc/version").read_text().lower():
            return False
    except OSError:
        return False
    try:
        mode = subprocess.run(["wslinfo", "--networking-mode"], capture_output=True, text=True, timeout=5).stdout.strip()
        if mode:
            return mode == "nat"
    except (OSError, subprocess.TimeoutExpired):
        pass  # older WSL without wslinfo
    return ipaddress.ip_address(lan_ip()) in ipaddress.ip_network("172.16.0.0/12")


POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
WINDOWS_PROBE = """
$ip = @((Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -ne $null -and $_.NetAdapter.Status -eq 'Up' }).IPv4Address.IPAddress)
$proxy = netsh interface portproxy show v4tov4 | Out-String
$fw = [bool](Get-NetFirewallRule -DisplayName 'WorldTinder' -ErrorAction SilentlyContinue)
@{ ips = $ip; proxy = $proxy; firewall = $fw } | ConvertTo-Json -Compress
"""


def windows_network():
    """From WSL: Windows' Wi-Fi addresses, its port forwards (listen port -> WSL address), and our firewall rule."""
    try:
        out = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", WINDOWS_PROBE],
                             capture_output=True, text=True, timeout=20).stdout
        info = json.loads(out)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    forwards = {int(m[1]): m[2] for m in re.finditer(r"^\s*\S+\s+(\d+)\s+(\S+)\s+\d+\s*$", info.get("proxy") or "", re.M)}
    return {"ips": [ip for ip in info.get("ips") or [] if ip], "forwards": forwards, "firewall": bool(info.get("firewall"))}


def wsl_forwarding_notes(ports):
    """Work out the Wi-Fi address in WSL NAT mode, and the Windows commands still needed to reach this server."""
    print("WSL2 with NAT networking: asking Windows for its Wi-Fi address (a few seconds)…")
    win = windows_network()
    if not win or not win["ips"]:
        return None, ["Couldn't ask Windows for its address. Pass --join-host <Windows Wi-Fi IP>."]
    wsl_ip, notes, fixes = lan_ip(), [], []
    for port in ports:
        target = win["forwards"].get(port)
        if target == wsl_ip:
            continue
        verb = "add" if target is None else "set"
        fixes.append(f"netsh interface portproxy {verb} v4tov4 listenaddress=0.0.0.0 listenport={port} connectaddress={wsl_ip} connectport={port}")
    if not win["firewall"]:
        fixes.append(f"New-NetFirewallRule -DisplayName WorldTinder -Direction Inbound -Protocol TCP -LocalPort {','.join(map(str, ports))} -Action Allow")
    if fixes:
        notes.append("Phones reach Windows, not WSL. Run this once in an *administrator* PowerShell on Windows")
        notes.append("(the port forward must be redone whenever WSL restarts and gets a new address):")
        notes += [f"    {cmd}" for cmd in fixes]
        notes.append("Or switch WSL to mirrored networking; see \"Running the demo in one room\" in README.md.")
    return win["ips"][0], notes


def ensure_cert(ips):
    """Self-signed certificate for localhost and these addresses, regenerated when they change (new Wi-Fi)."""
    cert, key, names = CERT_DIR / "cert.pem", CERT_DIR / "key.pem", CERT_DIR / "addresses.txt"
    want = ",".join(["DNS:localhost", "IP:127.0.0.1"] + [f"IP:{ip}" for ip in sorted(set(ips))])
    if not (cert.exists() and key.exists() and names.exists() and names.read_text() == want):
        CERT_DIR.mkdir(exist_ok=True)
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365",
                "-keyout", str(key), "-out", str(cert),
                "-subj", "/CN=worldtinder-local",
                "-addext", f"subjectAltName={want}",
            ],
            check=True,
            capture_output=True,
        )
        names.write_text(want)
    return cert, key


def main():
    global hub
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--https", action="store_true", help="also serve HTTPS with a self-signed cert (for the Quest over Wi-Fi)")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (default 8000)")
    parser.add_argument("--https-port", type=int, default=8443, help="HTTPS port (default 8443)")
    parser.add_argument("--join-host", default=os.environ.get("WORLDTINDER_JOIN_HOST"),
                        help="address phones should use, if the detected one is wrong (e.g. several network interfaces)")
    parser.add_argument("--public-port", type=int, default=os.environ.get("WORLDTINDER_PUBLIC_PORT"),
                        help="also listen on 127.0.0.1:PORT as the public door, for a tunnel to point at (e.g. 8080)")
    parser.add_argument("--public-url", default=os.environ.get("WORLDTINDER_PUBLIC_URL"),
                        help="the tunnel's public address, given to phones (e.g. https://mac.tailnet.ts.net:8443)")
    args = parser.parse_args()

    # Phones join the game over the network; the scene routes check scene_allowed() themselves.
    listeners = [("0.0.0.0", args.port, Handler)]
    if args.https:
        listeners.append(("0.0.0.0", args.https_port, Handler))
    if args.public_port:
        listeners.append(("127.0.0.1", int(args.public_port), PublicHandler))
    servers = []
    for address, port, handler in listeners:  # bind first, so a busy port fails before anything slow happens
        try:
            servers.append(http.server.ThreadingHTTPServer((address, port), functools.partial(handler, directory=str(ROOT))))
        except OSError as err:
            raise SystemExit(f"Can't listen on port {port}: {err.strerror}. Is another serve.py still running? "
                             f"Stop it, or pick other ports with --port / --https-port / --public-port.")
    lan_ports = [args.port] + ([args.https_port] if args.https else [])
    notes = []
    join_host = args.join_host
    if not join_host and wsl_nat():
        join_host, notes = wsl_forwarding_notes(lan_ports)
    join_host = join_host or lan_ip()
    public_url = (args.public_url or "").rstrip("/")
    join_url = f"{public_url}/audience/" if public_url else f"http://{join_host}:{args.port}/audience/"

    hub = Hub()
    events = EventHub()
    engine = GameEngine(Catalog.load(), events)
    engine.start()
    host_key = os.environ.get("WORLDTINDER_HOST_KEY") or secrets.token_urlsafe(8)
    Handler.game_api = GameAPI(engine, events, host_key, join_url)
    Handler.host_key = host_key
    Handler.links = {"audience": join_url}

    if args.https:
        cert, key = ensure_cert([join_host, lan_ip()])
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        # Handshake in the request's thread, not in accept(): a client that stalls mid-handshake (or a browser's
        # speculative connection) would otherwise hold up every other HTTPS request.
        servers[1].socket = ctx.wrap_socket(servers[1].socket, server_side=True, do_handshake_on_connect=False)

    local = f"http://localhost:{args.port}"
    if public_url:
        quest = f"{public_url}/#key={host_key}"
    elif args.https:
        quest = f"https://{join_host}:{args.https_port}/#key={host_key}   (accept the certificate warning)"
    else:
        quest = f"{local}/ over USB or Wi-Fi adb (scripts/quest-wireless.sh; kept forwarded), or restart with --https"
    print()
    for label, value in [
        ("Audience (phones)", join_url),
        ("Join screen with QR", f"{local}/join/   (put this on the projector)"),
        ("Host console", f"{local}/host/#key={host_key}"),
        ("Control", f"{local}/control/#key={host_key}   (the live scene and the host view side by side)"),
        ("Quest", quest),
        ("Scene on this laptop", f"{local}/"),
        ("State", f"curl -s localhost:{args.port}/api/state   (game: /api/game/state)"),
    ] + ([("Public door", f"127.0.0.1:{args.public_port} for a tunnel; there the scene routes need the host key")]
         if args.public_port else []):
        print(f"  {label + ':':<22}{value}")
    print(f"  {'Scene:':<22}{len(hub.ops)} edits from scenes/{hub.source}")
    if notes:
        print()
    for line in notes:
        print(f"  {line}")
    print()
    threading.Thread(target=watch_headset, args=(hub,), daemon=True).start()
    threading.Thread(target=keep_quest_forwarded, args=(args.port,), daemon=True).start()

    for server in servers[1:]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()


if __name__ == "__main__":
    main()
