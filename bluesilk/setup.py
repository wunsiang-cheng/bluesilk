"""Setup: the CLI questions and the web settings page both end in apply_settings."""
import getpass, json
from urllib.error import HTTPError

from .state import API, CFG, CONFIG, HOME, MCP, MODEL, http
from .telegram import tg


# --- setup: the CLI questions and the web settings page both end in apply_settings

def parse_int(v):
    return int(v) if str(v).strip() else None


def check_key(v):
    http(f"{API}/key", headers={"Authorization": f"Bearer {v}"})  # /models is public, only /key rejects a bad key


def check_model(slug):
    """The model exists on OpenRouter and can call tools. Returns its context size and whether it reads images."""
    m = next((m for m in http(f"{API}/models")["data"] if m["id"] == slug), None)
    if m is None:
        raise ValueError(f"{slug!r}: no such model on openrouter.ai/models")
    if "tools" not in m.get("supported_parameters", []):
        raise ValueError(f"{slug!r} doesn't support tool calling, which bluesilk needs")
    return m["context_length"], "image" in m.get("architecture", {}).get("input_modalities", [])


def check_bot(token, uid):
    """The bot exists and can reach the user, who must have pressed Start."""
    name = tg("getMe", token=token)["username"]
    try:
        tg("sendChatAction", token=token, chat_id=uid, action="typing")
    except HTTPError as e:
        raise ValueError(f"bot can't reach {uid} (have you pressed Start?): {e}") from e
    return name


def write_config():
    HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    CONFIG.touch(mode=0o600)
    CONFIG.chmod(0o600)
    CONFIG.write_text(json.dumps(CFG))


def settings_view():
    mask = lambda v: v and f"{v[:3]}…{v[-4:]}"
    return {"api_key": mask(CFG.get("api_key", "")), "bot_token": mask(CFG.get("bot_token", "")),
            "model": CFG.get("model", ""), "default_model": MODEL, "user_id": CFG.get("user_id", ""),
            "port": CFG.get("web", {}).get("port", ""), "mcp": MCP.read_text() if MCP.exists() else ""}


def apply_settings(f):
    """Validate a settings form (blank secrets keep their current value), then write config.json and mcp.json."""
    # ponytail: every change restarts bluesilk, a turn in flight is lost; upgrade: apply api_key live
    new = {"api_key": f.get("api_key") or CFG.get("api_key", ""), "model": (f.get("model") or "").strip() or MODEL}
    if browser := {k: v for k, v in CFG.get("browser", {}).items() if k in ("enabled", "headless", "viewport", "executable_path")}:
        new["browser"] = browser  # hand-edited keys survive; the 0.8 visual-mode keys are dropped
    check_key(new["api_key"])
    context, new["vision"] = check_model(new["model"])
    new["compact_at"] = context // 2
    if uid := parse_int(f.get("user_id", "")):
        token = f.get("bot_token") or CFG.get("bot_token", "")
        if not token:
            raise ValueError("Telegram bot token missing")
        check_bot(token, uid)
        new.update(bot_token=token, user_id=uid)
    if port := parse_int(f.get("port", "")):
        new["web"] = {"port": port}
    if not (uid or port):
        raise ValueError("set up Telegram, the web console, or both")
    if (mcp := f.get("mcp")) is not None:  # the CLI doesn't ask: it leaves mcp.json alone
        if mcp.strip():
            json.loads(mcp)
    CFG.clear()
    CFG.update(new)
    write_config()
    if mcp is not None:
        MCP.write_text(mcp) if mcp.strip() else MCP.unlink(missing_ok=True)
    return new


def ask(prompt, check, secret=False):
    while True:
        value = (getpass.getpass if secret else input)(f"{prompt}: ").strip().strip("\"'")
        try:
            check(value)
            return value
        except Exception as e:
            print(f"  ✗ {e}")


def setup():
    if CONFIG.exists():
        CFG.update(json.loads(CONFIG.read_text()))
    print("bluesilk: an OpenRouter key and model, then Telegram and/or the web console\n")
    f = {"api_key": ask("OpenRouter API key (openrouter.ai/keys)", check_key, secret=True)}
    print("  ✓ OpenRouter key works")

    def model_ok(v):
        context, vision = check_model(v or MODEL)
        print(f"  ✓ {v or MODEL}: {context // 1000}k context, {'reads images' if vision else 'text only (no photos or screenshots)'}")
    f["model"] = ask(f"Model, an openrouter.ai/models slug that supports tools; empty for {MODEL}", model_ok)
    f["user_id"] = ask("Your Telegram user ID (ask @userinfobot, then press Start on your bot); empty to skip Telegram", parse_int)
    if uid := parse_int(f["user_id"]):
        f["bot_token"] = ask("Telegram bot token (from @BotFather)",
                             lambda v: print(f"  ✓ bot @{check_bot(v, uid)} can reach you"), secret=True)
    f["port"] = ask("Web console port (it listens on 127.0.0.1 only); empty to skip the web console", parse_int)
    cfg = apply_settings(f)
    if web := cfg.get("web"):
        print(f"\nWeb console: http://127.0.0.1:{web['port']}/")
    print(f"\nSaved to {CONFIG}. Starting...\n")
