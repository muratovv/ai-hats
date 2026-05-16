# Orchestration — session tags, JSON, exit codes

When you fan out ai-hats sessions via parallel, xargs, CI, or webhook orchestrators, you need tagged metadata, machine-readable output, and stable exit codes. This guide covers all three.

## Session tags & queryable history

Кастомные `k=v` метаданные на сессиях — для оркестраторов (autosre, CI, batch),
cost attribution, pipeline tracking, A/B экспериментов. Теги попадают в
`metrics.json` под ключ `tags` и индексируются через `session list`.

```bash
# Запись — теги при запуске (повторяемый флаг, до 20 на сессию)
ai-hats agent sre-diagnoser --task "..." \
    --tag alert_fp=abc123 \
    --tag alertname=ImmichContainerDown \
    --tag client=home-lab

# То же для интерактивной сессии
ai-hats --tag client=acme --tag project=migration-v2

# Запрос — фильтры + machine-readable JSON для pipe в jq/parallel
ai-hats session list --tag alert_fp=abc123 --json | jq .
ai-hats session list --role sre-diagnoser --since 2026-04-20 --json
ai-hats session list --tag client=acme --tag project=X --all --json
```

**Валидация (строгая, raise при нарушении):**
- Ключ: `^[a-zA-Z_][a-zA-Z0-9_.\-]*$`, max 64 chars.
- Значение: max 256 chars, непустое.
- Max 20 тегов на сессию.
- Reserved keys (shadow запрещён): `role`, `provider`, `exit_code`, `model`,
  `timed_out`, `error`, `isolation_mode`, `turns`, `tokens`, `models`,
  `tool_calls`, `session_id`, `session_dir`, `started_at`.

**JSON output** — `--json` выдаёт plain список словарей. Форма каждого
элемента — все поля `metrics.json` плюс computed `session_id`, `session_dir`,
`started_at` (ISO-8601). Consumers выбирают нужное через `jq`.

**Рецепт dedup в оркестраторе** (заменяет собой идею `--idempotency-key`):

```bash
# Перед запуском нового диагноза — проверить, есть ли уже сессия с этим fp
fp="$1"
existing=$(ai-hats session list --tag alert_fp="$fp" --since "$(date -u +%Y-%m-%d)" --all --json \
            | jq -r '.[] | select(.exit_code == 0) | .session_id' | head -n1)

if [ -n "$existing" ]; then
    echo "Already diagnosed in session $existing — skipping"
    exit 0
fi
ai-hats agent sre-diagnoser --tag alert_fp="$fp" --task "..."
```

Атомарность check-and-spawn (race между двумя параллельными вебхуками) — на
стороне оркестратора: filelock/redis/что удобнее.

## Machine-readable run

Для fan-out через `parallel`/`xargs`/CI:

```bash
ai-hats agent <role> --task "..." --json
# → stdout: {"session_id":"...","exit_code":0,"role":"...","duration_s":12.3,"tags":{...},...}
```

Форма совпадает с элементом `session list --json` — один и тот же парсинг на
стороне оркестратора. `--json` режим **полностью подавляет** rich-summary в
stdout; человекочитаемый режим (без `--json`) оставлен как был.

**Exit codes** (стабильный контракт, пробрасываются из sub-agent):

| Код | Значение |
|---|---|
| 0 | успех (sub-agent завершился 0) |
| 1 | agent/runtime error (subprocess exit 1, generic exception в runtime) |
| 2 | CLI usage error (неверные флаги — default click) |
| 124 | timeout (sub-agent превысил wall-clock limit) — convention GNU coreutils |
| другой non-zero | форвардится от провайдера (claude/gemini exit code) |

Пример fan-out:

```bash
# N параллельных вызовов, собрать все результаты, отфильтровать успешные
cat tasks.jsonl | jq -r '.task' | parallel -j 3 \
    'ai-hats agent diagnoser --task {} --json' \
  | jq -s 'map(select(.exit_code == 0))'
```

Если надо узнать код завершения одной сессии, не парся stdout — хватит `$?`:

```bash
ai-hats agent diagnoser --task "..." --json > result.json
echo "exit=$?"   # совпадает с .exit_code в result.json
```
