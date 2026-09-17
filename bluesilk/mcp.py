"""MCP clients: stdio servers as subprocesses, HTTP servers over urllib."""
import itertools, json, os, queue, subprocess, threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .state import HOME, MCP, MCP_TIMEOUT, PROTOCOL, SERVERS, log, quiet
from .tools import TOOLS, clip


class Server:
    """One MCP server, spoken to as JSON-RPC over its stdin/stdout."""

    def __init__(self, name, cfg):
        self.name, self.lock, self.wlock, self.q, self.n, self.dead = name, threading.Lock(), threading.Lock(), queue.Queue(), 0, False
        err = (HOME / "jobs" / f"mcp-{name}.log").open("wb")  # servers are chatty on stderr; keep it out of ours
        try:
            self.p = subprocess.Popen([cfg["command"], *cfg.get("args", [])], env={**os.environ, **cfg.get("env", {})},
                                      cwd=Path.home(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                      encoding="utf-8", errors="replace", bufsize=1)  # MCP is UTF-8, whatever the locale says
        finally:
            err.close()  # Popen keeps its own descriptor; the parent does not need this file object
        threading.Thread(target=self.reader, daemon=True).start()

    def reader(self):
        for line in self.p.stdout:
            m = quiet(json.loads, line)
            if not isinstance(m, dict) or "id" not in m:  # a notification, or a stray print
                continue
            if "method" in m:  # the server's own request: ping gets its answer, anything else (sampling, ...) a refusal
                self.send({"jsonrpc": "2.0", "id": m["id"], **({"result": {}} if m["method"] == "ping" else
                           {"error": {"code": -32601, "message": "bluesilk serves no requests"}})})
            else:
                self.q.put(m)
        self.dead = True  # stdout closed: a crash on startup is the common case, don't sit out MCP_TIMEOUT for it
        self.q.put(None)

    def send(self, msg):
        with self.wlock:  # the reader thread answers the server's pings while a request of ours may be going out
            self.p.stdin.write(json.dumps(msg) + "\n")
            self.p.stdin.flush()

    def rpc(self, method, **params):
        # ponytail: one lock per server, one request in flight; upgrade: an id -> waiter map, when that delays a real member
        with self.lock:
            if self.dead:
                raise RuntimeError(f"mcp server {self.name} has exited; its stderr is in jobs/mcp-{self.name}.log")
            self.n += 1
            self.send({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params})
            try:
                while (r := self.q.get(timeout=MCP_TIMEOUT)) and r.get("id") != self.n:
                    pass  # a late answer from a call that already timed out
            except queue.Empty:
                raise RuntimeError(f"mcp server {self.name} did not answer in {MCP_TIMEOUT}s") from None
        if r is None:
            raise RuntimeError(f"mcp server {self.name} died on this call; its stderr is in jobs/mcp-{self.name}.log")
        if "error" in r:
            raise RuntimeError(f"mcp server {self.name}: {r['error'].get('message')}")
        return r["result"]


class HttpServer:
    """One MCP server over Streamable HTTP. Offers what Server does: a name, rpc and send."""

    def __init__(self, name, cfg):
        self.name, self.url, self.lock, self.n, self.session, self.version = name, cfg["url"], threading.Lock(), 0, None, PROTOCOL
        self.headers = {"Authorization": f"Bearer {cfg['token']}"} if cfg.get("token") else {}
        self.headers.update(cfg.get("headers", {}))  # anything else the server wants, e.g. X-Api-Key

    def post(self, msg):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "MCP-Protocol-Version": self.version, **self.headers}
        if self.session:
            h["Mcp-Session-Id"] = self.session
        try:
            resp = urlopen(Request(self.url, json.dumps(msg).encode(), h), timeout=MCP_TIMEOUT)
        except HTTPError as e:
            e.msg = e.read().decode(errors="replace")[:500]  # the server's reason, not just "Bad Request"
            raise
        with resp as r:
            self.session = r.headers.get("Mcp-Session-Id") or self.session
            if "text/event-stream" not in r.headers.get("Content-Type", ""):
                body = r.read()
                return json.loads(body) if body.strip() else None  # a notification gets a 202, or a 200 and nothing
            data = ""  # SSE: our answer, past any progress notification the server streams first
            for raw in itertools.chain(r, [b""]):  # a stream cut right after the last data: line still counts
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("data:"):
                    data += line[5:].removeprefix(" ") + "\n"
                elif not line and data:
                    m, data = quiet(json.loads, data) or {}, ""
                    if m.get("id") == msg.get("id") and ("result" in m or "error" in m):
                        return m

    def send(self, msg):
        self.post(msg)

    def rpc(self, method, **params):
        try:
            return self.request(method, **params)
        except HTTPError as e:
            if e.code not in (400, 404) or not self.session:
                raise
            self.session = None  # the server forgot us (404 per spec, 400 from the SDK's own example), a restart usually
            handshake(self)
            return self.request(method, **params)

    def request(self, method, **params):
        # ponytail: the lock only keeps the session id consistent; upgrade: narrow it to that, network call outside
        with self.lock:
            self.n += 1
            r = self.post({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params})
        if r is None:
            raise RuntimeError(f"mcp server {self.name} sent no answer to {method}")
        if "error" in r:
            raise RuntimeError(f"mcp server {self.name}: {r['error'].get('message')}")
        return r["result"]


def handshake(srv):
    r = srv.rpc("initialize", protocolVersion=PROTOCOL, capabilities={}, clientInfo={"name": "bluesilk", "version": "0"})
    srv.version = r.get("protocolVersion", PROTOCOL)  # what the server settled on names every later HTTP request
    srv.instructions = r.get("instructions") or ""  # how the server wants to be used; goes into the system prompt
    srv.send({"jsonrpc": "2.0", "method": "notifications/initialized"})


def mcp_connect():
    """Start every server in mcp.json and publish its tools. One that won't start is logged and skipped, never fatal."""
    # ponytail: a dead server stays dead until bluesilk restarts, no tools/list_changed; upgrade: reconnect in rpc's dead branch
    for name, cfg in (json.loads(MCP.read_text()).get("mcpServers", {}) if MCP.exists() else {}).items():
        try:
            srv = (HttpServer if "url" in cfg else Server)(name, cfg)
            handshake(srv)
            tools, cursor = [], None
            for _ in range(100):  # a server that keeps handing back the same cursor can't loop us forever
                page = srv.rpc("tools/list", **({"cursor": cursor} if cursor else {}))
                tools += page["tools"]
                if not (cursor := page.get("nextCursor")):
                    break
            for t in tools:
                full = "".join(c if c.isascii() and (c.isalnum() or c in "_-") else "_" for c in f"{name}__{t['name']}")[:64]
                if full in SERVERS:  # two names alike once cleaned up and cut: the second would shadow the first
                    log(f"mcp {name}: tool {t['name']} skipped, its name clashes with {full}")
                    continue
                SERVERS[full] = (srv, t["name"])
                TOOLS.append({"type": "function", "function": {
                    "name": full, "description": t.get("description", ""),
                    "parameters": {"properties": {}, **(t.get("inputSchema") or {}), "type": "object"}}})  # or the API rejects every request
            log(f"mcp {name}: {len(tools)} tool(s)")
        except Exception as e:
            log(f"mcp {name} failed:", e)


def mcp_call(s, name, args):  # args as a dict: a tool may well have a parameter called name
    # ponytail: the stop button doesn't reach a running MCP call; upgrade: poll s.stop on a thread, when MCP_TIMEOUT waits bite
    srv, tool = SERVERS[name]
    r = srv.rpc("tools/call", name=tool, arguments=args)
    # text only. An image or audio block becomes a placeholder; a tool message can't carry one anyway
    text = "\n".join(c.get("text", f"[{c.get('type')}]") for c in r.get("content", []))
    if not text and "structuredContent" in r:  # a text copy SHOULD come along, says the spec; not every server does
        text = json.dumps(r["structuredContent"], ensure_ascii=False)
    return clip(text) + ("\n[the tool reported an error]" if r.get("isError") else "")
