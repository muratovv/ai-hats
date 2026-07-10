import { spawnSync } from "node:child_process";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

const AI_HATS_DIR = process.env.AI_HATS_DIR ?? "";
const PROJECT_DIR = process.env.AI_HATS_PROJECT_DIR ?? "";
const HOOKS_DIR = join(AI_HATS_DIR, "library", "hooks");
const INDEX_PATH = join(PROJECT_DIR, ".cline", "plugins", "ai-hats-hooks.json");

interface HookEntry { event: string; cline_tool: string; script: string }

let _index: HookEntry[] | null = null;
function loadIndex(): HookEntry[] {
  if (_index !== null) return _index;
  try {
    const raw = existsSync(INDEX_PATH) ? readFileSync(INDEX_PATH, "utf8") : "[]";
    _index = JSON.parse(raw) as HookEntry[];
  } catch {
    _index = [];
  }
  return _index;
}

function buildStdin(input: Record<string, unknown>): string {
  return JSON.stringify({ tool_input: { command: (input.command as string) ?? "" } });
}

function runHook(script: string, stdin: string): void {
  const path = join(HOOKS_DIR, script);
  if (!existsSync(path)) throw new Error("[ai-hats] hook not found: " + path);
  const res = spawnSync(path, [], { input: stdin, encoding: "utf8", timeout: 10000 });
  if (res.error) throw new Error("[ai-hats] hook spawn error: " + script + "\n" + res.error.message);
  if (res.status !== 0) throw new Error("[ai-hats] BLOCKED by " + script + ":\n" + (res.stderr?.trim() ?? ""));
}

const plugin = {
  name: "ai-hats-hooks",
  manifest: { capabilities: ["hooks"] },
  setup() {},
  hooks: {
    beforeTool(context: { toolCall: { name: string }; input: Record<string, unknown> }) {
      for (const h of loadIndex()) {
        if (h.event !== "PreToolUse") continue;
        if (context.toolCall.name !== h.cline_tool) continue;
        runHook(h.script, buildStdin(context.input));
      }
    },
    afterTool() {},
  },
};

export default plugin;
