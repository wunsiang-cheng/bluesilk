"""Constants, paths, the process-wide registries and the small helpers everything else shares."""
import json, os, queue, threading, time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

MODEL = "deepseek/deepseek-v4.1-flash"  # the default; setup stores the chosen model, its compact_at and vision in config
API = "https://openrouter.ai/api/v1"
COMPACT_AT = 500_000  # for a config without compact_at: 50% of the default model's 1M context
SHELL_TIMEOUT = 600
FOREGROUND = 30  # seconds before a running command moves to the background and frees the chat
DREAM_EVERY = 24 * 3600
DREAM_IDLE = 30 * 60
IMAGE_SIGS = ((b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG", "image/png"), (b"GIF8", "image/gif"))  # WebP: see as_image
MCP_TIMEOUT = 120  # seconds to wait for an MCP server to answer
PROTOCOL = "2025-06-18"  # the MCP version we speak

HOME = Path(os.environ.get("BLUESILK_HOME", Path.home() / ".bluesilk"))
CONFIG, STATE_FILE, HISTORY = HOME / "config.json", HOME / "state.json", HOME / "history.jsonl"
MCP = HOME / "mcp.json"  # {"mcpServers": {"<name>": {"command": ..., "args": [...], "env": {}} or {"url": ..., "token": ..., "headers": {}}}}

CFG, STATE = {}, {}  # STATE: the dream's bookkeeping; the conversation lives in session.json
SERVERS = {}  # tool name -> (Server, its name on that server)
HISTORY_LOCK = threading.Lock()
LAST = {"active": time.time()}
BROWSER = None
DESKTOP = None


class Session:
    """The conversation: its queue, worker, context, stop button and live draft. Two exist: the user's and the dream's."""

    def __init__(self, name="you", dream=False):
        self.name, self.dream, self.file = name, dream, HOME / "session.json"
        self.q, self.stop = queue.Queue(), threading.Event()  # q: the user's messages and background command results
        self.busy = False
        self.draft_id, self.draft_text, self.tokens = 0, "", 0
        self.note, self.jobs = "", 0  # note: what the session is doing between turns (compacting); jobs: background commands
        self.summary, self.messages = "", []
        self.web = False  # where the latest message came from: the web console (True) or Telegram; replies go back there
        self.subs, self.files = [], {}  # subs: a queue per open console tab; files: id -> path the agent sent

    def push(self, kind, text):
        for q in list(self.subs):
            q.put((kind, text))

    def status(self):
        """The console's status line: the running turn, else what keeps the agent busy in the background."""
        if self.draft_id:
            return self.draft_text or "…"
        return self.note or DREAM.note or (f"{self.jobs} JOB{'S' * (self.jobs > 1)} IN BACKGROUND" if self.jobs else "")


SESSION = Session()  # load() names it after the user
DREAM = Session("nobody: this is the periodic background reflection", dream=True)  # not recorded; send reaches the user


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def quiet(f, *a, **k):
    try:
        return f(*a, **k)
    except Exception as e:
        log("ignored:", e)


def http(url, payload=None, data=None, headers=None, timeout=60):
    if payload is not None:
        data = json.dumps(payload).encode()
    try:
        with urlopen(Request(url, data, {"Content-Type": "application/json", **(headers or {})}), timeout=timeout) as r:
            return json.load(r)
    except HTTPError as e:
        e.msg = e.read().decode(errors="replace")[:500]  # surface the API's reason in str(e)
        raise


def save(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    tmp.replace(path)
