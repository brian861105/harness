# Interactive CLI Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement these slices in the current session.

**Goal:** Run a terminal conversation whose completed turns survive restart in SQLite.

**Architecture:** Keep OAuth and HTTP in `harness.py`; add `chat_store.py` for
SQLite and `chat_ui.py` for terminal interaction. Commit complete turns at the
boundary between a successful provider result and the next prompt.

**Tech Stack:** Python 3.9+, sqlite3, httpx, prompt-toolkit 3, Rich 13/14, uv.

**Spec:** `docs/designs/interactive-cli.md`

## Global constraints

- Preserve OAuth and one-shot usage; tokens remain in memory.
- Resume only the verified identity/workspace's latest session.
- Save only completed turns, preserving full provider output items.
- Implement native in the current checkout; do not create a commit or PR.

## Review focus

- Cancellation or network failure must not commit a partial turn.
- Reopening SQLite must replay opaque reasoning and message items in order.
- Model changes must reject unavailable slugs before persisting them.
- A switched account or working directory must not receive earlier context.
- Display control characters must not become terminal escape sequences.

## Slice 1: durable conversations

Files: `chat_store.py`, `test_chat_store.py`.
Interface: `ChatStore.session(owner, workspace, model, new=False)` returns session
metadata; `inputs(session_id)` returns input items; `append_turn(session_id,
prompt, response, model)` atomically records a completed turn; `set_model` updates
metadata; `prompts(owner, workspace)` seeds input recall; `turns(session_id)`
returns persisted turns for rendering.

- [x] Write tests for reopen/replay, identity/workspace isolation, new session,
  invalid-result rejection, nullable usage, and model persistence.
- [x] Run tests red, implement SQLite, run focused tests green.

## Slice 2: stateless streaming continuation

Files: `harness.py`, `test_harness.py`.
Interface: `ask(..., history=None)` streams to the supplied writer and returns
the completed response for persistence. Callers still supply the new prompt.

- [x] Test request history and returned completed output/usage before changing
  `ask`; preserve existing failure tests.
- [x] Implement continuation, run stream tests green.

## Slice 3: interactive terminal

Files: `chat_ui.py`, `test_chat_ui.py`, `harness.py`, `pyproject.toml`, `README.md`.
Interface: `run_chat(client, access_token, catalog, store, owner, workspace,
requested_model=None)` owns the prompt loop and invokes `ask` through the caller.

- [x] Test successive turns, slash commands, invalid models, cancellation,
  provider failure recovery, token reporting, and sanitized display.
- [x] Add prompt-toolkit and Rich with uv; implement UI and default entrypoint.
- [x] Run all tests plus CLI-help and PTY checks; request targeted review and
  document live-account validation limits.

## Execution record

- SQLite: 6 real-database tests failed against the initial missing implementation
  and passed after implementation. Reopen, isolation, new-session preservation,
  incomplete-turn rejection, nullable usage and model restore are covered.
- Streaming continuation: the new history test failed before the API change;
  completed-item reasoning and refusal-delta regressions also failed before fixes.
- Terminal: initial UI tests failed against the missing loop, then passed.
  Real prompt-toolkit pipe input verifies commands and Alt+Enter. A PTY smoke
  exercised rendered Markdown/code, token counts, `/model`, `/new`, `/help`,
  Ctrl+C input cancellation, and `/exit` against a fake provider.
- Script-mode error identity: reproduced the wrong exception across the script
  and imported UI boundary. Moved the shared public error into `errors.py`;
  the regression now passes and safe startup diagnostics are preserved.
- Final verification: `uv run --locked --offline python -m unittest -v` passed
  41 tests; CLI help passed. Live login and inference were not run.
- Native implementation used the existing checkout. No commit, PR, external
  project, or persistent credential store was created.
