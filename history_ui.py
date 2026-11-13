"""Dictation history in your browser, with infinite scroll and search.

Usage:
  python history_ui.py               # opens http://127.0.0.1:8765
  python history_ui.py --no-browser  # just run the server
"""
import json
import re
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HISTORY_FILE = Path(__file__).parent / "history.jsonl"
AUDIO_DIR = Path(__file__).parent / "audio"
PORT = 8765
PAGE_SIZE_MAX = 100


def load_entries():
    if not HISTORY_FILE.exists():
        return []
    lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()][::-1]  # newest first


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            self.send(200, "text/html; charset=utf-8", PAGE.encode())
        elif url.path == "/api/history":
            qs = parse_qs(url.query)
            offset = int(qs.get("offset", ["0"])[0])
            limit = min(int(qs.get("limit", ["30"])[0]), PAGE_SIZE_MAX)
            q = qs.get("q", [""])[0].strip().lower()
            entries = load_entries()
            total_words = sum(len(e["text"].split()) for e in entries)
            if q:
                entries = [e for e in entries if q in e["text"].lower()]
            body = {
                "items": entries[offset : offset + limit],
                "total": len(entries),
                "total_words": total_words,
            }
            self.send(200, "application/json", json.dumps(body, ensure_ascii=False).encode())
        elif url.path.startswith("/audio/"):
            self.send_audio(url.path[len("/audio/") :])
        else:
            self.send(404, "text/plain", b"not found")

    def send_audio(self, name):
        # Only plain file names inside audio/; supports Range so the player can seek
        path = AUDIO_DIR / name
        if not re.fullmatch(r"[\w-]+\.wav", name) or not path.is_file():
            self.send(404, "text/plain", b"not found")
            return
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if m and (m[1] or m[2]):
            if m[1]:
                start, end = int(m[1]), min(int(m[2]) if m[2] else size - 1, size - 1)
            else:
                start = max(size - int(m[2]), 0)
            if start > end:
                self.send(416, "text/plain", b"bad range", {"Content-Range": f"bytes */{size}"})
                return
            status = 206
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(end - start + 1)
        headers = {"Accept-Ranges": "bytes"}
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        self.send(status, "audio/wav", data, headers)

    def send(self, status, ctype, data, headers=None):
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass  # keep the terminal quiet


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dictation History</title>
<style>
  :root {
    --bg: #f7f5f2; --card: #fdfcfb; --border: #e8e4de; --text: #1f1d1a;
    --muted: #8a857d; --hover: #f1eee9; --accent: #e9a24f; --hl: #fbe7c6;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #1a1918; --card: #222120; --border: #34322f; --text: #ebe8e3;
      --muted: #8f8a83; --hover: #2a2927; --accent: #e9a24f; --hl: #5a4320;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  .wrap { max-width: 860px; margin: 0 auto; padding: 32px 16px 80px; }
  header { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
  h1 { font-size: 26px; margin: 0; font-weight: 600; }
  .stats { color: var(--muted); font-size: 14px; }
  .stats b { color: var(--text); font-weight: 600; }
  .search { margin: 24px 0 8px; width: 100%; padding: 10px 14px; font: inherit; color: var(--text);
            background: var(--card); border: 1px solid var(--border); border-radius: 10px; outline: none; }
  .search:focus { border-color: var(--accent); }
  .day { margin: 28px 0 10px; font-size: 13px; letter-spacing: .08em; color: var(--muted); font-weight: 600; }
  .list { background: var(--card); border: 1px solid var(--border); border-radius: 14px; overflow: hidden; }
  .row { display: grid; grid-template-columns: 80px 1fr auto; gap: 16px; align-items: center;
         padding: 14px 18px; border-top: 1px solid var(--border); }
  .row:first-child { border-top: none; }
  .row:hover { background: var(--hover); }
  .time { color: var(--muted); font-size: 13px; }
  .text { white-space: pre-wrap; word-break: break-word; }
  mark { background: var(--hl); color: inherit; border-radius: 3px; }
  .copy { opacity: 0; border: none; background: none; cursor: pointer; color: var(--muted);
          padding: 6px; border-radius: 6px; font-size: 15px; }
  .row:hover .copy, .copy.playing { opacity: 1; }
  .actions { display: flex; gap: 2px; }
  .copy:hover { color: var(--text); background: var(--border); }
  .status { text-align: center; color: var(--muted); padding: 24px; font-size: 14px; }
  @media (max-width: 560px) {
    .row { grid-template-columns: 1fr auto; }
    .time { grid-column: 1 / -1; margin-bottom: -10px; }
    .copy { opacity: 1; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Dictation history</h1>
    <div class="stats"><b id="count">0</b> dictations · <b id="words">0</b> words</div>
  </header>
  <input id="search" class="search" type="search" placeholder="Search your history…" autocomplete="off">
  <div id="feed"></div>
  <div id="status" class="status">Loading…</div>
</div>
<script>
const PAGE = 30;
const player = new Audio(); let playingBtn = null;
player.onended = player.onpause = () => { if (playingBtn) { playingBtn.textContent = "▶"; playingBtn.classList.remove("playing"); } };
let offset = 0, total = Infinity, loading = false, lastDay = null, currentList = null, query = "", newestTime = null;
const feed = document.getElementById("feed"), statusEl = document.getElementById("status");

function dayLabel(d) {
  const today = new Date(); today.setHours(0,0,0,0);
  const that = new Date(d); that.setHours(0,0,0,0);
  const diff = Math.round((today - that) / 86400000);
  if (diff === 0) return "TODAY";
  if (diff === 1) return "YESTERDAY";
  return that.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" }).toUpperCase();
}

function timeLabel(d) {
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }).replace(" ", "").toLowerCase();
}

function fillText(el, text) {
  // Highlight search matches without using innerHTML on user text
  if (!query) { el.textContent = text; return; }
  const lower = text.toLowerCase(); let i = 0;
  while (true) {
    const j = lower.indexOf(query, i);
    if (j < 0) { el.append(text.slice(i)); break; }
    el.append(text.slice(i, j));
    const m = document.createElement("mark"); m.textContent = text.slice(j, j + query.length); el.append(m);
    i = j + query.length;
  }
}

function addEntry(e) {
  const d = new Date(e.time), key = d.toDateString();
  if (key !== lastDay) {
    lastDay = key;
    const h = document.createElement("div"); h.className = "day"; h.textContent = dayLabel(d); feed.append(h);
    currentList = document.createElement("div"); currentList.className = "list"; feed.append(currentList);
  }
  const row = document.createElement("div"); row.className = "row";
  const t = document.createElement("div"); t.className = "time"; t.textContent = timeLabel(d);
  const txt = document.createElement("div"); txt.className = "text"; fillText(txt, e.text);
  const btn = document.createElement("button"); btn.className = "copy"; btn.title = "Copy"; btn.textContent = "⧉";
  btn.onclick = async () => { await navigator.clipboard.writeText(e.text); btn.textContent = "✓"; setTimeout(() => btn.textContent = "⧉", 1200); };
  const actions = document.createElement("div"); actions.className = "actions";
  if (e.audio) {
    const play = document.createElement("button"); play.className = "copy"; play.title = "Play"; play.textContent = "▶";
    play.onclick = () => {
      if (playingBtn === play && !player.paused) { player.pause(); return; }
      player.pause(); playingBtn = play;
      player.src = "/audio/" + encodeURIComponent(e.audio);
      player.play().then(() => { play.textContent = "⏸"; play.classList.add("playing"); })
        .catch(() => { play.textContent = "✕"; play.title = "Audio file missing"; });
    };
    actions.append(play);
  }
  actions.append(btn);
  row.append(t, txt, actions); currentList.append(row);
}

async function loadMore() {
  if (loading || offset >= total) return;
  loading = true; statusEl.textContent = "Loading…";
  const myQuery = query;
  try {
    const r = await fetch(`/api/history?offset=${offset}&limit=${PAGE}&q=${encodeURIComponent(query)}`);
    const data = await r.json();
    if (myQuery !== query) return; // search changed while loading
    total = data.total; offset += data.items.length;
    document.getElementById("words").textContent = data.total_words.toLocaleString();
    if (!query) document.getElementById("count").textContent = data.total.toLocaleString();
    if (offset === data.items.length && !query) newestTime = data.items[0]?.time ?? null;
    data.items.forEach(addEntry);
    statusEl.textContent = total === 0 ? (query ? "No matches." : "No dictations yet.") : (offset >= total ? "That's everything." : "");
  } catch (err) {
    statusEl.textContent = "Could not load history. Is history_ui.py still running?";
  } finally {
    loading = false;
  }
  // Keep loading if the page isn't tall enough to scroll yet
  if (offset < total && document.body.scrollHeight <= innerHeight + 200) loadMore();
}

function reset() {
  offset = 0; total = Infinity; lastDay = null; currentList = null; feed.replaceChildren(); loading = false; loadMore();
}

new IntersectionObserver(es => { if (es[0].isIntersecting) loadMore(); }, { rootMargin: "400px" }).observe(statusEl);
let timer; document.getElementById("search").addEventListener("input", ev => {
  clearTimeout(timer); timer = setTimeout(() => { query = ev.target.value.trim().toLowerCase(); reset(); }, 200);
});
loadMore();

// Live updates: every 3s check for a newer entry; reload only if you're near the top and not searching
setInterval(async () => {
  if (query || loading || scrollY > 200 || !player.paused) return;
  try {
    const data = await (await fetch("/api/history?offset=0&limit=1")).json();
    const latest = data.items[0]?.time ?? null;
    if (latest !== newestTime) reset();
  } catch (err) {}
}, 3000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"History UI running at {url}  (Ctrl+C to stop)")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
