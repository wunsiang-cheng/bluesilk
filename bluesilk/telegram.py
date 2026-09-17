"""Telegram transport, and the outbound side of the web console (a web member's send_text is a push)."""
import time, uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from .state import CFG, HOME, SESSIONS, http, quiet


# --- Telegram

def tg_url(method, token=None):
    return f"https://api.telegram.org/bot{token or CFG['bot_token']}/{method}"


def tg(method, token=None, **params):
    return http(tg_url(method, token), params, timeout=70)["result"]


def send_text(uid, text):
    if (s := SESSIONS.get(uid)) and s.web:
        return s.push("msg", text)
    for i in range(0, len(text), 4096):
        chunk = text[i:i + 4096]
        try:
            tg("sendMessage", chat_id=uid, text=chunk, parse_mode="Markdown")
        except HTTPError as e:
            if e.code != 400:
                raise
            tg("sendMessage", chat_id=uid, text=chunk)  # the model's Markdown didn't parse


def send_file(uid, path):
    path = Path(path).expanduser()
    if (s := SESSIONS.get(uid)) and s.web:  # the page links to /file/<id>/<name>; only files sent this way are served
        fid = uuid.uuid4().hex
        s.files[fid] = path
        return s.push("file", f"{fid}/{path.name}")
    quiet(tg, "sendChatAction", chat_id=uid, action="upload_document")
    b = uuid.uuid4().hex
    head = (f'--{b}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{uid}\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="document"; filename="{path.name.replace(chr(34), "")}"\r\n\r\n')
    body = head.encode() + path.read_bytes() + f"\r\n--{b}--\r\n".encode()
    http(tg_url("sendDocument"), data=body, headers={"Content-Type": f"multipart/form-data; boundary={b}"}, timeout=600)


def download(file):
    fp = tg("getFile", file_id=file["file_id"])["file_path"]
    dest = HOME / "inbox" / f"{int(time.time())}_{Path(file.get('file_name') or fp).name}"
    with urlopen(f"https://api.telegram.org/file/bot{CFG['bot_token']}/{fp}", timeout=300) as r:
        dest.write_bytes(r.read())
    return dest


def draft(s, text=None):
    """Live status under the member's message: empty text shows Telegram's "Thinking..." placeholder."""
    if text is not None:
        s.draft_text = text[:300]
    if s.web:
        s.push("status", s.status())
    elif s.draft_id:
        quiet(tg, "sendMessageDraft", chat_id=s.uid, draft_id=s.draft_id, text=s.draft_text, can_stop=True)


def pulse():
    for s in SESSIONS.values():
        draft(s)


def heartbeat():
    while True:  # drafts vanish after 30s
        time.sleep(20)
        pulse()
