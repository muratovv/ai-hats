// Browser client for the relay. Binary messages are terminal bytes behind a small
// header; text messages are JSON control. Both sides of that split live in wire.py —
// the constants below are the only place this page encodes knowledge of it.
"use strict";

const PROTOCOL_VERSION = 1;
const OUT_HEADER = 9; // version + u64 seq
const IN_HEADER = 1; // version

const status = document.getElementById("status");
const encoder = new TextEncoder();

function say(text, kind) {
  status.textContent = text;
  if (kind) status.dataset.kind = kind;
  else delete status.dataset.kind;
}

// OSC (BEL- or ST-terminated), CSI, charset designation, two-character escapes, and
// stray control bytes. Charset designation is here because `ESC ( B` otherwise loses
// only its ESC and leaves a literal "(B" in the text.
const CONTROL = /\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -\/]*[@-~]|\x1b[()*+][@-~]|\x1b[@-Z\\-_]|[\x00-\x08\x0b-\x1f\x7f]/g;
const DETAIL_LIMIT = 200;

function readable(raw) {
  if (!raw) return "";
  const text = raw
    .replace(CONTROL, " ")
    // A TUI's rules and padding are real characters, not escapes; left alone they eat
    // the budget that the actual last words need.
    .replace(/(.)\1{3,}/g, "$1$1$1")
    .replace(/\s+/g, " ")
    .trim();
  // Keep the END: a dying process says why in its last line, not its first.
  return text.length > DETAIL_LIMIT ? `…${text.slice(-DETAIL_LIMIT)}` : text;
}

const term = new Terminal({
  convertEol: false,
  cursorBlink: true,
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
  fontSize: 13,
  scrollback: 5000,
});
const fit = new FitAddon.FitAddon();
term.loadAddon(fit);
term.open(document.getElementById("terminal"));
fit.fit();
term.focus();

const params = new URLSearchParams(location.search);
const sid = params.get("sid");
const token = params.get("token") || "";
if (!sid) {
  say("no sid in the URL — open the link hats-relay-attach printed", "error");
  throw new Error("missing sid");
}

const socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/`);
socket.binaryType = "arraybuffer";

socket.addEventListener("open", () => {
  const attach = { op: "attach", sid, cols: term.cols, rows: term.rows };
  if (token) attach.token = token;
  socket.send(JSON.stringify(attach));
});

socket.addEventListener("message", (event) => {
  if (typeof event.data === "string") {
    onControl(event.data);
    return;
  }
  const frame = new Uint8Array(event.data);
  // A version we do not know means the payload behind it is not ours to guess at;
  // painting it would fill the screen with binary rather than say what went wrong.
  if (frame[0] !== PROTOCOL_VERSION) {
    say(`broker speaks protocol ${frame[0]}, this page speaks ${PROTOCOL_VERSION}`, "error");
    socket.close();
    return;
  }
  term.write(frame.subarray(OUT_HEADER));
});

function onControl(text) {
  let message;
  try {
    message = JSON.parse(text);
  } catch {
    say(`unparseable message from broker: ${text}`, "error");
    return;
  }
  if (message.error) {
    say(message.error, "error");
    return;
  }
  if (message.event === "exit") {
    // A killed session is reaped without a code; "exited (null)" would read as a bug.
    const how = message.returncode === null || message.returncode === undefined
      ? "session ended"
      : `session exited (${message.returncode})`;
    // `detail` is the child's raw stdio tail — kilobytes of escape sequences. It exists
    // to explain a session that died on its own, so it is worth showing only when one
    // did, and only after the terminal control is taken back out of it.
    const detail = message.returncode ? readable(message.detail) : "";
    say(detail ? `${how} — ${detail}` : how, "ended");
    return;
  }
  if (message.ok) say(`attached to ${message.sid}`);
}

term.onData((data) => {
  if (socket.readyState !== WebSocket.OPEN) return;
  const payload = encoder.encode(data);
  const frame = new Uint8Array(IN_HEADER + payload.length);
  frame[0] = PROTOCOL_VERSION;
  frame.set(payload, IN_HEADER);
  socket.send(frame);
});

socket.addEventListener("close", () => {
  // Only speak up if nothing already explained why — an eviction or a dead session
  // has a better message than "closed", and it arrived before this.
  if (!status.dataset.kind) say("connection closed — reload to re-attach", "ended");
});

socket.addEventListener("error", () => say("could not reach the broker", "error"));

// Telling the session is driven by xterm's own resize event, NOT by the observer:
// the observer fires on things that do not change the grid (a scrollbar appearing, the
// status line rewrapping), and fitting re-enters it. Sending from there put a burst of
// resize frames between consecutive keystrokes — each one a repaint of a live TUI.
term.onResize(({ cols, rows }) => {
  if (socket.readyState !== WebSocket.OPEN) return;
  socket.send(JSON.stringify({ op: "resize", cols, rows }));
});

let resizeTimer = null;
new ResizeObserver(() => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => fit.fit(), 150);
}).observe(document.getElementById("terminal"));
