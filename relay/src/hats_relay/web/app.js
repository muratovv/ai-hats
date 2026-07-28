// Browser client for the relay. Binary messages are terminal bytes behind a small
// header; text messages are JSON control. Both sides of that split live in wire.py —
// the constants below are the only place this page encodes knowledge of it.
"use strict";

const PROTOCOL_VERSION = 1;
const OUT_HEADER = 9; // version + u64 seq
const IN_HEADER = 1; // version

const BROKER = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/`;

const status = document.getElementById("status");
const drawer = document.getElementById("drawer");
const sessionList = document.getElementById("sessions");
const menuToggle = document.getElementById("menu-toggle");
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
const token = params.get("token") || "";
// Which session this page is on. Null is a real state, not an error: a link may carry
// no sid, and killing the session you are on puts you back into it.
let current = params.get("sid");

// One connection serves one session: `list` and `kill` are answered and then closed by
// the broker, and a connection that is already ATTACHED refuses every op but `resize`
// (server.py). So a control op gets its OWN socket — the shape client.py already uses.
function control(op, fields = {}) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(BROKER);
    const fail = () => reject(new Error("could not reach the broker"));
    socket.addEventListener("open", () => socket.send(JSON.stringify({ op, token, ...fields })));
    socket.addEventListener("message", (event) => {
      socket.close();
      let reply;
      try {
        reply = JSON.parse(event.data);
      } catch {
        reject(new Error(`unparseable reply to ${op}`));
        return;
      }
      if (reply.error) reject(new Error(reply.error));
      else resolve(reply);
    });
    // Also fires on the close that FOLLOWS a reply; a settled promise ignores it.
    socket.addEventListener("close", fail);
    socket.addEventListener("error", fail);
  });
}

let socket = null;

function attach() {
  socket = new WebSocket(BROKER);
  socket.binaryType = "arraybuffer";

  socket.addEventListener("open", () => {
    const request = { op: "attach", sid: current, cols: term.cols, rows: term.rows };
    if (token) request.token = token;
    socket.send(JSON.stringify(request));
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

  socket.addEventListener("close", () => {
    // `current` is null when this page killed its own session and already said so.
    // Only speak up if nothing already explained why — an eviction or a dead session
    // has a better message than "closed", and it arrived before this.
    if (!current) return;
    if (!status.dataset.kind) say("connection closed — reload to re-attach", "ended");
  });

  socket.addEventListener("error", () => say("could not reach the broker", "error"));
}

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
    if (!current) return;
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
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  const payload = encoder.encode(data);
  const frame = new Uint8Array(IN_HEADER + payload.length);
  frame[0] = PROTOCOL_VERSION;
  frame.set(payload, IN_HEADER);
  socket.send(frame);
});

// Telling the session is driven by xterm's own resize event, NOT by the observer:
// the observer fires on things that do not change the grid (a scrollbar appearing, the
// status line rewrapping), and fitting re-enters it. Sending from there put a burst of
// resize frames between consecutive keystrokes — each one a repaint of a live TUI.
term.onResize(({ cols, rows }) => {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  socket.send(JSON.stringify({ op: "resize", cols, rows }));
});

let resizeTimer = null;
new ResizeObserver(() => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => fit.fit(), 150);
}).observe(document.getElementById("terminal"));

// ---------------------------------------------------------------- session drawer

const POLL_MS = 2000;
let poll = null;

function ago(createdAt) {
  const seconds = Math.max(0, Date.now() / 1000 - createdAt);
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

function describe(session) {
  const state = session.exited === null || session.exited === undefined
    ? "running"
    : `exited ${session.exited}`;
  const clients = `${session.clients} client${session.clients === 1 ? "" : "s"}`;
  return `${ago(session.created_at)} · ${clients} · ${state}`;
}

function notice(text) {
  sessionList.textContent = "";
  const line = document.createElement("div");
  line.className = "notice";
  line.textContent = text;
  sessionList.append(line);
}

function disarm() {
  for (const button of sessionList.querySelectorAll(".kill[data-armed]")) {
    delete button.dataset.armed;
    button.textContent = "✕";
  }
}

function go(target) {
  if (target === current) {
    setMenu(false);
    return;
  }
  const query = new URLSearchParams({ sid: target, ...(token ? { token } : {}) });
  location.assign(`${location.pathname}?${query}`);
}

function detach(message) {
  // Killing the session this page is ON would otherwise leave it staring at a dead
  // screen. Land where a sid-less link lands, and drop the dead sid from the URL so a
  // reload does not return to it. `say` comes first: it marks the status as spoken for,
  // which is what keeps the socket's own close handler quiet.
  say(message, "ended");
  current = null;
  if (socket) socket.close();
  socket = null;
  term.reset();
  history.replaceState({}, "", token ? `${location.pathname}?token=${token}` : location.pathname);
}

async function kill(target) {
  const own = target === current;
  try {
    await control("kill", { sid: target });
  } catch (exc) {
    notice(exc.message);
    return;
  }
  if (own) detach("session killed — pick another from the menu");
  await refresh();
}

function row(session) {
  const item = document.createElement("div");
  item.className = "session";
  item.dataset.sid = session.sid;

  const pick = document.createElement("button");
  pick.type = "button";
  pick.className = "pick";
  if (session.sid === current) pick.setAttribute("aria-current", "true");
  const role = document.createElement("span");
  role.className = "role";
  role.textContent = (session.spec && session.spec.role) || "?";
  const meta = document.createElement("span");
  meta.className = "meta";
  meta.textContent = describe(session);
  pick.append(role, meta);
  pick.addEventListener("click", () => go(session.sid));

  const button = document.createElement("button");
  button.type = "button";
  button.className = "kill";
  button.textContent = "✕";
  button.title = `kill ${session.sid.slice(0, 8)}`;
  // Killing ends a live agent and cannot be undone — on a phone that is one mis-tap,
  // so the first press only arms and says so.
  button.addEventListener("click", () => {
    if (button.dataset.armed !== "true") {
      disarm();
      button.dataset.armed = "true";
      button.textContent = "kill?";
      return;
    }
    kill(session.sid);
  });

  item.append(pick, button);
  return item;
}

// What a row is built from. Age is deliberately absent: it changes every second, and
// rebuilding the list on that would drop the armed kill and whatever holds focus.
function signature(sessions) {
  return sessions.map((s) => `${s.sid}:${s.exited}:${s.clients}`).join("|");
}

let rendered = "";

async function refresh(quiet = false) {
  if (!quiet) notice("loading…");
  let reply;
  try {
    reply = await control("list");
  } catch (exc) {
    notice(exc.message);
    rendered = "";
    return;
  }
  const sessions = reply.sessions || [];
  const now = signature(sessions);
  if (quiet && now === rendered) {
    // Nothing came or went, so leave the DOM alone and just let the ages tick.
    for (const session of sessions) {
      const meta = sessionList.querySelector(`.session[data-sid="${session.sid}"] .meta`);
      if (meta) meta.textContent = describe(session);
    }
    return;
  }
  rendered = now;
  if (!sessions.length) {
    notice("no live sessions");
    return;
  }
  sessionList.textContent = "";
  for (const session of sessions) sessionList.append(row(session));
}

function menuIsOpen() {
  return document.body.classList.contains("menu-open");
}

function setMenu(open) {
  document.body.classList.toggle("menu-open", open);
  drawer.setAttribute("aria-hidden", String(!open));
  menuToggle.setAttribute("aria-expanded", String(open));
  clearInterval(poll);
  if (open) {
    // Focus has to leave the terminal, or keystrokes meant for the menu reach the
    // session; the close button is the one target that exists before the list loads.
    document.getElementById("menu-close").focus();
    refresh();
    // The list is otherwise a snapshot: a session that starts or dies elsewhere leaves
    // an open drawer showing something that is no longer true.
    poll = setInterval(() => refresh(true), POLL_MS);
  } else {
    poll = null;
    rendered = "";
    disarm();
    term.focus();
  }
}

menuToggle.addEventListener("click", () => setMenu(!menuIsOpen()));
document.getElementById("menu-close").addEventListener("click", () => setMenu(false));
document.getElementById("backdrop").addEventListener("click", () => setMenu(false));
document.addEventListener("keydown", (event) => {
  // Esc is load-bearing INSIDE the session, so it may only be taken while the menu is up.
  if (event.key === "Escape" && menuIsOpen()) {
    event.preventDefault();
    setMenu(false);
  }
});

if (current) {
  attach();
} else {
  // A missing sid is not an error: the token is the durable capability and the sid is
  // disposable, so a bookmarked link outlives the session it was minted for.
  say("no session attached — pick one from the menu", "ended");
  setMenu(true);
}
