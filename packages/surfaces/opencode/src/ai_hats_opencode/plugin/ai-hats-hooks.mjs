// ai-hats runtime-hook dispatcher for OpenCode (HATS-1788).
//
// Materialized into the ai-hats session cache and registered through the
// session-scoped OPENCODE_CONFIG `plugin` array as a file:// URL. The composed
// hook list lives in <AI_HATS_SESSION_CACHE_DIR>/opencode/hooks.json using the
// same manifest schema (version 1) as the cline and codex surfaces.
//
// Semantics mirror ai_hats_codex.hook_dispatcher:
//   - no AI_HATS_SESSION_CACHE_DIR pin  -> inert (hookless role, by design)
//   - manifest missing                  -> warn once, inert (nothing to run)
//   - manifest unreadable/malformed     -> fail closed (every mapped tool blocked)
//   - session identity mismatch         -> fail closed (stale cache protection)
//   - hook exit 2                       -> deny: the tool call is thrown away
//   - hook stdout {"decision":"block"}  -> deny with reason
//   - any other hook failure            -> fail open with a warning line

const MANIFEST_VERSION = 1;

// OpenCode tool name -> Claude-dialect tool name. Matchers in SKILL.md
// frontmatter are written against the Claude vocabulary; an unmatched native
// tool passes through untouched because no hook can declare it.
const TOOL_MAP = {
  bash: "Bash",
  read: "Read",
  edit: "Edit",
  write: "Write",
  glob: "Glob",
  grep: "Grep",
  task: "Task",
  webfetch: "WebFetch",
  todowrite: "TodoWrite",
};

function claudeToolName(nativeTool) {
  const key = String(nativeTool || "").toLowerCase();
  return Object.prototype.hasOwnProperty.call(TOOL_MAP, key) ? TOOL_MAP[key] : null;
}

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
  return { state: "ready", hooks: manifest.hooks || {} };
}

function runHook(command, payload) {
  const { spawnSync } = require("node:child_process");
  try {
    return spawnSync(command, [], {
      input: payload,
      encoding: "utf8",
      timeout: 60000,
      killSignal: "SIGKILL",
      env: process.env,
    });
  } catch (error) {
    return { status: -1, stderr: String(error), stdout: "" };
  }
}

export const AiHatsHooksPlugin = async () => {
  const cacheDir = process.env.AI_HATS_SESSION_CACHE_DIR;
  const sessionId = process.env.AI_HATS_SESSION_ID;
  if (!cacheDir) {
    return {}; // hookless composition: the plugin must be a no-op
  }

  const manifest = await loadManifest(cacheDir, sessionId);

  if (manifest.state === "missing") {
    console.warn("[ai-hats] hook manifest absent under session cache; guards disabled");
    return {};
  }
  if (manifest.state !== "ready") {
    const reason =
      manifest.state === "foreign"
        ? `[ai-hats] hook manifest belongs to another session; refusing tool calls (${manifest.path})`
        : `[ai-hats] hook manifest unreadable; refusing tool calls (${manifest.path})`;
    console.error(reason);
    return {
      "tool.execute.before": async () => {
        throw new Error(reason);
      },
    };
  }

  const entriesFor = (event) => manifest.hooks[event] || [];

  const dispatch = async (event, nativeTool, args, canDeny) => {
    const toolName = claudeToolName(nativeTool);
    if (!toolName) return;
    const payload = JSON.stringify({
      session_id: sessionId,
      tool_name: toolName,
      tool_input: args ?? {},
    });
    for (const hook of entriesFor(event)) {
      let matcher;
      try {
        matcher = new RegExp(hook.matcher || "");
      } catch {
        console.warn(`[ai-hats] skipping hook with invalid matcher: ${hook.tag}`);
        continue;
      }
      if (!matcher.test(toolName)) continue;
      const result = runHook(hook.command, payload);
      if (result.status === 2) {
        const detail = String(result.stderr || "blocked by ai-hats guard").trim();
        if (!canDeny) {
          console.error(`[ai-hats] ${hook.tag}: ${detail}`);
          continue;
        }
        throw new Error(`[ai-hats] ${hook.tag}: ${detail}`);
      }
      if (result.status !== 0) {
        console.warn(
          `[ai-hats] hook failed open (${hook.tag}): status=${result.status} ${String(result.stderr || "").trim()}`,
        );
        continue;
      }
      let verdict = null;
      try {
        verdict = result.stdout ? JSON.parse(result.stdout) : null;
      } catch {
        verdict = null;
      }
      if (verdict && verdict.decision === "block") {
        const detail = String(verdict.reason || "blocked by ai-hats guard").trim();
        if (!canDeny) {
          console.error(`[ai-hats] ${hook.tag}: ${detail}`);
          continue;
        }
        throw new Error(`[ai-hats] ${hook.tag}: ${detail}`);
      }
    }
  };

  return {
    "tool.execute.before": async (input, output) => {
      await dispatch("PreToolUse", input.tool, output.args, true);
    },
    "tool.execute.after": async (input, output) => {
      await dispatch("PostToolUse", input.tool, output.args, false);
    },
  };
};
