"""Entry points: the Telegram poll loop, serve, and main."""
import subprocess, sys, threading, time
from http.server import ThreadingHTTPServer

from . import state
from .agent import COMMANDS, dreamer, load, receive, worker
from .setup import setup
from .state import CFG, CONFIG, SESSION, log, quiet
from .telegram import heartbeat, tg
from .web import Web


def handle_update(u):
    if stopped := u.get("stopped_message_generation"):
        if stopped["chat"]["id"] == CFG["user_id"]:
            SESSION.stop.set()
        return
    m = u.get("message") or {}
    if m.get("from", {}).get("id") != CFG["user_id"] or m["chat"]["type"] != "private":  # the only trust boundary
        return
    quiet(tg, "setMessageReaction", chat_id=CFG["user_id"], message_id=m["message_id"], reaction=[{"type": "emoji", "emoji": "👀"}])
    receive(SESSION, m)


def telegram():
    log(f"bluesilk running as @{tg('getMe')['username']}, Ctrl+C to stop")
    quiet(tg, "setMyCommands", commands=COMMANDS, scope={"type": "chat", "chat_id": CFG["user_id"]})
    quiet(tg, "sendMessage", chat_id=CFG["user_id"], text="🟢 bluesilk online")
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
    threading.Thread(target=worker, args=(SESSION,), daemon=True).start()
    threading.Thread(target=heartbeat, daemon=True).start()
    threading.Thread(target=dreamer, daemon=True).start()
    if web := CFG.get("web"):  # loopback only: no login, the OS account is the access control
        httpd = ThreadingHTTPServer(("127.0.0.1", web["port"]), Web)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        log(f"web console on http://127.0.0.1:{httpd.server_address[1]}/")
    if CFG.get("user_id"):
        telegram()
    log("bluesilk running, Ctrl+C to stop")
    threading.Event().wait()


def install_browser():
    raise SystemExit(subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"]).returncode)


def main():
    try:
        if sys.argv[1:] == ["browser", "install"]:
            return install_browser()
        if not CONFIG.exists():  # to start over: delete it, or use /settings in the console
            setup()
        serve()
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        if state.BROWSER is not None:
            state.BROWSER.close()
