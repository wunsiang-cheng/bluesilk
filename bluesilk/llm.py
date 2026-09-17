"""OpenRouter chat completions."""
import time

from .state import API, CFG, MODEL, http, log
from .tools import TOOLS


# --- OpenRouter

def chat(s, messages, **extra):
    headers = {"Authorization": f"Bearer {CFG['api_key']}", "HTTP-Referer": "https://github.com/wunsiang-cheng/bluesilk",
               "X-Title": "bluesilk"}  # the last two: OpenRouter's optional app attribution
    for attempt in range(4):
        try:
            r = http(f"{API}/chat/completions", {"model": CFG.get("model", MODEL), "messages": messages, "tools": TOOLS, **extra},
                     headers=headers, timeout=900)
            s.tokens = r["usage"]["prompt_tokens"]
            return r["choices"][0]["message"]
        except OSError as e:  # network errors, timeouts, HTTP errors
            code = getattr(e, "code", 500)
            if attempt == 3 or 400 <= code < 500 and code != 429:
                raise
            log("retrying:", e)
            time.sleep(5 * 2 ** attempt)
