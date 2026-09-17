"""The desktop tool: xdotool and scrot on the host's real X display."""
import json, os, shutil, subprocess, threading, time, uuid

from . import state
from .browser import PlaywrightBrowserBackend
from .state import CFG, HOME, log
from .tools import TOOLS, ToolResult, as_image

DESKTOP_TOOL = {"type": "function", "function": {
    "name": "desktop",
    "description": "Operate the host's real desktop (X11) from screenshots using mouse and keyboard. Every action "
                   "returns the screen size and a new screenshot; choose pixel coordinates from the newest screenshot. "
                   "Call it once at a time. The screen is shared by the whole team and a human may be using it: "
                   "observe before acting.",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["observe", "click", "double_click", "right_click", "move", "drag",
                                                      "type", "key", "scroll", "wait"]},
        "x": {"type": "number"}, "y": {"type": "number"},
        "x2": {"type": "number"}, "y2": {"type": "number"},
        "text": {"type": "string"},
        "key": {"type": "string", "description": "xdotool key such as Return, Tab, ctrl+l or alt+F4"},
        "delta_y": {"type": "number", "description": "scroll notches, negative scrolls up"},
        "seconds": {"type": "number", "description": "wait duration, capped at 10 seconds"}},
        "required": ["action"]}}}

class XDesktopBackend:
    """xdotool and scrot (or ffmpeg) on the one shared X display; actions are serialised."""

    def __init__(self):
        self.lock = threading.Lock()
        self.shot = (["scrot", "-p", "-q", "75"] if shutil.which("scrot") else
                     ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-i", os.environ.get("DISPLAY", ":0"),
                      "-frames:v", "1", "-q:v", "5"])

    def _x(self, *argv, text=None):
        subprocess.run(["xdotool", *map(str, argv)], input=text, text=True, check=True, timeout=30, capture_output=True)

    def _action(self, s, args):
        action = args.get("action")
        if action not in DESKTOP_TOOL["function"]["parameters"]["properties"]["action"]["enum"]:
            raise ValueError(f"unknown desktop action: {action!r}")
        need = {"click": "x y", "double_click": "x y", "right_click": "x y", "move": "x y", "drag": "x y x2 y2",
                "type": "text", "key": "key"}.get(action, "")
        PlaywrightBrowserBackend._need(args, *need.split())
        x, y, x2, y2 = (args.get(k) for k in ("x", "y", "x2", "y2"))
        with self.lock:
            if action in ("click", "double_click", "right_click"):
                repeat = ["--repeat", "2"] if action == "double_click" else []
                self._x("mousemove", x, y, "click", *repeat, "3" if action == "right_click" else "1")
            elif action == "move":
                self._x("mousemove", x, y)
            elif action == "drag":
                self._x("mousemove", x, y, "mousedown", "1", "mousemove", x2, y2, "mouseup", "1")
            elif action == "type":
                self._x("type", "--delay", "12", "--file", "-", text=args["text"])
            elif action == "key":
                self._x("key", "--clearmodifiers", args["key"])
            elif action == "scroll":
                n = int(args.get("delta_y", 3))
                if n:
                    self._x("click", "--repeat", min(abs(n), 20), "5" if n > 0 else "4")
            elif action == "wait":
                end = time.time() + min(max(float(args.get("seconds", 1)), 0), 10)
                while time.time() < end and not s.stop.is_set():
                    time.sleep(min(0.2, end - time.time()))
            time.sleep(0.25)
            root = HOME / "desktop" / "screenshots"
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            shot = root / f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}.jpg"
            subprocess.run([*self.shot, str(shot)], check=True, timeout=15, capture_output=True)
            for old in sorted(root.glob("*.jpg"))[:-20]:
                old.unlink(missing_ok=True)
            w, h = subprocess.check_output(["xdotool", "getdisplaygeometry"], text=True, timeout=10).split()
        # ponytail: full-resolution screenshots; add downscaling (and coordinate mapping) for 4K displays
        result = {"action": action, "status": "ok", "screen": [int(w), int(h)], "screenshot": str(shot)}
        return ToolResult(json.dumps(result), as_image(shot))


def init_desktop_tool():
    """Publish desktop only on an X11 session with xdotool and a screenshot tool."""
    if not CFG.get("desktop", {}).get("enabled", True):
        return
    if not os.environ.get("DISPLAY") or not shutil.which("xdotool") or not (shutil.which("scrot") or shutil.which("ffmpeg")):
        log("desktop disabled: needs an X11 DISPLAY, xdotool and scrot or ffmpeg (apt install xdotool scrot)")
        return
    if not any(t["function"]["name"] == "desktop" for t in TOOLS):
        TOOLS.append(DESKTOP_TOOL)
    if state.DESKTOP is None:
        state.DESKTOP = XDesktopBackend()


def desktop(s, **args):
    if state.DESKTOP is None:
        raise RuntimeError("desktop control is unavailable; it needs an X11 session with xdotool and scrot")
    return state.DESKTOP._action(s, args)
