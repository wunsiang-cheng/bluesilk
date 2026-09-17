"""The web console: one page, events out over SSE, small POSTs in."""
import json, mimetypes, os, queue, sys, threading, time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs

from .agent import browser_view, receive, tool_label
from .setup import apply_settings, settings_view
from .state import HOME, SESSION


# --- web console

def transcript(s):
    """The conversation as (role, text) pairs, for a page that (re)connects; role tool is a call the agent made."""
    out = []
    for m in s.messages:
        if browser_view(m):
            continue
        c = m.get("content")
        if isinstance(c, list):
            c = "\n".join(p.get("text", "[image]") for p in c)
        if m["role"] in ("user", "assistant") and c:
            out.append((m["role"], c))
        out += [("tool", tool_label(c)) for c in m.get("tool_calls") or []]
    return out


PAGE = Path(__file__).with_name("console.html").read_text()  # the whole console: no build step, no CDN, works offline


class Web(BaseHTTPRequestHandler):
    """The web console: one page, events out over SSE, small POSTs in. No login: it listens on 127.0.0.1 only, so
    whoever reaches it is already logged in to this OS account."""

    def log_message(self, *a):
        pass

    def reply(self, body, ctype="text/plain; charset=utf-8", code=200, **headers):
        body = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(code)
        for k, v in {"Content-Type": ctype, "Content-Length": str(len(body)), **headers}.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/":
            return self.reply(PAGE, "text/html; charset=utf-8")
        if path == "/events":
            return self.events()
        if path == "/settings":
            return self.reply(settings_view(), "application/json")
        if path.startswith("/file/") and (p := SESSION.files.get(path.split("/")[2])):
            return self.reply(p.read_bytes(), mimetypes.guess_type(p.name)[0] or "application/octet-stream",
                              **{"Content-Disposition": f'inline; filename="{p.name.replace(chr(34), "")}"'})
        self.reply("not found", code=404)

    def do_POST(self):
        path, _, query = self.path.partition("?")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        s = SESSION
        try:
            if path == "/send":
                receive(s, {"message_id": int(time.time()), "text": body.decode(), "via": "web"})
                return self.reply({"queued": s.q.qsize() if s.busy else 0}, "application/json")  # behind a running turn
            elif path == "/upload":
                q = parse_qs(query)
                dest = HOME / "inbox" / f"{int(time.time())}_{Path(q.get('name', ['file'])[0]).name}"
                dest.write_bytes(body)
                receive(s, {"message_id": int(time.time()), "text": q.get("text", [""])[0], "local": [dest], "via": "web"})
                return self.reply({"queued": s.q.qsize() if s.busy else 0}, "application/json")
            elif path == "/stop":
                s.stop.set()
            elif path == "/settings":
                apply_settings(json.loads(body))
                threading.Timer(0.5, restart).start()  # after this reply is out
                return self.reply(settings_view(), "application/json")
            else:
                return self.reply("not found", code=404)
            self.reply("ok")
        except Exception as e:
            self.reply(str(e), code=400)

    def events(self):
        s = SESSION
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        q = queue.Queue()
        s.subs.append(q)
        try:
            self.event("hello", s.name)
            self.event("history", transcript(s))
            self.event("status", s.status())  # a page reloaded mid-turn
            while True:
                try:
                    self.event(*q.get(timeout=20))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")  # keeps proxies and the browser from giving up on a quiet stream
                    self.wfile.flush()
        except OSError:  # tab closed
            pass
        finally:
            s.subs.remove(q)

    def event(self, kind, data):
        self.wfile.write(f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
        self.wfile.flush()


def restart():
    os.execv(sys.executable, [sys.executable, sys.argv[0]])  # config.json exists now, so main() goes straight to serve()
