"""The built-in tools: shell, send, and the schema list the model sees. Browser, desktop and MCP register into TOOLS."""
import base64, os, shutil, signal, subprocess, threading, time, uuid
from pathlib import Path

from .state import CFG, FOREGROUND, HOME, IMAGE_SIGS, SHELL_TIMEOUT
from .telegram import send_file, send_text

CLIP = 20_000  # long tool output keeps this many chars of head and tail

TOOLS = [
    {"type": "function", "function": {
        "name": "shell",
        "description": "Run a bash command on the host. stdin is closed; stdout+stderr are merged and, past "
                       f"{2 * CLIP} chars, clipped to head and tail (filter big output with grep/head/sed). Still running "
                       f"after {FOREGROUND}s, it continues in the background and its result arrives later as a message.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "description": f"seconds, default {SHELL_TIMEOUT}"}},
            "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "send",
        "description": "Send a message and/or file to the user right now. Your final reply is delivered automatically; "
                       "don't duplicate it here.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"},
            "file": {"type": "string", "description": "path of a file to send"}}}}},
]

# --- tools

def clip(s):
    return s if len(s) <= 2 * CLIP else f"{s[:CLIP]}\n\n[... {len(s) - 2 * CLIP} chars omitted; rerun with grep/head/sed for a specific part ...]\n\n{s[-CLIP:]}"


def shell(s, command, timeout=SHELL_TIMEOUT):
    # POSIX only (process groups), Windows would need taskkill /T
    out = HOME / "jobs" / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.log"
    with out.open("wb") as f:  # a file, not a pipe: whatever the command leaves running can't hang us
        p = subprocess.Popen(command, shell=True, executable=shutil.which("bash"), cwd=Path.home(),
                             stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
    start = time.time()
    while (note := ended(p, start, int(timeout), s.stop)) is None:
        if not s.dream and time.time() - start > FOREGROUND:  # the dream has nobody to report back to, so it waits
            s.jobs += 1
            threading.Thread(target=background, args=(s, p, out, start, int(timeout), command), daemon=True).start()
            return (f"[still running after {FOREGROUND}s, moved to the background (process group {p.pid}, output: {out}). "
                    "End your turn now and tell the user it's running. Don't sleep, poll or read the log: the result "
                    "arrives by itself as a new message, and you answer then.]")
    result = clip(f"exit {p.returncode}\n{out.read_text(errors='replace')}{note}")
    out.unlink()
    return result


def ended(p, start, timeout, stop):
    """Wait up to 1s: None while running, else a note on how it ended. Kills the whole group on timeout or stop."""
    try:
        p.wait(timeout=1)
        return ""
    except subprocess.TimeoutExpired:
        if stop.is_set() or time.time() - start > timeout:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            return "\n[stopped by user]" if stop.is_set() else f"\n[killed after {timeout}s]"


def background(s, p, out, start, timeout, command):
    # the stop button doesn't reach background commands; the agent kills them by process group
    never = threading.Event()
    while (note := ended(p, start, timeout, never)) is None:
        pass
    s.jobs -= 1
    s.q.put(f"[background command finished: {command}]\n[full output: {out}]\n"
            + clip(f"exit {p.returncode}\n{out.read_text(errors='replace')}{note}"))


class ToolResult:
    """A normal tool response plus an image that must be shown to the vision model."""

    def __init__(self, text, image=None):
        self.text, self.image = text, image


def send(s, text="", file=""):
    if text:
        send_text(text)
    if file:
        send_file(file)
    return "sent"


def as_image(path):
    """data: URI if the model can read the file as an image. Models sniff content, not names, so do we."""
    if not CFG.get("vision", True):  # a text-only model: the path in the message is all it gets
        return None
    data = path.read_bytes()
    mime = next((t for sig, t in IMAGE_SIGS if data.startswith(sig)), "image/webp" if data[8:12] == b"WEBP" else None)
    return mime and f"data:{mime};base64,{base64.b64encode(data).decode()}"
