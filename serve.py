"""Serve the WebXR preview (index.html + scene files) locally, and keep every open page in sync.

    python serve.py            # http://localhost:8000 — for previewing on the Mac, and the Quest over USB
    python serve.py --https    # https://<your-mac-ip>:8443 — for opening on the Quest over Wi-Fi

WebXR only runs in a "secure context": localhost, or HTTPS. The Mac browser is fine
over plain HTTP on localhost. The Quest browser either needs HTTPS (a self-signed
certificate is generated into .certs/ on first use; accept the warning in the
headset), or a USB cable plus `adb reverse tcp:8000 tcp:8000` so that the Quest's
http://localhost:8000 reaches this server.

The chat box posts to /api/chat, handled by scene_agent.py with the Claude API. Set your key in
the shell before starting the server (export ANTHROPIC_API_KEY=...); it stays on this machine.
Optional: WORLDTINDER_MODEL (default claude-opus-5-5) and WORLDTINDER_EFFORT (default low).

Live sync
---------
Every page opens a WebSocket to /ws. The page someone is using (the headset) is the "player": it streams its
head and controller poses here about 30 times a second, and answers requests for a snapshot of its live scene.
http://localhost:8000/?mirror is a "mirror": it shows the same scene from the player's head.

Scene edits are kept here as an ordered log and sent to every page, which applies them without reloading;
pages replay the log when they load. An edit is either
  - commands: the add_object / update_object / remove_object calls the chat box uses, or
  - a patch: a named snippet of JavaScript run in the page (see setPatch() in index.html for what it can use).
    Sending a patch with an existing name replaces the old one.
The log lasts until the server restarts or is reset.

HTTP API, e.g. with curl from this machine:
  GET    /api/state            latest pose, live scene snapshot from the player, patch names, recent page errors
                               (?scene=0 skips the snapshot)
  POST   /api/patch?id=NAME    body: the patch's JavaScript; replies with whether it ran on the player
  DELETE /api/patch?id=NAME    remove a patch
  POST   /api/commands         body: {"commands": [{"name": "add_object", "input": {...}}, ...]}
  POST   /api/reload           reload every page (edits are replayed)
  POST   /api/reset            drop all edits, and reload every page

Patches run code in the pages, so the server only listens on this machine, except with --https, where anyone
on your network can reach it.
"""

import argparse
import base64
import collections
import functools
import hashlib
import http.server
import json
import socket
import ssl
import struct
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
CERT_DIR = ROOT / ".certs"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # fixed by the WebSocket spec (RFC 6455)


class WebSocket:
    """Just enough of RFC 6455 for JSON text messages between this server and a browser."""

    def __init__(self, rfile, wfile, role):
        self.rfile, self.wfile, self.role = rfile, wfile, role
        self.connected_at = time.time()
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


class Hub:
    """The edit log, the open pages, and the latest pose from the player."""

    def __init__(self):
        self.lock = threading.RLock()
        self.session = uuid.uuid4().hex[:8]  # changes on restart and reset, so pages know their edits are stale
        self.seq = 0
        self.ops = []  # {"seq", "kind": "commands", "commands"} or {"seq", "kind": "patch", "id", "code"}
        self.sockets = []
        self.pose = None
        self.errors = collections.deque(maxlen=30)
        self.pending = {}  # request id -> {"event", "from": the page whose reply counts, "reply"}

    def player(self):
        """The page whose state counts: a player in VR, else the newest player, else the newest page."""
        with self.lock:
            candidates = [s for s in self.sockets if s.role == "player"] or list(self.sockets)
        return max(candidates, key=lambda s: (s.presenting, s.connected_at), default=None)

    def serve(self, ws):
        """Run one page's connection until it closes."""
        with self.lock:  # registering and sending the log together, so no edit is missed in between
            self.sockets.append(ws)
            ws.send({"type": "init", "session": self.session, "ops": self.ops})
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
        if kind == "pose":
            ws.presenting = bool(msg.get("presenting"))
            if ws is self.player():
                self.pose = {**msg, "received": time.time()}
                with self.lock:
                    mirrors = [s for s in self.sockets if s.role == "mirror"]
                for mirror in mirrors:
                    mirror.send(msg)
        elif kind == "commands":  # edits made on a page, like the trigger dropping a book
            self.append({"kind": "commands", "commands": msg.get("commands", [])})
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

    def append(self, op):
        """Add an edit to the log and send it to every page."""
        with self.lock:  # held while sending too, so every page gets edits in sequence order
            self.seq += 1
            op = {"seq": self.seq, **op}
            if op["kind"] == "patch":  # only the newest version of a patch needs replaying
                self.ops = [o for o in self.ops if not (o["kind"] == "patch" and o["id"] == op["id"])]
            self.ops.append(op)
            self.broadcast({"type": "op", "op": op})
        return op

    def ask(self, ws, send, timeout):
        """Call send(rid) to message a page, then wait for its reply to that rid (None on timeout)."""
        rid = uuid.uuid4().hex[:8]
        entry = self.pending[rid] = {"event": threading.Event(), "from": ws, "reply": None}
        send(rid)
        entry["event"].wait(timeout)
        self.pending.pop(rid, None)
        return entry["reply"]

    def edit(self, op, timeout):
        """Append an edit, then wait for the player to report how applying it went."""
        target = self.player()
        if target is None:
            op = self.append(op)
            return {"seq": op["seq"], "note": "No page is open; the edit applies when one loads."}
        result = {}
        reply = self.ask(target, lambda rid: result.update(seq=self.append({**op, "rid": rid})["seq"]), timeout)
        if reply is None:
            result["note"] = f"The player didn't report back within {timeout} s."
        else:
            result["applied"] = {k: v for k, v in reply.items() if k not in ("type", "rid")}
        return result

    def snapshot(self):
        target = self.player()
        if target is None:
            return None
        reply = self.ask(target, lambda rid: target.send({"type": "request", "what": "snapshot", "rid": rid}), 2)
        return reply and reply.get("data")

    def state(self, include_scene):
        pose = self.pose and {k: v for k, v in self.pose.items() if k not in ("type", "received")}
        if pose:
            pose["age_ms"] = round((time.time() - self.pose["received"]) * 1000)
        with self.lock:
            pages = [{"role": s.role, "in_vr": s.presenting, "connected_s": round(time.time() - s.connected_at)} for s in self.sockets]
            patches = [o["id"] for o in self.ops if o["kind"] == "patch" and o["code"] is not None]
        return {
            "session": self.session,
            "seq": self.seq,
            "pages": pages,
            "pose": pose,
            "patches": patches,
            "scene": self.snapshot() if include_scene else None,
            "errors": list(self.errors),
        }

    def reset(self):
        with self.lock:
            self.session, self.seq, self.ops = uuid.uuid4().hex[:8], 0, []
            self.errors.clear()
            self.broadcast({"type": "reload"})


hub = Hub()


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".gltf": "model/gltf+json",
        ".glb": "model/gltf-binary",
        ".bin": "application/octet-stream",
        ".js": "text/javascript",
    }

    def end_headers(self):
        # Always serve fresh files so edits show up on reload.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def same_origin(self):
        """Refuse pages on other sites: WebSockets and simple POSTs aren't covered by CORS, and patches run code."""
        origin = self.headers.get("Origin")
        return origin is None or urlparse(origin).netloc == self.headers.get("Host")

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/ws":
            self.open_websocket(url)
        elif url.path == "/api/state":
            self.send_json(hub.state(include_scene=parse_qs(url.query).get("scene") != ["0"]))
        else:
            super().do_GET()

    def do_POST(self):
        url = urlparse(self.path)
        if not self.same_origin():
            return self.send_error(403, "Cross-origin request refused")
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
            self.send_json(hub.edit({"kind": "commands", "commands": commands}, timeout=3))
        elif url.path == "/api/reload":
            hub.broadcast({"type": "reload"})
            self.send_json({"ok": True})
        elif url.path == "/api/reset":
            hub.reset()
            self.send_json({"ok": True})
        else:
            self.send_error(404)

    def do_DELETE(self):
        url = urlparse(self.path)
        if not self.same_origin():
            return self.send_error(403, "Cross-origin request refused")
        if url.path != "/api/patch":
            return self.send_error(404)
        self.patch(url, None)

    def patch(self, url, code):
        patch_id = parse_qs(url.query).get("id", [""])[0].strip()
        if not patch_id:
            return self.send_json({"error": "Name the patch with ?id=NAME"}, 400)
        self.send_json(hub.edit({"kind": "patch", "id": patch_id, "code": code}, timeout=20))

    def chat(self, body):
        try:
            request = json.loads(body)
            # Imported lazily so the viewer still works without the anthropic package or an API key.
            import scene_agent
            result, status = scene_agent.handle_chat(request.get("message", ""), request.get("scene", {})), 200
            if result["commands"]:
                hub.append({"kind": "commands", "commands": result["commands"]})  # every page applies them
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
        role = "mirror" if parse_qs(url.query).get("role") == ["mirror"] else "player"
        hub.serve(WebSocket(self.rfile, self.wfile, role))
        self.close_connection = True

    def send_json(self, data, status=200):
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def lan_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))  # no packets are sent; this just picks the outgoing interface
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def ensure_cert(ip):
    cert, key = CERT_DIR / "cert.pem", CERT_DIR / "key.pem"
    if not (cert.exists() and key.exists()):
        CERT_DIR.mkdir(exist_ok=True)
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365",
                "-keyout", str(key), "-out", str(cert),
                "-subj", "/CN=worldtinder-local",
                "-addext", f"subjectAltName=DNS:localhost,IP:127.0.0.1,IP:{ip}",
            ],
            check=True,
            capture_output=True,
        )
    return cert, key


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--https", action="store_true", help="serve over HTTPS with a self-signed cert (for the Quest over Wi-Fi)")
    parser.add_argument("--port", type=int, help="port (default 8000, or 8443 with --https)")
    args = parser.parse_args()

    port = args.port or (8443 if args.https else 8000)
    ip = lan_ip()
    handler = functools.partial(Handler, directory=str(ROOT))
    # Over USB, adb reverse delivers the Quest's requests to localhost, so plain HTTP only needs to listen locally.
    server = http.server.ThreadingHTTPServer(("0.0.0.0" if args.https else "127.0.0.1", port), handler)

    if args.https:
        cert, key = ensure_cert(ip)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        print(f"Mac:   https://localhost:{port}   (mirror of the headset: https://localhost:{port}/?mirror)")
        print(f"Quest: https://{ip}:{port}   (same Wi-Fi; accept the certificate warning)")
    else:
        print(f"Mac:   http://localhost:{port}   (mirror of the headset: http://localhost:{port}/?mirror)")
        print(f"Quest: plug in USB, run `adb reverse tcp:{port} tcp:{port}`, then open http://localhost:{port}")
        print("       (or restart with --https to use Wi-Fi)")
    print(f"State: curl -s localhost:{port}/api/state")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
