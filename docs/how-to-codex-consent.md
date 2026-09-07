# Confirm rack transitions in Codex

Start an ordinary interactive session with runtime hooks enabled:

```bash
ai-hats --provider codex --role assistant
```

For development from a worktree, select both its interpreter and its library:

```bash
AI_HATS_LIBRARY_ROOT="$PWD/packages/ai-hats-library/src/ai_hats_library" \
  ./.venv/bin/python -m ai_hats --provider codex --role maintainer
```

A role declaring `rack.transition` under `apps.consent_gate` receives the
`ai_hats_consent` MCP server automatically. No separate MCP configuration or
timed grant is needed. The agent calls `rack_transition` with the arguments
after `rack transition`, for example `["HATS-042", "execute"]` [1].

The server starts in the session's bound project directory, even when Codex
itself starts from a worktree or another directory.

Run the development command from the worktree root. A session bound to the
main checkout otherwise resolves that checkout's library, which can contain
an older consent hook than the worktree's server. Restart the session after
changing the library selection; existing sessions retain their materialized hooks.

Read the project, task, source state, command and request ID in the form.
Accept authorizes that command once. Decline and Cancel leave it unstarted.
An existing human-issued grant can cover the operation without another form.

Acceptance does not bypass FSM or quality checks. A completed command reports
its exit code. `stale_request` means the source state changed while the form
was open; the command was not started. `indeterminate` means execution may
have had effects: inspect `rack context <ID>` before deciding what to do next.
Do not automatically repeat the mutation.

The tool accepts one state operation per request, including accompanying
`--log` or `--attach` operations. Cross-project `--tasks-dir`, direct worktree
commands and headless approval are outside this transport. The existing
wrapper remains the authorization boundary [2].

## Manual acceptance prompt

```text
Проверь native consent для Codex на новых одноразовых тестовых карточках.
Существующие карточки не изменяй; код проекта не меняй, ничего не мержи.
Подготовка тестовых карточек и их планов разрешена.
Для защищённых переходов используй MCP ai_hats_consent.rack_transition.
Не запускай сервер вручную и не выдавай consent/grants.
Последовательно запроси: Accept, Decline, новый Accept, Cancel.
После каждого ответа проверь состояние через rack context и сообщи
request_id, decision, execution и exit_code. Decline/Cancel не повторяй
автоматически. После проверки остановись и перечисли тестовые карточки.
```

Protocol tests cannot prove that a human saw the native form. Confirm that
separately in the fresh session; correlate results by request ID.

## References

**[1]** — [Rack workflow](how-to-hatrack.md).

**[2]** — [Consent command middleware](adr/0030-consent-is-session-command-middleware.md).
