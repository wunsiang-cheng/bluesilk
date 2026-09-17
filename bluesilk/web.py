"""The web console: one page, events out over SSE, small POSTs in."""
import hmac, json, mimetypes, os, queue, sys, threading, time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs

from .agent import browser_view, receive
from .setup import apply_settings, settings_view
from .state import HOME, SESSIONS, TOKENS


# --- web console

def transcript(s):
    """The member's conversation as (role, text) pairs, for a page that (re)connects."""
    out = []
    for m in s.messages:
        if browser_view(m):
            continue
        c = m.get("content")
        if isinstance(c, list):
            c = "\n".join(p.get("text", "[image]") for p in c)
        if m["role"] in ("user", "assistant") and c:
            out.append((m["role"], c))
    return out


PAGE = r"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>bluesilk</title>
<style>
:root{--ph:#3f6;--bg:#070b07;--dim:#1a4d27;--hi:#e6ffe9}
.amber{--ph:#fb0;--bg:#0f0a00;--dim:#5c4300;--hi:#fff3d0}
*{box-sizing:border-box}
html,body{height:100%;margin:0;background:#000}
body{color:var(--ph);font:15px/1.45 "Cascadia Mono","IBM Plex Mono","Fira Mono","DejaVu Sans Mono",Menlo,Consolas,monospace;text-shadow:0 0 5px var(--ph)}
#crt{position:fixed;inset:0;display:flex;flex-direction:column;padding:22px 26px;background:var(--bg);border-radius:22px;box-shadow:inset 0 0 100px #000,inset 0 0 8px var(--dim);overflow:hidden}
#crt::before{content:"";position:absolute;inset:0;z-index:2;pointer-events:none;background:repeating-linear-gradient(0deg,rgba(0,0,0,.3) 0 1px,transparent 1px 3px)}
#crt::after{content:"";position:absolute;inset:0;z-index:2;pointer-events:none;background:radial-gradient(ellipse at center,transparent 55%,rgba(0,0,0,.65));animation:flicker 6s infinite}
@keyframes flicker{0%,100%{opacity:1}93%{opacity:1}94%{opacity:.7}95%{opacity:1}}
#out{flex:1;overflow-y:auto;white-space:pre-wrap;overflow-wrap:anywhere;scrollbar-width:none}
.u{color:var(--hi);text-shadow:0 0 5px var(--hi)}
.s{opacity:.6}
b{color:var(--hi)} code,pre.k{color:var(--hi)} pre.k{margin:2px 0;padding:0 0 0 2ch;border-left:2px solid var(--dim);white-space:pre-wrap}
a{color:inherit}
#st{opacity:.7;min-height:1.45em;white-space:pre-wrap}
#st:not(:empty)::after{content:"▮";animation:blink 1s steps(1) infinite}
@keyframes blink{50%{opacity:0}}
#ln{display:flex}
#ps{white-space:pre}
#in{flex:1;background:0;border:0;outline:0;color:inherit;font:inherit;text-shadow:inherit;resize:none;padding:0;height:1.45em;max-height:40vh;caret-color:var(--ph)}
#cfg{position:absolute;inset:0;z-index:1;background:var(--bg);padding:22px 26px;overflow:auto;scrollbar-width:thin;scrollbar-color:var(--dim) transparent}
#cfg label{display:block;margin:12px 0 3px}
#cfg input,#cfg textarea{width:100%;background:0;border:1px solid var(--dim);color:inherit;font:inherit;text-shadow:inherit;padding:4px 6px;outline:0}
#cfg .check{display:inline-block;margin-right:20px}#cfg .check input{width:auto;margin-right:7px}
#cfg input:focus,#cfg textarea:focus{border-color:var(--ph)}
#cfg button{background:0;border:1px solid var(--ph);color:inherit;font:inherit;text-shadow:inherit;padding:4px 12px;margin:16px 8px 0 0;cursor:pointer}
#cfg button:hover{background:var(--dim)}
#err{color:#f66;text-shadow:0 0 5px #f66;margin-top:8px;white-space:pre-wrap}
@media(max-width:600px){#crt,#cfg{padding:12px 14px;border-radius:0}}
</style>
<div id="crt">
 <div id="out"></div>
 <div id="st"></div>
 <div id="ln"><span id="ps">TOKEN&gt; </span><textarea id="in" autofocus autocomplete="off" spellcheck="false" rows="1"></textarea></div>
 <input type="file" id="file" multiple hidden>
 <form id="cfg" hidden>
  <div>== SETTINGS == <span class="s">blank secrets keep their current value; saving restarts bluesilk</span></div>
  <label>OPENROUTER API KEY</label><input name="api_key" type="password" autocomplete="off">
  <label>MODEL <span class="s">an openrouter.ai/models slug that supports tools; blank = the default</span></label><input name="model">
  <label>TELEGRAM USER IDS <span class="s">comma-separated, each must have pressed Start on the bot; empty = no Telegram</span></label><input name="user_ids">
  <label>TELEGRAM BOT TOKEN</label><input name="bot_token" type="password" autocomplete="off">
  <label>WEB CONSOLE MEMBERS <span class="s">names, comma-separated; empty = no web console</span></label><input name="members">
  <label>WEB HOST / PORT <span class="s">127.0.0.1 = this machine only; anything else is plain http on your network</span></label>
  <div style="display:flex;gap:8px"><input name="host"><input name="port" style="width:8ch"></div>
  <label>VISUAL BROWSER <span class="s">visible mode requires a desktop session; delay is 0–2000 ms</span></label>
  <div><label class="check"><input name="browser_visible" type="checkbox" value="1">SHOW CHROMIUM WINDOW</label><label class="check"><input name="browser_cursor" type="checkbox" value="1">SHOW ACTION CURSOR</label></div>
  <input name="browser_slow_mo" type="number" min="0" max="2000" step="50" placeholder="action delay in milliseconds">
  <label>LOGIN LINKS</label><div id="toks" class="s"></div>
  <label>MCP.JSON</label><textarea name="mcp" rows="6" spellcheck="false"></textarea>
  <div id="err"></div>
  <button type="submit">SAVE &amp; RESTART</button><button type="button" id="cancel">CANCEL</button>
 </form>
</div>
<script>
const $=id=>document.getElementById(id),out=$('out'),st=$('st'),inp=$('in'),cfg=$('cfg');
let mode='token',es,typing=false,seen=false;const tq=[];
const esc=s=>s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const md=s=>esc(s).replace(/```(?:\w*\n)?([\s\S]*?)```/g,'<pre class="k">$1</pre>').replace(/`([^`\n]+)`/g,'<code>$1</code>')
 .replace(/\*([^*\n]+)\*/g,'<b>$1</b>').replace(/(^|[^\w])_([^_\n]+)_(?=[^\w]|$)/g,'$1<i>$2</i>');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function line(html,cls){const d=document.createElement('div');if(cls)d.className=cls;d.innerHTML=html;out.append(d);out.scrollTop=out.scrollHeight;return d}
function type(html,cls){tq.push([html,cls]);typing||next()}
function next(){  // typewriter: the text nodes are emptied, then refilled a few chars per frame, one message at a time
 const it=tq.shift();if(!it)return typing=false;typing=true;
 const d=line(...it),w=document.createTreeWalker(d,NodeFilter.SHOW_TEXT),nodes=[];let n,total=0,i=0;
 while(n=w.nextNode()){nodes.push([n,n.data]);total+=n.data.length;n.data=''}
 const step=Math.max(2,Math.ceil(total/150));  // integer: a fractional step makes substr/i drift and skip chars
 (function tick(){let b=step;while(b>0&&nodes.length){const[nd,s]=nodes[0],k=Math.min(b,s.length-i);nd.data+=s.substr(i,k);i+=k;b-=k;if(i>=s.length){nodes.shift();i=0}}
  out.scrollTop=out.scrollHeight;nodes.length?requestAnimationFrame(tick):next()})();
}
function cookie(t){document.cookie='t='+t+';path=/;SameSite=Strict;max-age=31536000'}
async function boot(){
 try{if(localStorage.amber)document.documentElement.classList.add('amber')}catch{}
 for(const l of['BLUESILK CONSOLE','MEM CHECK ........ OK','LINK ............. '+location.host]){line(l,'s');await sleep(160)}
 if(location.hash.length>1){cookie(location.hash.slice(1));history.replaceState(null,'',location.pathname)}
 login();
}
async function login(){
 let r;try{r=await fetch('/settings')}catch{return setTimeout(login,1500)}  // restarting: try again
 if(r.status===401){mode='token';$('ps').textContent='TOKEN> ';line('ENTER YOUR TOKEN (or open your login link).','s');return inp.focus()}
 const v=await r.json();mode='chat';
 v.setup?openCfg(v):connect();
}
function connect(){
 es?.close();es=new EventSource('/events');
 es.addEventListener('hello',e=>{const me=JSON.parse(e.data);if(seen)out.innerHTML='';seen=true;  // a reconnect replays history
  $('ps').textContent=me+'@bluesilk:~$ ';line('LOGGED IN AS '+esc(me)+'. /help FOR COMMANDS.','s');inp.focus()});
 es.addEventListener('history',e=>{for(const[r,t]of JSON.parse(e.data))line(r==='user'?esc($('ps').textContent+t):md(t),r==='user'?'u':'')});
 es.addEventListener('msg',e=>{st.textContent='';type(md(JSON.parse(e.data)))});
 es.addEventListener('status',e=>st.textContent=JSON.parse(e.data));
 es.addEventListener('file',e=>{const d=JSON.parse(e.data),i=d.indexOf('/'),name=esc(d.slice(i+1));type(`[FILE] <a href="/file/${d.slice(0,i)}/${name}" target="_blank">${name}</a>`)});
 es.onerror=()=>{if(es.readyState===2){line('LINK CLOSED.','s');login()}else st.textContent='LINK LOST, RETRYING'};
 es.onopen=()=>st.textContent='';
}
async function post(path,body){const r=await fetch(path,{method:'POST',body});if(!r.ok)line(esc(await r.text()),'s');return r}
inp.onkeydown=async e=>{
 if(e.key==='Escape')return post('/stop');
 if(e.ctrlKey&&e.key==='u'){e.preventDefault();return $('file').click()}
 if(e.key!=='Enter'||e.shiftKey)return;
 e.preventDefault();const t=inp.value.trim();inp.value='';inp.style.height='';if(!t)return;
 if(mode==='token'){cookie(t);return login()}
 line(esc($('ps').textContent+t),'u');
 if(t==='/help')return line('/new  /reset  /settings  /theme  /help    Esc = stop    Ctrl+U, paste or drop = send a file','s');
 if(t==='/theme'){const a=document.documentElement.classList.toggle('amber');try{localStorage.amber=a?1:''}catch{}return}
 if(t==='/settings')return openCfg(await(await fetch('/settings')).json());
 st.textContent='…';post('/send',t);
};
inp.oninput=()=>{inp.style.height='';inp.style.height=inp.scrollHeight+'px'};
document.onmouseup=e=>{if(cfg.hidden&&!e.target.closest('a')&&getSelection().isCollapsed)inp.focus()};
async function upload(f){const cap=inp.value.trim();inp.value='';inp.style.height='';
 line(esc($('ps').textContent+'[sending '+f.name+'] '+cap),'u');st.textContent='…';
 post('/upload?name='+encodeURIComponent(f.name)+'&text='+encodeURIComponent(cap),f)}
$('file').onchange=e=>{for(const f of e.target.files)upload(f);e.target.value=''};
document.ondragover=e=>e.preventDefault();document.ondrop=e=>{e.preventDefault();for(const f of e.dataTransfer.files)upload(f)};
inp.onpaste=e=>{for(const f of e.clipboardData.files)upload(f)};
function openCfg(v){
 cfg.hidden=false;$('cancel').hidden=!!v.setup;$('err').textContent='';
 cfg.api_key.value=cfg.bot_token.value='';cfg.api_key.placeholder=v.api_key||'';cfg.bot_token.placeholder=v.bot_token||'';
 cfg.model.value=v.model;cfg.model.placeholder=v.default_model;cfg.user_ids.value=v.user_ids.join(', ');cfg.host.value=v.host;cfg.port.value=v.port;
 cfg.members.value=Object.values(v.tokens).join(', ');cfg.mcp.value=v.mcp;
 cfg.browser_visible.checked=v.browser_visible;cfg.browser_cursor.checked=v.browser_cursor;cfg.browser_slow_mo.value=v.browser_slow_mo;
 $('toks').innerHTML=Object.entries(v.tokens).map(([t,n])=>`${esc(n)}: <a href="/#${t}">${location.origin}/#${t}</a>`).join('<br>')||'(members get theirs when saved)';
 cfg.api_key.focus();
}
$('cancel').onclick=()=>{cfg.hidden=true;inp.focus()};
cfg.onsubmit=async e=>{
 e.preventDefault();$('err').textContent='VALIDATING…';
 const r=await fetch('/settings',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(cfg)))});
 if(!r.ok)return $('err').textContent=await r.text();
 const v=await r.json();cfg.hidden=true;es?.close();
 line('SAVED. RESTARTING…','s');
 for(const[t,n]of Object.entries(v.tokens))line(`${esc(n)}: ${location.origin}/#${t}`);
 if(v.setup)line('OPEN YOUR LOGIN LINK ABOVE, OR PASTE THE TOKEN AT THE PROMPT.','s');
 setTimeout(login,2000);
};
boot();
</script>
"""  # the whole console: no build step, no CDN, works offline


class Web(BaseHTTPRequestHandler):
    """The web console: one page, events out over SSE, small POSTs in. Auth is the member's token in a cookie."""

    def log_message(self, *a):
        pass

    def auth(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        got = c["t"].value if "t" in c else ""
        for token, name in TOKENS.items():
            if hmac.compare_digest(token, got):
                return SESSIONS[name]

    def reply(self, body, ctype="text/plain; charset=utf-8", code=200, **headers):
        body = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(code)
        for k, v in {"Content-Type": ctype, "Content-Length": str(len(body)), **headers}.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/":
            return self.reply(PAGE, "text/html; charset=utf-8")
        if not (s := self.auth()):
            return self.reply("unauthorized", code=401)
        if path == "/events":
            return self.events(s)
        if path == "/settings":
            return self.reply(settings_view(), "application/json")
        if path.startswith("/file/") and (p := s.files.get(path.split("/")[2])):
            return self.reply(p.read_bytes(), mimetypes.guess_type(p.name)[0] or "application/octet-stream",
                              **{"Content-Disposition": f'inline; filename="{p.name.replace(chr(34), "")}"'})
        self.reply("not found", code=404)

    def do_POST(self):
        path, _, query = self.path.partition("?")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if not (s := self.auth()):
            return self.reply("unauthorized", code=401)
        try:
            if path == "/send":
                receive(s, {"message_id": int(time.time()), "text": body.decode()})
            elif path == "/upload":
                q = parse_qs(query)
                dest = HOME / "inbox" / f"{int(time.time())}_{Path(q.get('name', ['file'])[0]).name}"
                dest.write_bytes(body)
                receive(s, {"message_id": int(time.time()), "text": q.get("text", [""])[0], "local": [dest]})
            elif path == "/stop":
                s.stop.set()
            elif path == "/settings":
                apply_settings(json.loads(body))
                threading.Timer(0.5, restart).start()  # after this reply is out
                return self.reply(settings_view(), "application/json")
            else:
                return self.reply("not found", code=404)
            self.reply("ok")
        except Exception as e:
            self.reply(str(e), code=400)

    def events(self, s):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        q = queue.Queue()
        s.subs.append(q)
        try:
            self.event("hello", s.name)
            self.event("history", transcript(s))
            while True:
                try:
                    self.event(*q.get(timeout=20))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")  # keeps proxies and the browser from giving up on a quiet stream
                    self.wfile.flush()
        except OSError:  # tab closed
            pass
        finally:
            s.subs.remove(q)

    def event(self, kind, data):
        self.wfile.write(f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
        self.wfile.flush()


def restart():
    os.execv(sys.executable, [sys.executable, sys.argv[0]])  # config.json exists now, so main() goes straight to serve()
