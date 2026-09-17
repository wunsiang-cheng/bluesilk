"""Setup: the CLI questions and the web settings page both end in apply_settings."""
import getpass, hashlib, hmac, json, re, secrets
from urllib.error import HTTPError

from . import state
from .state import API, CFG, CONFIG, HOME, MCP, MODEL, http
from .telegram import tg


# --- setup: the CLI questions and the web settings page both end in apply_settings

def parse_ids(v):
    return [int(x) for x in v.replace(",", " ").split()]


def parse_names(v):
    names = v.replace(",", " ").split()
    for n in names:
        if not re.fullmatch(r"[\w.-]+", n):
            raise ValueError(f"{n!r}: letters, digits, . _ - only")
    return names


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


def check_bot(token, ids):
    """The bot exists and can reach every member: each of them must have pressed Start."""
    name = tg("getMe", token=token)["username"]
    for uid in ids:
        try:
            tg("sendChatAction", token=token, chat_id=uid, action="typing")
        except HTTPError as e:
            raise ValueError(f"bot can't reach {uid} (have they pressed Start?): {e}") from e
    return name


def hash_password(pw):
    salt = secrets.token_bytes(16)
    return f"scrypt${salt.hex()}${hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1).hex()}"


def check_password(pw, stored):
    _, salt, digest = stored.split("$")
    return hmac.compare_digest(hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex(), digest)


def write_config():
    HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    CONFIG.touch(mode=0o600)
    CONFIG.chmod(0o600)
    CONFIG.write_text(json.dumps(CFG))


def settings_view():
    mask = lambda v: v and f"{v[:3]}…{v[-4:]}"
    web = CFG.get("web", {})
    return {"setup": state.SETUP, "api_key": mask(CFG.get("api_key", "")), "bot_token": mask(CFG.get("bot_token", "")),
            "model": CFG.get("model", ""), "default_model": MODEL,
            "user_ids": CFG.get("user_ids", []), "host": web.get("host", "127.0.0.1"), "port": web.get("port", 8321),
            "members": {n: h is not None for n, h in web.get("members", {}).items()}, "mcp": MCP.read_text() if MCP.exists() else ""}


def apply_settings(f):
    """Validate a settings form (blank secrets keep their current value), then write config.json and mcp.json."""
    # ponytail: every change restarts bluesilk, a turn in flight is lost; upgrade: apply api_key and members live
    new = {"api_key": f.get("api_key") or CFG.get("api_key", ""), "model": (f.get("model") or "").strip() or MODEL}
    if browser := {k: v for k, v in CFG.get("browser", {}).items() if k in ("enabled", "viewport", "executable_path")}:
        new["browser"] = browser  # hand-edited keys survive; the 0.8 visual-mode keys are dropped
    check_key(new["api_key"])
    context, new["vision"] = check_model(new["model"])
    new["compact_at"] = context // 2
    if ids := parse_ids(f.get("user_ids", "")):
        token = f.get("bot_token") or CFG.get("bot_token", "")
        if not token:
            raise ValueError("Telegram bot token missing")
        check_bot(token, ids)
        new.update(bot_token=token, user_ids=ids)
    if names := parse_names(f.get("members", "")):
        old, reset = CFG.get("web", {}).get("members", {}), parse_names(f.get("reset", ""))  # members keep their password
        new["web"] = {"host": f.get("host") or "127.0.0.1", "port": int(f.get("port") or 8321),
                      "members": {n: None if n in reset else old.get(n) for n in names}}
    if not (ids or names):
        raise ValueError("set up Telegram members, web console members, or both")
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
    print("bluesilk setup: an OpenRouter key and model, then Telegram members and/or web console members\n")
    f = {"api_key": ask("OpenRouter API key (openrouter.ai/keys)", check_key, secret=True)}
    print("  ✓ OpenRouter key works")

    def model_ok(v):
        context, vision = check_model(v or MODEL)
        print(f"  ✓ {v or MODEL}: {context // 1000}k context, {'reads images' if vision else 'text only (no photos or screenshots)'}")
    f["model"] = ask(f"Model, an openrouter.ai/models slug that supports tools; empty for {MODEL}", model_ok)
    f["user_ids"] = ask("Team members' Telegram user IDs, comma-separated (each asks @userinfobot, then presses Start on your "
                        "bot); empty to skip Telegram", parse_ids)
    if parse_ids(f["user_ids"]):
        ids = parse_ids(f["user_ids"])
        f["bot_token"] = ask("Telegram bot token (from @BotFather)",
                             lambda v: print(f"  ✓ bot @{check_bot(v, ids)} can reach all {len(ids)}"), secret=True)
    f["members"] = ask("Web console members' names, comma-separated; empty to skip the web console", parse_names)
    cfg = apply_settings(f)
    if web := cfg.get("web"):
        print(f"\nWeb console: http://{web['host']}:{web['port']}/ (change host/port on its settings page). Members log in "
              f"with their name; a new member sets their password on the first login.")
    print(f"\nSaved to {CONFIG}. Starting...\n")
