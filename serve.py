"""Serve the WebXR preview (index.html + scene files) locally.

    python serve.py            # http://localhost:8000 — for previewing on the Mac
    python serve.py --https    # https://<your-mac-ip>:8443 — for opening on the Quest over Wi-Fi

WebXR only runs in a "secure context": localhost, or HTTPS. The Mac browser is fine
over plain HTTP on localhost. The Quest browser either needs HTTPS (a self-signed
certificate is generated into .certs/ on first use; accept the warning in the
headset), or a USB cable plus `adb reverse tcp:8000 tcp:8000` so that the Quest's
http://localhost:8000 reaches this server.

The chat box posts to /api/chat, handled by scene_agent.py with the Claude API. Set your key in
the shell before starting the server (export ANTHROPIC_API_KEY=...); it stays on this machine.
Optional: WORLDTINDER_MODEL (default claude-opus-5-5) and WORLDTINDER_EFFORT (default low).

The game server (game/) handles every other /api/ route; the audience web app is at /audience/.
Host and headset calls need the host key: WORLDTINDER_HOST_KEY, or a random one printed at startup.
"""

import argparse
import functools
import http.server
import json
import os
import re
import secrets
import socket
import ssl
import subprocess
from pathlib import Path

from game.catalog import Catalog
from game.engine import GameEngine
from game.events import EventHub
from game.http_api import GameAPI

ROOT = Path(__file__).resolve().parent
CERT_DIR = ROOT / ".certs"


class Handler(http.server.SimpleHTTPRequestHandler):
    game_api = None  # set in main()
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".gltf": "model/gltf+json",
        ".glb": "model/gltf-binary",
        ".bin": "application/octet-stream",
        ".js": "text/javascript",
    }

    def log_message(self, format, *args):
        # EventSource puts the audience token or host key in the URL; keep them out of the log.
        super().log_message(format, *(re.sub(r"\b(token|key)=[^&\s\"]+", r"\1=…", a) if isinstance(a, str) else a for a in args))

    def end_headers(self):
        # Always serve fresh files so edits show up on reload.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path.startswith("/api/"):
            if not self.game_api.handle(self, "GET"):
                self.send_error(404)
            return
        super().do_GET()

    def do_POST(self):
        if self.path != "/api/chat":
            if not self.game_api.handle(self, "POST"):
                self.send_error(404)
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            # Imported lazily so the viewer still works without the anthropic package or an API key.
            import scene_agent
            result, status = scene_agent.handle_chat(body.get("message", ""), body.get("scene", {})), 200
        except ImportError:
            result, status = {"error": "The anthropic package isn't installed (pip install anthropic)."}, 500
        except Exception as err:  # surfaced in the chat box; the server keeps running
            message = f"{type(err).__name__}: {err}"
            if "authentication method" in str(err):
                message = "No Claude API key found. Restart the server with ANTHROPIC_API_KEY set (see serve.py)."
            result, status = {"error": message}, 500
        payload = json.dumps(result).encode()
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
    hub = EventHub()
    engine = GameEngine(Catalog.load(), hub)
    engine.start()
    host_key = os.environ.get("WORLDTINDER_HOST_KEY") or secrets.token_urlsafe(8)
    Handler.game_api = GameAPI(engine, hub, host_key)
    handler = functools.partial(Handler, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler)

    if args.https:
        cert, key = ensure_cert(ip)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        print(f"Mac:   https://localhost:{port}")
        print(f"Quest: https://{ip}:{port}   (same Wi-Fi; accept the certificate warning)")
    else:
        print(f"Mac:   http://localhost:{port}")
        print(f"Quest: plug in USB, run `adb reverse tcp:{port} tcp:{port}`, then open http://localhost:{port}")
        print("       (or restart with --https to use Wi-Fi)")
    base = f"{'https' if args.https else 'http'}://{ip}:{port}"
    print(f"Audience: {base}/audience/   (phones on the same network{'' if args.https else '; WebXR still needs HTTPS or localhost'})")
    print(f"Host:     {base}/host/#key={host_key}   (roster, live queue, start / reset)")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()


if __name__ == "__main__":
    main()
