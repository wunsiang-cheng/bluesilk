"""The browser tool: Playwright Chromium driven from screenshots."""
import atexit, json, queue, re, threading, time, uuid
from pathlib import Path

from . import state
from .state import CFG, HOME, log, quiet
from .tools import TOOLS, ToolResult, as_image

COMPUTER_TOOL = {"type": "function", "function": {
    "name": "browser",
    "description": "Operate a private Chromium browser from screenshots using mouse and keyboard. Actions except close "
                   "return the current URL, title, viewport size and a new screenshot; choose pixel coordinates from the "
                   "newest screenshot, never DOM selectors. Call it once at a time so you see each result, and observe "
                   "again whenever the screen is uncertain.",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["open", "observe", "click", "double_click", "move", "drag",
                                                      "type", "key", "scroll", "wait", "back", "forward", "reload",
                                                      "tabs", "switch_tab", "close"]},
        "url": {"type": "string", "description": "http(s) URL for open"},
        "x": {"type": "number"}, "y": {"type": "number"},
        "x2": {"type": "number"}, "y2": {"type": "number"},
        "text": {"type": "string"},
        "key": {"type": "string", "description": "Playwright key such as Enter, Tab or Control+L"},
        "delta_x": {"type": "number"}, "delta_y": {"type": "number"},
        "seconds": {"type": "number", "description": "wait duration, capped at 10 seconds"},
        "tab": {"type": "integer", "description": "zero-based tab index for switch_tab"}},
        "required": ["action"]}}}


class PlaywrightBrowserBackend:
    """One Playwright thread and Chromium process, with one persistent context (cookies, tabs, downloads)."""

    def __init__(self):
        self.q, self.ready, self.start_lock = queue.Queue(), threading.Event(), threading.Lock()
        self.thread, self.error, self.closed = None, None, False
        self.contexts, self.active, self.downloads = {}, {}, {}

    def start(self):
        with self.start_lock:
            if self.closed:
                raise RuntimeError("browser backend is closed")
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name="bluesilk-browser", daemon=True)
                self.thread.start()
        if not self.ready.wait(45):
            raise RuntimeError("Chromium did not start within 45 seconds")
        if self.error:
            raise RuntimeError(f"Chromium could not start: {self.error}. Run `bluesilk browser install`")

    def submit(self, s, args):
        self.start()
        answer = queue.Queue(1)
        self.q.put((s, args, answer))
        while True:
            try:
                result = answer.get(timeout=1)
                break
            except queue.Empty:
                if not self.thread.is_alive():
                    raise RuntimeError("browser backend stopped during an action") from None
        if isinstance(result, Exception):
            raise result
        return result

    def reset(self):
        if self.thread and self.thread.is_alive() and self.ready.is_set() and not self.error:
            answer = queue.Queue(1)
            self.q.put((None, {"action": "reset"}, answer))
            answer.get(timeout=60)

    def close(self):
        with self.start_lock:
            if self.closed:
                return
            self.closed = True
            if self.thread and self.thread.is_alive():
                answer = queue.Queue(1)
                self.q.put((None, {"action": "shutdown"}, answer))
                quiet(answer.get, timeout=60)
                self.thread.join(timeout=60)

    def _run(self):
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeout, sync_playwright
            self.PlaywrightTimeout = PlaywrightTimeout
            self.playwright = sync_playwright().start()
            cfg = CFG.get("browser", {})
            launch = {"headless": True}
            if cfg.get("executable_path"):
                launch["executable_path"] = str(Path(cfg["executable_path"]).expanduser())
            self.browser = self.playwright.chromium.launch(**launch)
        except Exception as e:
            self.error = repr(e)
            self.ready.set()
            return
        self.ready.set()
        try:
            while True:
                s, args, answer = self.q.get()
                try:
                    if args["action"] == "shutdown":
                        answer.put("closed")
                        break
                    if args["action"] == "reset":
                        self._reset()
                        answer.put("reset")
                    else:
                        answer.put(self._action(s, args))
                except Exception as e:
                    answer.put(e)
        finally:
            for context in list(self.contexts.values()):
                quiet(context.close)
            quiet(self.browser.close)
            quiet(self.playwright.stop)

    def _dir(self, key):
        path = HOME / "browser" / key
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        (path / "screenshots").mkdir(exist_ok=True)
        (path / "downloads").mkdir(exist_ok=True)
        return path

    def _download(self, key, download):
        try:
            root = self._dir(key) / "downloads"
            name = Path(download.suggested_filename).name or "download"
            path = root / f"{int(time.time())}-{uuid.uuid4().hex[:6]}-{name}"
            download.save_as(path)
            self.downloads[key] = str(path)
        except Exception as e:
            self.downloads[key] = f"download failed: {e}"

    def _context(self, s):
        key = "main"  # ponytail: the dicts below are keyed for the 0.8 per-member contexts; one key now
        if key not in self.contexts:
            root, cfg = self._dir(key), CFG.get("browser", {})
            viewport = cfg.get("viewport", [1280, 720])
            if not (isinstance(viewport, list) and len(viewport) == 2 and all(isinstance(x, int) and x > 0 for x in viewport)):
                viewport = [1280, 720]
            storage = root / "storage.json"
            options = {"viewport": {"width": viewport[0], "height": viewport[1]}, "device_scale_factor": 1,
                       "accept_downloads": True}
            if storage.exists():
                options["storage_state"] = str(storage)
            context = self.browser.new_context(**options)
            self.contexts[key] = context
            context.on("page", lambda page, k=key: self._watch(k, page))
            page = context.new_page()
            if self.active.get(key) is not page:
                self._watch(key, page)
        return key, self.contexts[key]

    def _watch(self, key, page):
        self.active[key] = page
        page.set_default_timeout(10_000)
        page.set_default_navigation_timeout(30_000)
        page.on("download", lambda download, k=key: self._download(k, download))

    def _page(self, key, context):
        pages = [p for p in context.pages if not p.is_closed()]
        if not pages:
            page = context.new_page()
            if self.active.get(key) is not page:
                self._watch(key, page)
            pages = [self.active[key]]
        page = self.active.get(key)
        if page not in pages:
            page = self.active[key] = pages[-1]
        return page

    @staticmethod
    def _need(args, *names):
        missing = [name for name in names if args.get(name) is None]
        if missing:
            raise ValueError(f"{args['action']} requires {', '.join(missing)}")

    def _action(self, s, args):
        action = args.get("action")
        if action not in COMPUTER_TOOL["function"]["parameters"]["properties"]["action"]["enum"]:
            raise ValueError(f"unknown browser action: {action!r}")
        key, context = self._context(s)
        page, note = self._page(key, context), "ok"
        try:
            if action == "open":
                self._need(args, "url")
                if not re.match(r"^https?://", args["url"], re.I):
                    raise ValueError("open requires an http:// or https:// URL")
                page.goto(args["url"], wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(300)
            elif action == "observe":
                pass
            elif action in ("click", "double_click"):
                self._need(args, "x", "y")
                getattr(page.mouse, "dblclick" if action == "double_click" else "click")(args["x"], args["y"])
                page.wait_for_timeout(250)
            elif action == "move":
                self._need(args, "x", "y")
                page.mouse.move(args["x"], args["y"])
            elif action == "drag":
                self._need(args, "x", "y", "x2", "y2")
                page.mouse.move(args["x"], args["y"])
                page.mouse.down()
                page.mouse.move(args["x2"], args["y2"], steps=10)
                page.mouse.up()
            elif action == "type":
                self._need(args, "text")
                page.keyboard.insert_text(args["text"])
            elif action == "key":
                self._need(args, "key")
                key_name = re.sub(r"(?i)(^|\+)ctrl(?=\+|$)", r"\1Control", args["key"])
                key_name = re.sub(r"(?i)(^|\+)cmd(?=\+|$)", r"\1Meta", key_name)
                page.keyboard.press(key_name)
            elif action == "scroll":
                page.mouse.wheel(args.get("delta_x", 0), args.get("delta_y", 0))
                page.wait_for_timeout(250)
            elif action == "wait":
                end = time.time() + min(max(float(args.get("seconds", 1)), 0), 10)
                while time.time() < end:
                    if s.stop.is_set():
                        raise RuntimeError("stopped by user")
                    page.wait_for_timeout(int(min(200, max(1, (end - time.time()) * 1000))))
            elif action in ("back", "forward", "reload"):
                getattr(page, "go_back" if action == "back" else "go_forward" if action == "forward" else "reload")(
                    wait_until="domcontentloaded", timeout=30_000)
            elif action == "tabs":
                note = [{"tab": i, "url": p.url, "active": p is page} for i, p in enumerate(context.pages) if not p.is_closed()]
            elif action == "switch_tab":
                self._need(args, "tab")
                pages = [p for p in context.pages if not p.is_closed()]
                if not 0 <= int(args["tab"]) < len(pages):
                    raise ValueError(f"tab must be between 0 and {len(pages) - 1}")
                page = self.active[key] = pages[int(args["tab"])]
                page.bring_to_front()
            elif action == "close":
                context.storage_state(path=str(self._dir(key) / "storage.json"))
                context.close()
                self.contexts.pop(key, None)
                self.active.pop(key, None)
                return ToolResult(json.dumps({"action": action, "status": "closed"}))
        except self.PlaywrightTimeout:
            note = "timed out; showing the current page"

        page = self._page(key, context)  # a click may have opened and focused a popup
        root = self._dir(key)
        shot = root / "screenshots" / f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}.jpg"
        page.screenshot(path=str(shot), type="jpeg", quality=75, full_page=False, timeout=10_000)
        context.storage_state(path=str(root / "storage.json"))
        old = sorted((root / "screenshots").glob("*.jpg"))[:-20]
        for path in old:
            path.unlink(missing_ok=True)
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        result = {"action": action, "status": note, "url": page.url, "title": page.title(),
                  "viewport": viewport, "screenshot": str(shot)}
        if key in self.downloads:
            result["latest_download"] = self.downloads[key]
        return ToolResult(json.dumps(result, ensure_ascii=False), as_image(shot))

    def _reset(self):
        for context in list(self.contexts.values()):
            quiet(context.close)
        self.contexts.clear()
        self.active.clear()
        self.downloads.clear()


def init_browser_tool():
    if not CFG.get("browser", {}).get("enabled", True):
        return
    if not any(t["function"]["name"] == "browser" for t in TOOLS):
        TOOLS.append(COMPUTER_TOOL)
    if state.BROWSER is None:
        state.BROWSER = PlaywrightBrowserBackend()
        atexit.register(state.BROWSER.close)


def browser(s, **args):
    if state.BROWSER is None:
        raise RuntimeError("the browser is disabled in the config")
    return state.BROWSER.submit(s, args)
