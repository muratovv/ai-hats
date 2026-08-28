// ai-hats session dispatcher plugin for OpenCode (HATS-1788, HATS-1792).
//
// Materialized into the ai-hats session cache and registered through the
// session-scoped OPENCODE_CONFIG `plugin` array as a file:// URL. The composed
// hook list and the role's permission rules live in
// <AI_HATS_SESSION_CACHE_DIR>/opencode/hooks.json using the same manifest
// schema (version 1) as the cline and codex surfaces.
//
// Tool-hook semantics: this plugin does not judge. It hands the call to
// `ai_hats.surfaces.opencode.hook_dispatcher` and marshals back the verdict
// document it is given (`hook_channel.to_wire`), because JavaScript here cannot
// hold a verdict and a channel that derives one from an exit code understands
// only the shapes it happened to implement.
//   - no AI_HATS_SESSION_CACHE_DIR pin  -> inert (by design: nothing composed)
//   - manifest present, no rows for the event -> no spawn (nothing to miss)
//   - anything else                     -> the dispatcher decides, including a
//                                          missing, unreadable or foreign
//                                          manifest, which it refuses while
//                                          naming AI_HATS_GATE_BROKEN_ACK
//
// Permission semantics (HATS-1792):
//   - native asks surface on the bus as permission.asked events; the typed
//     `permission.ask` plugin hook is not wired in opencode 1.18.x
//   - first manifest `permissions` rule matching the request decides;
//     decisions are answered through the server API (once / reject)
//   - no matching rule defers: HITL keeps the native TUI prompt, headless
//     `opencode run` auto-rejects on its own
//   - a failed reply fails open the same way (the platform channel stays in charge)

const MANIFEST_VERSION = 1;

async function loadManifest(cacheDir, sessionId) {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const manifestPath = path.join(cacheDir, "opencode", "hooks.json");
  let raw;
  try {
    raw = fs.readFileSync(manifestPath, "utf8");
  } catch {
    return { state: "missing" };
  }
  let manifest;
  try {
    manifest = JSON.parse(raw);
  } catch {
    return { state: "invalid", path: manifestPath };
  }
  if (manifest.version !== MANIFEST_VERSION) {
    return { state: "invalid", path: manifestPath };
  }
  const session = manifest.session || {};
  if (session.id !== sessionId) {
    return { state: "foreign", path: manifestPath };
  }
  return {
    state: "ready",
    hooks: manifest.hooks || {},
    permissions: Array.isArray(manifest.permissions) ? manifest.permissions : [],
  };
}

async function judge(event, nativeTool, args) {
  // `await import`, not `require`: this is an ES module, and `require` is a
  // ReferenceError under Node — `loadManifest` above already had it right.
  const { spawnSync } = await import("node:child_process");
  const python = process.env.AI_HATS_PYTHON;
  if (!python) {
    return {
      decision: "deny",
      reason:
        "[ai-hats] AI_HATS_PYTHON is unset, so no gate can be consulted; " +
        "this session predates the dispatcher — restart it.",
      nudges: [],
    };
  }
  const request = JSON.stringify({
    event,
    payload: {
      session_id: process.env.AI_HATS_SESSION_ID,
      tool_name: nativeTool,
      tool_input: args ?? {},
      hook_event_name: event,
    },
  });
  // Derived from the chain's own budget by the materializer, never written by
  // hand here: an outer bound at or below the inner one kills the dispatcher
  // before it can produce the verdict that names the way past.
  const ceiling = Number(process.env.AI_HATS_HOOK_SURFACE_TIMEOUT_MS) || 300000;
  const run = spawnSync(python, ["-m", "ai_hats.surfaces.opencode.hook_dispatcher"], {
    input: request,
    encoding: "utf8",
    timeout: ceiling,
    killSignal: "SIGKILL",
    env: process.env,
  });
  try {
    const verdict = JSON.parse(run.stdout);
    if (verdict && typeof verdict.decision === "string") return verdict;
  } catch {
    // fall through to the refusal below
  }
  // The dispatcher answers on stdout whatever it concludes, so no answer means
  // it never ran. Passing the call would be guessing on the gate's behalf.
  return {
    decision: "deny",
    reason: `[ai-hats] the hook dispatcher did not answer: ${String(run.stderr || "").trim()}`,
    nudges: [],
  };
}

// A prefix rule matches when every path the request touches sits under the
// rule's directory. Candidates mix opencode patterns and raw paths; a glob
// inside the subtree still carries the prefix, so the check stays glob-free.
function ruleMatches(rule, asked) {
  if (!rule || typeof rule !== "object") return false;
  if (rule.permission !== "*" && rule.permission !== asked.permission) return false;
  if (!rule.prefix) return true;
  const meta = asked.metadata || {};
  const candidates = [meta.filepath, meta.parentDir, ...(asked.patterns || [])].filter(
    (value) => typeof value === "string" && value.length > 0,
  );
  if (candidates.length === 0) return false;
  return candidates.every((value) => value.startsWith(rule.prefix));
}

function argsOf(input, output) {
  for (const side of [output, input]) {
    if (side && typeof side.args === "object" && side.args !== null) return side.args;
  }
  return {};
}

function decide(rules, asked) {
  for (const rule of rules) {
    if (ruleMatches(rule, asked)) return rule;
  }
  return null;
}

async function replyPermission(client, sessionID, permissionID, action) {
  const response = action === "allow" ? "once" : "reject";
  try {
    await client.postSessionIdPermissionsPermissionId({
      path: { id: sessionID, permissionID },
      body: { response },
    });
  } catch (error) {
    console.warn(`[ai-hats] permission reply failed (${response}): ${String(error)}`);
  }
}

export const AiHatsHooksPlugin = async ({ client }) => {
  const cacheDir = process.env.AI_HATS_SESSION_CACHE_DIR;
  const sessionId = process.env.AI_HATS_SESSION_ID;
  if (!cacheDir) {
    return {}; // hookless composition: the plugin must be a no-op
  }

  const manifest = await loadManifest(cacheDir, sessionId);
  const permissionRules = manifest.state === "ready" ? manifest.permissions : [];

  // Every non-ready state still registers the hooks and lets the dispatcher
  // judge. A missing manifest used to return {} here, so a session that lost
  // its manifest before the plugin loaded ran with every gate off for its whole
  // life -- and the refusal the dispatcher would have produced, hatch and all,
  // was never asked for.
  const bound = manifest.state === "ready" ? manifest.hooks || {} : null;

  const dispatch = async (event, nativeTool, args, canDeny) => {
    // Nothing composed is bound to this event: no gate can be missed, and the
    // interpreter this would start costs ~120 ms on the host's own event loop.
    if (bound && !(Array.isArray(bound[event]) && bound[event].length)) return;
    const verdict = await judge(event, nativeTool, args);
    for (const nudge of verdict.nudges || []) {
      if (nudge && nudge.text) console.warn(`[ai-hats] ${nudge.hook || "guard"}: ${nudge.text}`);
    }
    if (verdict.decision === "allow") return;
    const detail = String(verdict.reason || "blocked by an ai-hats guard").trim();
    if (!canDeny) {
      // PostToolUse has no cancel channel here: the call already ran, so the
      // refusal is reported rather than pretended.
      console.error(`[ai-hats] ${verdict.hook || "guard"}: ${detail}`);
      return;
    }
    throw new Error(`[ai-hats] ${verdict.hook || "guard"}: ${detail}`);
  };

  return {
    event: async ({ event }) => {
      if (String(event?.type || "") !== "permission.asked") return;
      const asked = event.properties || {};
      const rule = decide(permissionRules, asked);
      // No rule -> defer to the platform channel (TUI prompt or headless
      // auto-reject); the role decided it has no opinion on this request.
      if (!rule) return;
      await replyPermission(client, asked.sessionID, asked.id, rule.action);
    },
    "tool.execute.before": async (input, output) => {
      await dispatch("PreToolUse", input.tool, argsOf(input, output), true);
    },
    "tool.execute.after": async (input, output) => {
      // `tool.execute.after` carries the RESULT, and the arguments are not
      // reliably on it; whichever side holds them is where they are read from,
      // so a PostToolUse gate is not handed an empty input by construction.
      await dispatch("PostToolUse", input.tool, argsOf(input, output), false);
    },
  };
};
