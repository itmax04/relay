"""Local JSON + Server-Sent Events API for the menu-bar app. Binds 127.0.0.1 only.

GET  /api/health                     engine status, LLM chain state
GET  /api/sessions                   active sessions, most recent first
GET  /api/sessions/<id>              one session
GET  /api/quota                      latest Claude and Codex quota readings + "status" (ok/stale/no_reading/no_source)
GET  /api/catalog                    switchable models per agent
GET  /api/advice?limit=&session=     advice, newest first
POST /api/advice/<id>/outcome        {"outcome": "useful" | "wrong" | null, "note": "..."}
GET  /api/config                     user file, effective settings and defaults
POST /api/config                     partial settings → ~/.config/relay/config.json, applied live;
                                     {} re-reads keys (after the app changes the Keychain)
GET  /api/usage?days=7               `relay usage --json` data (cached 2 min)
GET  /api/agents                     relay hooks / status line / skill installed per agent
GET  /api/runs                       dispatcher runs, newest first
GET  /api/runs/<id>                  one run
POST /api/runs/<id>/stop             {} → stop the run (its agent process is killed)
POST /api/runs/<id>/approve          {} → approve the plan or [confirm] step the run waits at
POST /api/runs/<id>/resume           {} → continue a failed/stopped run in a detached dispatcher
GET  /api/events                     SSE: events "session", "advice", "quota", "run"; ": ping" every 15 s

Requests whose Host is not localhost are refused (DNS rebinding), and POST requires a JSON body
(a cross-site form cannot send one without a CORS preflight, which is never answered).
"""
import json
import queue
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from relay import dispatch

LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")


class Hub:
    def __init__(self):
        self.lock = threading.Lock()
        self.clients = []

    def subscribe(self):
        q = queue.Queue(maxsize=200)
        with self.lock:
            self.clients.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)

    def publish(self, event, data):
        with self.lock:
            clients = list(self.clients)
        for q in clients:
            try:
                q.put_nowait((event, data))
            except queue.Full:
                pass  # a stalled client misses events; it re-reads state on reconnect


def make_handler(engine):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _local(self):
            host = self.headers.get("Host") or ""
            host = host[:host.find("]") + 1] if host.startswith("[") else host.split(":")[0]
            if host in LOCAL_HOSTS:
                return True
            self._json(403, {"error": "non-local Host"})
            return False

        def _json(self, code, data):
            body = json.dumps(data, ensure_ascii=False, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._local():
                return
            url = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(url.query)
            path = url.path.rstrip("/")
            if path == "/api/health":
                return self._json(200, engine.health())
            if path == "/api/sessions":
                return self._json(200, engine.sessions())
            m = re.fullmatch(r"/api/sessions/([^/]+)", path)
            if m:
                s = engine.session(urllib.parse.unquote(m.group(1)))
                return self._json(200 if s else 404, s or {"error": "no such session"})
            if path == "/api/quota":
                return self._json(200, engine.quota())
            if path == "/api/catalog":
                return self._json(200, engine.catalog())
            if path == "/api/advice":
                try:
                    limit = min(int((qs.get("limit") or ["100"])[0]), 1000)
                except ValueError:
                    limit = 100
                return self._json(200, engine.log.list(limit, (qs.get("session") or [None])[0]))
            if path == "/api/config":
                return self._json(200, engine.config_view())
            if path == "/api/agents":
                return self._json(200, engine.agents())
            if path == "/api/usage":
                try:
                    days = int((qs.get("days") or ["7"])[0])
                except ValueError:
                    days = 7
                return self._json(200, engine.usage(days))
            if path == "/api/runs":
                return self._json(200, engine.runs())
            m = re.fullmatch(r"/api/runs/([\w-]+)", path)
            if m:
                r = next((x for x in engine.runs() if x["id"] == m.group(1)), None)
                return self._json(200 if r else 404, r or {"error": "no such run"})
            if path == "/api/events":
                return self._events()
            self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._local():
                return
            path = urllib.parse.urlparse(self.path).path.rstrip("/")
            m = re.fullmatch(r"/api/advice/([^/]+)/outcome", path)
            rm = re.fullmatch(r"/api/runs/([\w-]+)/(stop|approve|resume)", path)
            if not m and not rm and path != "/api/config":
                return self._json(404, {"error": "not found"})
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._json(415, {"error": "send application/json"})
            try:
                n = min(int(self.headers.get("Content-Length") or 0), 100000)
                body = json.loads(self.rfile.read(n) or b"{}")
                if rm and rm.group(2) == "resume":
                    ok, msg = dispatch.resume_detached(rm.group(1))
                    return self._json(200 if ok else 409, {"ok": ok, "message": msg})
                if rm:
                    res = engine.run_control(rm.group(1), rm.group(2))
                    return self._json(200 if res["ok"] else 409, res)
                if not m:
                    return self._json(200, engine.update_config(body))
                d = engine.log.set_outcome(m.group(1), body.get("outcome"), body.get("note"))
            except (ValueError, AttributeError) as e:
                return self._json(400, {"error": str(e)})
            if d is None:
                return self._json(404, {"error": "no such advice"})
            engine.hub.publish("advice", d)
            self._json(200, d)

        def _events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            q = engine.hub.subscribe()
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while not engine.stopping.is_set():
                    try:
                        event, data = q.get(timeout=15)
                        payload = json.dumps(data, ensure_ascii=False, default=str)
                        self.wfile.write(f"event: {event}\ndata: {payload}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                engine.hub.unsubscribe(q)

    return Handler


def serve(engine, port):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(engine))
    httpd.daemon_threads = True
    t = threading.Thread(target=httpd.serve_forever, name="relay-api", daemon=True)
    t.start()
    return httpd
