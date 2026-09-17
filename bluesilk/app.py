"""Entry points: the Telegram poll loop, serve, web setup, and main."""
import importlib.util, json, secrets, subprocess, sys, threading, time
from http.server import ThreadingHTTPServer

from . import state
from .agent import COMMANDS, dreamer, load, receive, worker
from .setup import hash_password, setup
from .state import CFG, CONFIG, SESSIONS, Session, log, quiet
from .telegram import heartbeat, tg
from .web import Web


def handle_update(u):
    if stopped := u.get("stopped_message_generation"):
        if s := SESSIONS.get(stopped["chat"]["id"]):
            s.stop.set()
        return
    m = u.get("message") or {}
    s = SESSIONS.get(m.get("from", {}).get("id"))
    if not s or m["chat"]["type"] != "private":  # the only trust boundary: team members, in their private chats
        return
    quiet(tg, "setMessageReaction", chat_id=s.uid, message_id=m["message_id"], reaction=[{"type": "emoji", "emoji": "👀"}])
    receive(s, m)


def telegram():
    log(f"bluesilk running as @{tg('getMe')['username']}, Ctrl+C to stop")
    for s in SESSIONS.values():
        if not s.web:
            quiet(tg, "setMyCommands", commands=COMMANDS, scope={"type": "chat", "chat_id": s.uid})
            quiet(tg, "sendMessage", chat_id=s.uid, text="🟢 bluesilk online")
    offset = 0
    while True:
        try:
            for u in tg("getUpdates", offset=offset, timeout=50, allowed_updates=["message", "stopped_message_generation"]):
                offset = u["update_id"] + 1
                handle_update(u)
        except Exception as e:
            log("poll error:", e)
            time.sleep(5)


def serve():
    load()
    for s in SESSIONS.values():
        threading.Thread(target=worker, args=(s,), daemon=True).start()
    threading.Thread(target=heartbeat, daemon=True).start()
    threading.Thread(target=dreamer, daemon=True).start()
    if web := CFG.get("web"):
        httpd = ThreadingHTTPServer((web.get("host", "127.0.0.1"), web.get("port", 8321)), Web)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        log(f"web console on http://{httpd.server_address[0]}:{httpd.server_address[1]}/")
    if CFG.get("bot_token"):
        telegram()
    log(f"bluesilk running for {len(SESSIONS)} member(s), Ctrl+C to stop")
    threading.Event().wait()


def setup_web():
    """Setup in the browser: the console in setup mode, one-time password, its settings page saves and restarts."""
    state.SETUP = True
    if CONFIG.exists():
        CFG.update(json.loads(CONFIG.read_text()))
    password = secrets.token_urlsafe(12)
    CFG.setdefault("web", {}).setdefault("members", {})["setup"] = hash_password(password)  # apply_settings drops it
    SESSIONS["setup"] = Session("setup", "setup", web=True)  # no worker: only /settings does anything
    web = CFG.get("web", {})
    httpd = ThreadingHTTPServer((web.get("host", "127.0.0.1"), web.get("port", 8321)), Web)
    log(f"open http://{httpd.server_address[0]}:{httpd.server_address[1]}/ and log in as setup with password {password} "
        "to set up bluesilk, Ctrl+C to stop")
    httpd.serve_forever()


def install_browser():
    if importlib.util.find_spec("playwright") is None:
        raise SystemExit("Playwright is not installed. First run: uv tool install 'bluesilk[browser]'")
    raise SystemExit(subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"]).returncode)


def main():
    try:
        if sys.argv[1:] == ["browser", "install"]:
            return install_browser()
        if sys.argv[1:] == ["setup", "web"]:
            return setup_web()
        if sys.argv[1:2] == ["setup"] or not CONFIG.exists() or "model" not in json.loads(CONFIG.read_text()):
            setup()  # no model: a config from before 0.7.0, whose key was for DeepSeek
        serve()
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        if state.BROWSER is not None:
            state.BROWSER.close()
