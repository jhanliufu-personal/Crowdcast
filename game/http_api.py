"""HTTP routes for the game: small JSON endpoints plus one Server-Sent Events stream per client.

serve.py hands every /api/ request except /api/chat to GameAPI.handle().

    GET  /api/catalog                  building blocks for the web app's buttons
    GET  /api/state                    game state snapshot
    POST /api/join          {name}     → {token, member, requests, cooldown_s}
    GET  /api/me            (token)    → {member, requests, cooldown_s}
    POST /api/spells        {parts}    (token) → the queued request
    GET  /api/events?role=audience&token=… | role=headset | role=spectator | role=host&key=…   (SSE)
    POST /api/headset/event {type, spell_id, target_member_id?}   (host key)
    POST /api/admin/start | /api/admin/reset                      (host key)
    GET  /api/admin/view                                          (host key) roster and queue

Audience calls carry their token in an X-Token header (or ?token= for EventSource, which can't
set headers). Host and headset calls carry X-Host-Key (or ?key= for EventSource).
"""

import hmac
import json
import queue
from urllib.parse import parse_qs, urlsplit

from .engine import GameError
from .events import ROLES
from .spell_request import RequestError

MAX_BODY = 16 * 1024
KEEPALIVE_S = 15


class GameAPI:
    def __init__(self, engine, hub, host_key):
        self.engine = engine
        self.hub = hub
        self.host_key = host_key
        self.routes = {
            ("GET", "/api/catalog"): self.catalog,
            ("GET", "/api/state"): self.state,
            ("POST", "/api/join"): self.join,
            ("GET", "/api/me"): self.me,
            ("POST", "/api/spells"): self.spells,
            ("GET", "/api/events"): self.events,
            ("POST", "/api/headset/event"): self.headset_event,
            ("POST", "/api/admin/start"): self.admin_start,
            ("POST", "/api/admin/reset"): self.admin_reset,
            ("GET", "/api/admin/view"): self.admin_view,
        }

    def handle(self, h, method):
        """Serve the request if it's a game route; returns False if it isn't one."""
        url = urlsplit(h.path)
        route = self.routes.get((method, url.path))
        if route is None:
            return False
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            route(h, query)
        except (GameError, RequestError) as err:
            self._json(h, getattr(err, "status", 400), {"error": str(err), **getattr(err, "extra", {})})
        return True

    # ---- routes --------------------------------------------------------------------------------

    def catalog(self, h, q):
        self._json(h, 200, self.engine.catalog.data)

    def state(self, h, q):
        self._json(h, 200, self.engine.state())

    def join(self, h, q):
        member = self.engine.join(self._body(h).get("name"))
        self._json(h, 200, {"token": member.token, **self.engine.member_view(member)})

    def me(self, h, q):
        self._json(h, 200, self.engine.member_view(self._member(h, q)))

    def spells(self, h, q):
        member = self._member(h, q)
        request = self.engine.submit(member, self._body(h).get("parts"))
        self._json(h, 200, {"request": request.to_public(), **self.engine.member_view(member)})

    def headset_event(self, h, q):
        self._require_host(h, q)
        body = self._body(h)
        self._json(h, 200, self.engine.report(body.get("type"), body.get("spell_id"), body.get("target_member_id")))

    def admin_start(self, h, q):
        self._require_host(h, q)
        self.engine.start_round()
        self._json(h, 200, self.engine.state())

    def admin_reset(self, h, q):
        self._require_host(h, q)
        self.engine.reset()
        self._json(h, 200, self.engine.state())

    def admin_view(self, h, q):
        self._require_host(h, q)
        self._json(h, 200, self.engine.host_view())

    def events(self, h, q):
        role = q.get("role", "audience")
        if role not in ROLES:
            raise GameError(f"Unknown role {role!r}.")
        member = self._member(h, q) if role == "audience" else None
        if role == "host":
            self._require_host(h, q)
        sub = self.hub.subscribe(role, member and member.id)  # before the snapshot, so nothing is missed
        if member:
            self.engine.mark_host_dirty()  # they're online now
        try:
            h.send_response(200)
            h.send_header("Content-Type", "text/event-stream")
            h.send_header("X-Accel-Buffering", "no")
            h.end_headers()
            self._sse(h, "game:state", json.dumps(self.engine.state()))
            if member:
                self._sse(h, "me", json.dumps(self.engine.member_view(member)))
            if role == "host":
                self._sse(h, "host:view", json.dumps(self.engine.host_view()))
            while not sub.closed and not self.engine.stopped:
                try:
                    event, payload = sub.get(timeout=KEEPALIVE_S)
                except queue.Empty:
                    h.wfile.write(b": keepalive\n\n")
                    h.wfile.flush()
                    continue
                self._sse(h, event, payload)
        except OSError:
            pass  # the client went away
        finally:
            self.hub.unsubscribe(sub)
            if member:
                self.engine.mark_host_dirty()  # possibly offline now
            h.close_connection = True

    # ---- helpers -------------------------------------------------------------------------------

    def _member(self, h, q):
        token = h.headers.get("X-Token") or q.get("token")
        member = token and self.engine.member_by_token(token)
        if not member:
            raise GameError("Unknown player. Join again.", 401)
        return member

    def _require_host(self, h, q):
        key = h.headers.get("X-Host-Key") or q.get("key", "")
        if not hmac.compare_digest(key.encode(), self.host_key.encode()):
            raise GameError("Host key required.", 403)

    def _body(self, h):
        length = int(h.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise GameError("Request too large.", 413)
        try:
            body = json.loads(h.rfile.read(length) or b"{}")
        except (ValueError, UnicodeDecodeError):
            raise GameError("Body must be JSON.")
        if not isinstance(body, dict):
            raise GameError("Body must be a JSON object.")
        return body

    @staticmethod
    def _json(h, status, data):
        payload = json.dumps(data).encode()
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(payload)))
        h.end_headers()
        h.wfile.write(payload)

    @staticmethod
    def _sse(h, event, payload):
        h.wfile.write(f"event: {event}\ndata: {payload}\n\n".encode())
        h.wfile.flush()
