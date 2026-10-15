# Interactive CLI with SQLite

Status: implemented and verified offline. Implements the approved terminal-chat design using SQLite for
local persistence. The existing OAuth and single-prompt invocation remain usable.
The subsequent tool-calling milestone is defined in [project-tools.md](project-tools.md).

## Observable contract

- `uv run harness.py` opens an interactive terminal after one OAuth login.
  `uv run harness.py "question"` retains the single-request mode.
- Store conversations at `.harness/chat.sqlite3` using Python's `sqlite3`, with
  no ORM. Resume the latest session for the verified client ID, subject, and
  resolved current working directory; do not load another identity's history.
- Save complete turns atomically only after `response.completed`. Preserve the
  user prompt, full output items (including opaque encrypted reasoning), selected
  model, and nullable usage. No OAuth credentials enter the database.
- Replay committed output items plus the next prompt for stateless Responses
  calls. Do not use server conversation IDs, silently truncate, or infer missing
  usage as zero. Display input/output/total token usage only when supplied.
- PromptSession provides input editing, in-memory history seeded from this
  identity/workspace's stored prompts, slash-command completion, and Alt+Enter
  for a newline. Enter submits. Input history does not require another file.
- Rich renders a startup panel, streamed Markdown/code, and current model,
  session, cwd, and last reported token usage. Model/user content is not Rich
  markup; terminal control characters are removed from displayed content.
- `/help` lists commands; `/model` lists available models; `/model SLUG` validates
  before changing the active session model; `/new` creates an empty session
  without deleting earlier sessions; `/exit` or Ctrl+D exits.
- Ctrl+C during input clears the input. During generation it closes the HTTP
  stream and returns to input, without saving a partial turn. Network/provider
  failure also leaves committed history unchanged and the interactive loop alive.
- Existing account registration and process lock remain. Token persistence and
  renewal are now defined in [remember-login.md](remember-login.md). This slice
  does not add tools, shell access, background requests, context compaction, or a
  session browser.

## Decomposition review and verification

SQLite persistence, response replay, and terminal controls are related behaviors
and were implemented in order as separately testable slices. Completed checks:
real temporary SQLite reopen/identity isolation/new-session/atomic-turn tests;
fake HTTP streaming tests for full history and terminal states; command-loop
tests for model validation and cancellation; prompt-toolkit pipe input and a
PTY smoke check for the actual UI. All 41 tests pass. Existing OAuth tests remain in the full suite.
Generated `uv.lock` is excluded from task line counts. Native implementation
continues in the current checkout (the repository has no initial commit).

Review decision: keep these slices together as the approved interactive-chat
milestone. The repository's uncommitted total exceeds 1,000 lines because it
also contains the earlier OAuth milestone; this change adds persistence and
terminal interaction rather than reimplementing authentication. The behavior
boundaries and verification are mapped above. A fresh review found invisible
refusal deltas and script/import exception identity mismatch; both have failing-
then-passing regression tests and are fixed. No reviewer findings remain deferred.

Live ChatGPT login/inference is not validated; tests and the terminal smoke use
synthetic credentials, a fake provider, and temporary databases. SQLite files
hold local conversation content, but no application OAuth credentials.

Sources: prompt-toolkit asking-for-input guide, Rich Live Display guide, and
OpenAI Responses reasoning/state documentation, checked 2026-10-15.
