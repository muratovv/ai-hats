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

const term = new Terminal({
  convertEol: false,
  cursorBlink: true,
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
  fontSize: 13,
  scrollback: 5000,
});
term.open(document.getElementById("terminal"));
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
    const detail = message.detail ? ` — ${message.detail}` : "";
    say(`session exited (${message.returncode})${detail}`, "ended");
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
