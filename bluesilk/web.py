"""The web console: one page, events out over SSE, small POSTs in."""
import json, mimetypes, os, queue, secrets, sys, threading, time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs

from . import state
from .agent import browser_view, receive
from .setup import apply_settings, check_password, hash_password, settings_view, write_config
from .state import CFG, HOME, SESSIONS, STATE, STATE_FILE, save


# --- web console

def transcript(s):
    """The member's conversation as (role, text) pairs, for a page that (re)connects."""
    out = []
    for m in s.messages:
        if browser_view(m):
            continue
        c = m.get("content")
        if isinstance(c, list):
            c = "\n".join(p.get("text", "[image]") for p in c)
        if m["role"] in ("user", "assistant") and c:
            out.append((m["role"], c))
    return out


PAGE = Path(__file__).with_name("console.html").read_text()  # the whole console: no build step, no CDN, works offline


class Web(BaseHTTPRequestHandler):
    """The web console: one page, events out over SSE, small POSTs in. Auth is a login cookie issued by /login."""

    def log_message(self, *a):
        pass

    def sid(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return c["s"].value if "s" in c else ""

    def auth(self):
        return SESSIONS.get(STATE.get("logins", {}).get(self.sid()))

    def logins(self, add=None, drop=()):
        """Update the login cookie -> member table; it lives in state.json so a restart keeps everyone logged in."""
        table = STATE.setdefault("logins", {})
        table.update(add or {})
        for k in drop:
            table.pop(k, None)
        if not state.SETUP:  # setup mode: HOME may not exist yet, and its one login is one-time anyway
            save(STATE_FILE, STATE)

    def login(self, f):
        members = CFG.get("web", {}).get("members", {})
        name, password = str(f.get("name", "")), str(f.get("password", ""))
        stored = members.get(name, "")  # "": no such member, None: hasn't picked a password yet, str: hash
        if stored is None:  # first login: the member picks a password, first come first served
            if len(password) < 8:
                return self.reply("password: at least 8 characters", code=400)
            if f.get("confirm") != password:
                return self.reply({"confirm": True}, "application/json")
            members[name] = hash_password(password)
            write_config()
        elif not (stored and check_password(password, stored)):
            time.sleep(1)  # ponytail: per-IP lockout if the console is ever exposed beyond a trusted network
            return self.reply("wrong name or password", code=401)
        sid = secrets.token_urlsafe(24)
        self.logins({sid: name})
        self.reply({"name": name}, "application/json",
                   **{"Set-Cookie": f"s={sid}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000"})

    def password(self, s, f):
        if state.SETUP:
            return self.reply("finish setup first", code=400)  # writing config.json now would leave a half config
        members = CFG["web"]["members"]
        if not check_password(str(f.get("old", "")), members[s.uid]):
            return self.reply("wrong password", code=401)
        if len(new := str(f.get("new", ""))) < 8:
            return self.reply("password: at least 8 characters", code=400)
        members[s.uid] = hash_password(new)
        write_config()
        self.logins(drop=[k for k, v in STATE["logins"].items() if v == s.uid and k != self.sid()])  # other devices
        self.reply("ok")

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
        if not (s := self.auth()):
            return self.reply("unauthorized", code=401)
        if path == "/events":
            return self.events(s)
        if path == "/settings":
            return self.reply(settings_view(), "application/json")
        if path.startswith("/file/") and (p := s.files.get(path.split("/")[2])):
            return self.reply(p.read_bytes(), mimetypes.guess_type(p.name)[0] or "application/octet-stream",
                              **{"Content-Disposition": f'inline; filename="{p.name.replace(chr(34), "")}"'})
        self.reply("not found", code=404)

    def do_POST(self):
        path, _, query = self.path.partition("?")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            if path == "/login":
                return self.login(json.loads(body))
            if not (s := self.auth()):
                return self.reply("unauthorized", code=401)
            if path == "/send":
                receive(s, {"message_id": int(time.time()), "text": body.decode()})
            elif path == "/upload":
                q = parse_qs(query)
                dest = HOME / "inbox" / f"{int(time.time())}_{Path(q.get('name', ['file'])[0]).name}"
                dest.write_bytes(body)
                receive(s, {"message_id": int(time.time()), "text": q.get("text", [""])[0], "local": [dest]})
            elif path == "/stop":
                s.stop.set()
            elif path == "/logout":
                self.logins(drop=[self.sid()])
                return self.reply("ok", **{"Set-Cookie": "s=; Path=/; Max-Age=0"})
            elif path == "/password":
                return self.password(s, json.loads(body))
            elif path == "/settings":
                apply_settings(json.loads(body))
                threading.Timer(0.5, restart).start()  # after this reply is out
                return self.reply(settings_view(), "application/json")
            else:
                return self.reply("not found", code=404)
            self.reply("ok")
        except Exception as e:
            self.reply(str(e), code=400)

    def events(self, s):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        q = queue.Queue()
        s.subs.append(q)
        try:
            self.event("hello", s.name)
            self.event("history", transcript(s))
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
