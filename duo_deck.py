"""The phone deck (v0.11): DUO serves a small local web page on the PC
for Joshua's phone -- the one place a web page is wanted (he ordered
it). A QR code in the window opens it on his phone; the page follows
whichever model is thinking, and he can send messages from the phone
and export the full transcript with one button. LAN-only, no
accounts, no internet.

Moved here verbatim from dual_chat.py in v0.31 (audit T19): the
window file was 16 lines under its 4,000-line bound, and the deck
is one self-contained feature. dual_chat.py re-exports the names
so `from dual_chat import ...` keeps working unchanged.
"""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from duo_transcript import default_full_filename

# QR codes for the phone deck: Nayuki's QR Code generator (MIT),
# vendored as qrcodegen.py next to this file. A missing module only
# costs the QR picture -- the deck URL is still shown as text.
try:
    import qrcodegen as _qrcodegen
except ImportError:  # pragma: no cover - the zip always ships it
    _qrcodegen = None


# Phone deck: DUO serves a small local page for his phone. Fixed
# port with a fallback chain if it is busy; DUAL_CHAT_DECK_PORT
# overrides it (0 means "any free port" -- used by the tests).
DECK_PORT = 8765
DECK_PORT_TRIES = 5
DECK_PORT_ENV = "DUAL_CHAT_DECK_PORT"

# Plain-language messages the deck sends back to the phone. The
# desktop send path uses MSG_EMPTY too -- it lives here because the
# deck handler needs it, and dual_chat.py re-exports it.
MSG_EMPTY = "Type a message first."
MSG_PHONE_TARGET = "Pick who to send to: A, B, or Both."


def get_lan_ip():
    """This computer's LAN address, for the deck URL. A UDP connect
    learns the address without sending any traffic; with no route at
    all it falls back to loopback."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        finally:
            sock.close()
    except OSError:
        return "127.0.0.1"


def build_deck_state(convo, state):
    """Snapshot of the conversation for the phone deck: both sides'
    transcripts (labels + display text, exactly as the window shows
    them), model names, which side is thinking, relay status. The
    lists are copied: the deck thread reads while the GUI thread
    writes, so a snapshot must never see a half-appended message."""
    # APP_VERSION lives in dual_chat (the single source) and
    # dual_chat imports this module, so it is read lazily
    # here -- a top-level import would be circular.
    from dual_chat import APP_VERSION
    sides = {}
    for side in ("A", "B"):
        side_obj = convo.sides[side]
        # Copy first: the deck thread reads while the GUI thread
        # writes, so the snapshot must never see a half-appended
        # message. One list of messages, so the three can never
        # drift out of sync the way the old parallel arrays could.
        messages = [{"label": msg.label, "text": msg.display_text()}
                    for msg in list(side_obj.messages)]
        sides[side] = {"model": side_obj.model, "messages": messages}
    active = sorted(state.get("active") or [])
    queued = sorted(state.get("queued") or [])
    if active:
        active_side = active[0]
    elif queued:
        active_side = queued[0]
    elif state.get("summarizing"):
        active_side = state["summarizing"]
    else:
        active_side = None
    relay = state.get("relay")
    return {
        "sides": sides,
        "active_side": active_side,
        "busy": bool(state.get("busy")),
        "relay": relay.status_running() if relay is not None else None,
        "version": APP_VERSION,
    }


DECK_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DUO</title>
<style>
body { margin: 0; padding: 12px; background: #1c1c1e; color: #f2f2f7;
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif; font-size: 18px; }
#top { display: flex; justify-content: space-between; align-items: baseline;
  margin-bottom: 10px; }
#title { font-size: 22px; font-weight: bold; }
#relay { font-size: 14px; color: #a1a1a6; }
.panel { background: #2c2c2e; border-radius: 10px; padding: 10px 12px;
  margin-bottom: 10px; }
#big-msgs { max-height: 45vh; overflow-y: auto; }
#small-msgs { max-height: 18vh; overflow-y: auto; font-size: 15px; color: #d1d1d6; }
.head { display: flex; justify-content: space-between; font-weight: bold;
  margin-bottom: 8px; }
.thinking { color: #0a84ff; font-weight: normal; font-size: 15px; }
.msg { margin-bottom: 14px; }
.lbl { font-weight: bold; }
.txt { white-space: pre-wrap; word-wrap: break-word; }
#composer { margin-top: 4px; }
#msg { width: 100%; box-sizing: border-box; font-size: 18px; padding: 10px;
  border-radius: 10px; border: none; background: #2c2c2e; color: #f2f2f7; }
#targets { display: flex; gap: 8px; margin-top: 8px; }
#targets button, #send { flex: 1; min-height: 48px; font-size: 18px;
  border-radius: 10px; border: none; background: #3a3a3c; color: #f2f2f7; }
#targets button.on { background: #0a84ff; }
#send { background: #30d158; color: #000; font-weight: bold; }
#err { color: #ff6961; min-height: 22px; margin-top: 6px; font-size: 15px; }
#foot { margin-top: 10px; text-align: center; }
#export { color: #0a84ff; font-size: 16px; }
</style>
</head>
<body>
<div id="top"><span id="title">DUO</span><span id="relay"></span></div>
<div class="panel"><div class="head"><span id="big-name"></span><span id="big-thinking" class="thinking"></span></div><div id="big-msgs"></div></div>
<div class="panel"><div class="head"><span id="small-name"></span><span></span></div><div id="small-msgs"></div></div>
<div id="composer">
<textarea id="msg" rows="2" placeholder="Type a message..."></textarea>
<div id="targets">
<button type="button" data-t="A">A</button>
<button type="button" data-t="B">B</button>
<button type="button" data-t="Both">Both</button>
<button type="button" id="send">Send</button>
</div>
<div id="err"></div>
</div>
<div id="foot"><a id="export" href="/transcript">Export transcript</a></div>
<script>
var bigSide = "A";
var lastState = "";
var target = "";
function show(el, msgs) {
  while (el.firstChild) { el.removeChild(el.firstChild); }
  for (var i = 0; i < msgs.length; i++) {
    var block = document.createElement("div");
    block.className = "msg";
    var lbl = document.createElement("div");
    lbl.className = "lbl";
    lbl.textContent = msgs[i].label + ":";
    var txt = document.createElement("div");
    txt.className = "txt";
    txt.textContent = msgs[i].text;
    block.appendChild(lbl);
    block.appendChild(txt);
    el.appendChild(block);
  }
}
function atBottom(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 60;
}
function render(state) {
  var active = state.active_side;
  if (active && active !== bigSide) { bigSide = active; }
  var smallSide = bigSide === "A" ? "B" : "A";
  var big = state.sides[bigSide];
  var small = state.sides[smallSide];
  document.getElementById("big-name").textContent =
    "Model " + bigSide + (big.model ? " (" + big.model + ")" : "");
  document.getElementById("small-name").textContent =
    "Model " + smallSide + (small.model ? " (" + small.model + ")" : "");
  document.getElementById("big-thinking").textContent =
    (active === bigSide) ? "thinking..." : "";
  document.getElementById("relay").textContent = state.relay || "";
  var bigEl = document.getElementById("big-msgs");
  var stick = atBottom(bigEl);
  show(bigEl, big.messages);
  if (stick) { bigEl.scrollTop = bigEl.scrollHeight; }
  var smallMsgs = small.messages.slice(-3);
  show(document.getElementById("small-msgs"), smallMsgs);
}
function poll() {
  fetch("/state").then(function (r) { return r.json(); }).then(function (state) {
    var raw = JSON.stringify(state);
    if (raw !== lastState) { lastState = raw; render(state); }
  }).catch(function () {});
}
document.querySelectorAll("#targets button[data-t]").forEach(function (btn) {
  btn.addEventListener("click", function () {
    document.querySelectorAll("#targets button[data-t]").forEach(function (b) {
      b.classList.remove("on");
    });
    btn.classList.add("on");
    target = btn.getAttribute("data-t");
    document.getElementById("err").textContent = "";
  });
});
document.getElementById("send").addEventListener("click", function () {
  var box = document.getElementById("msg");
  var err = document.getElementById("err");
  if (!target) { err.textContent = "Pick who to send to: A, B, or Both."; return; }
  if (!box.value.trim()) { err.textContent = "Type a message first."; return; }
  err.textContent = "";
  fetch("/send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: box.value, target: target })
  }).then(function (r) { return r.json(); }).then(function (res) {
    if (res.ok) { box.value = ""; poll(); }
    else { err.textContent = res.error || "Couldn't send that."; }
  }).catch(function () {
    err.textContent = "Couldn't reach DUO. Try again.";
  });
});
poll();
setInterval(poll, 2000);
</script>
</body>
</html>
"""


class _DeckRequestHandler(BaseHTTPRequestHandler):
    """One deck request. The page, the live state, phone sends, and
    the transcript download -- nothing else."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # No request logging: the deck stays quiet.

    @property
    def _deck(self):
        return self.server.deck

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, body, content_type="text/html; charset=utf-8",
                   filename=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if filename is not None:
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % filename)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send_text(DECK_PAGE_HTML)
        elif path == "/state":
            try:
                self._send_json(self._deck.get_state())
            except Exception:
                self._send_json({"error": "DUO is busy. Try again."},
                                status=500)
        elif path == "/transcript":
            try:
                text = self._deck.get_transcript()
            except Exception:
                self._send_json(
                    {"error": "Couldn't build the transcript."}, status=500)
                return
            self._send_text(text, "text/plain; charset=utf-8",
                            filename=default_full_filename())
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/send":
            self._send_json({"error": "not found"}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        length = max(0, min(length, 1024 * 1024))  # 1 MB cap
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json(
                {"ok": False,
                 "error": "That message didn't arrive right. Try again."})
            return
        if not isinstance(payload, dict):
            self._send_json(
                {"ok": False,
                 "error": "That message didn't arrive right. Try again."})
            return
        text = payload.get("text", "")
        target = payload.get("target", "")
        if not isinstance(text, str) or not text.strip():
            self._send_json({"ok": False, "error": MSG_EMPTY})
            return
        if target not in ("A", "B", "Both"):
            self._send_json({"ok": False, "error": MSG_PHONE_TARGET})
            return
        error = self._deck.send_message(text, target)
        if error is None:
            self._send_json({"ok": True})
        else:
            self._send_json({"ok": False, "error": error})


class DeckServer:
    """The phone deck's HTTP server: the page, the live state, phone
    sends, and the transcript download. Runs on its own thread; the
    callbacks reach into the window (get_state snapshots are copied,
    send_message marshals onto the GUI thread in build_window)."""

    def __init__(self, get_state, send_message, get_transcript,
                 host="0.0.0.0", port=DECK_PORT):
        self.get_state = get_state
        self.send_message = send_message
        self.get_transcript = get_transcript
        self.host = host
        self.port = port
        self.httpd = None
        self.thread = None

    @property
    def running(self):
        return self.httpd is not None

    @property
    def url(self):
        if not self.running:
            return None
        return "http://%s:%d/" % (get_lan_ip(), self.port)

    def start(self):
        """Bind and serve. Tries port, port+1, ... (DECK_PORT_TRIES
        ports); port 0 means any free port. Returns True on success."""
        if self.port == 0:
            tries = [0]
        else:
            tries = range(self.port, self.port + DECK_PORT_TRIES)
        for port in tries:
            try:
                httpd = ThreadingHTTPServer(
                    (self.host, port), _DeckRequestHandler)
            except OSError:
                continue
            httpd.daemon_threads = True
            httpd.deck = self
            self.httpd = httpd
            self.port = httpd.server_address[1]
            self.thread = threading.Thread(
                target=httpd.serve_forever, daemon=True)
            self.thread.start()
            return True
        return False

    def stop(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
            self.thread = None


def render_qr_pixmap(url, scale=6, border=4):
    """A QR code pixmap for `url`, or None when the encoder is missing.
    Drawn with QPainter: black modules on white, with a quiet zone."""
    if _qrcodegen is None:
        return None
    from PySide6 import QtCore, QtGui
    qr = _qrcodegen.QrCode.encode_text(url, _qrcodegen.QrCode.Ecc.MEDIUM)
    size = qr.get_size()
    pixels = (size + border * 2) * scale
    image = QtGui.QImage(pixels, pixels, QtGui.QImage.Format_RGB32)
    image.fill(QtGui.QColor("white"))
    painter = QtGui.QPainter(image)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QColor("black"))
    for y in range(size):
        for x in range(size):
            if qr.get_module(x, y):
                painter.drawRect((x + border) * scale,
                                 (y + border) * scale, scale, scale)
    painter.end()
    return QtGui.QPixmap.fromImage(image)
