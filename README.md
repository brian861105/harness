# Python Harness

A Python terminal coding harness using ChatGPT OAuth and the Responses API.
It stores conversations and token usage in SQLite and provides tools to read,
search, edit, and run commands in a project.

## Find the code

Application code lives in `src/harness_app/`. The root `harness.py` only launches
it. Tests follow the same responsibilities under `tests/`.

| Responsibility | Source | Tests |
| --- | --- | --- |
| Startup, arguments, application wiring | [cli.py](src/harness_app/cli.py) | [test_cli.py](tests/test_cli.py) |
| Model and tool execution loop | [agent.py](src/harness_app/agent.py) | [test_agent.py](tests/test_agent.py) |
| Model selection, Responses requests, streaming | [provider.py](src/harness_app/provider.py) | [test_provider.py](tests/test_provider.py) |
| Browser OAuth login | [auth/login.py](src/harness_app/auth/login.py) | [test_login.py](tests/auth/test_login.py) |
| Saved login and token renewal | [auth/session.py](src/harness_app/auth/session.py) | [test_session.py](tests/auth/test_session.py) |
| File reading, searching, editing | [tools/files.py](src/harness_app/tools/files.py) | [test_files.py](tests/tools/test_files.py) |
| Bash, timeouts, process cleanup | [tools/shell.py](src/harness_app/tools/shell.py) | [test_shell.py](tests/tools/test_shell.py) |
| Tool schemas and registration | [tools/registry.py](src/harness_app/tools/registry.py) | [test_registry.py](tests/tools/test_registry.py) |
| Diff and command approval | [permissions.py](src/harness_app/permissions.py) | [test_permissions.py](tests/test_permissions.py) |
| Terminal display, input, slash commands | [ui.py](src/harness_app/ui.py) | [test_ui.py](tests/test_ui.py) |
| SQLite persistence and conversation replay | [store.py](src/harness_app/store.py) | [test_store.py](tests/test_store.py) |

Service endpoints and the state directory are in [config.py](src/harness_app/config.py),
resource limits in [tools/limits.py](src/harness_app/tools/limits.py), and shared
errors in [errors.py](src/harness_app/errors.py). See the
[architecture guide](docs/architecture.md) and [documentation index](docs/README.md).
Repository documentation is written in English.

## Start

Run from the project directory with uv, which synchronizes the environment and
installs the local package:

```sh
cd /path/to/harness
uv run harness.py
```

You can also use `uv run harness` or `uv run python -m harness_app`.
This project supports running from a source checkout, with local state in the
checkout's `.harness/` directory.

1. On the first run, sign in with your ChatGPT Pro account in the browser.
2. Complete registration for **Python Harness Lesson** and authorize plan usage.
3. Return to the terminal, enter a prompt, and press Enter for a streamed Markdown response.
4. Later runs resume the latest conversation for the same registered account
   and resolved working directory.

After a successful login, the harness saves OAuth credentials and renews them
near expiration. You do not need to open the browser on every launch or extract
browser cookies. Login times out after five minutes. If the browser does not
open automatically, use the one-time link printed in the terminal on the same
computer. Availability and usage limits depend on your account, plan, and preview access.

For a single response without interactive conversation persistence:

```sh
uv run harness.py "Explain the login flow."
```

To select an available model at startup:

```sh
uv run harness.py --model YOUR_AVAILABLE_MODEL
```

## Terminal controls

| Input | Action |
| --- | --- |
| Enter | Submit a prompt |
| Alt+Enter | Insert a newline; Esc followed by Enter also works |
| Up / Down | Recall previous input |
| Tab | Complete slash commands |
| Ctrl+C | Clear input, or stop generation and return to the prompt |
| Ctrl+D | Exit |
| `/help` | Show commands |
| `/model` | List available models |
| `/model SLUG` | Switch the active conversation's model |
| `/new` | Start a new conversation while keeping previous data |
| `/exit` | Exit |

Only complete turns ending in `response.completed` are saved. Cancelled,
failed, or incomplete turns are excluded from the next request's context.
Displayed token usage totals all model requests in the turn; it is not your
remaining Pro allowance. Missing usage is shown as unknown, not zero.

## Project tools

Describe the work in natural language, for example:

```text
Read src/harness_app/auth/login.py and explain the login flow.
Find uses of sqlite3 in this project.
Add the test command to README.md.
Read scripts/test.sh, explain what it does, and then run it.
```

| Tool | Behavior |
| --- | --- |
| `read_file` | Read UTF-8 code with line numbers, up to 200 lines per call |
| `grep` | Search with ripgrep and return paths, line numbers, and matching text |
| `apply_patch` | Preview a diff, then create, update, or delete one file after approval |
| `run_shell` | Preview a command and working directory, then run Bash, including `.sh` scripts, after approval |

The model selects tools and continues from their results. Edits and commands
require `y` or `yes` at the terminal. Enter or any other input declines; Ctrl+C
stops the turn. Single-prompt mode also enables tools, but declines edits and
commands when stdin is not a terminal.

Dedicated file tools use relative paths inside the initial working directory.
They reject traversal and symlinks, and exclude `.harness`, `.git`, `.env*`,
`.venv`, and `node_modules`, including case variants. Search respects ignore
rules, so ignored code may be omitted. Install `rg` first; on macOS use
`brew install ripgrep`.

File reads and edits support files up to 1 MiB. Read results are capped at
32 KiB. Search and shell processes capture up to 32 KiB of combined raw
stdout/stderr and stop on overflow. Each process has a 60-second timeout and
no interactive stdin.

**Bash runs with your local OS permissions; the working directory is not a
sandbox.** Approved commands can access files outside the project and use the
network. Review the displayed command and script before approving it. Timeout,
output overflow, or Ctrl+C terminates the ordinary process group; background
daemons are unsupported. Cancellation or connection failure does not undo
already completed edits or command effects.

Successful turns save tool calls and results with the conversation. Restarting
replays context without executing stored tools. Failed or cancelled turns do
not save conversation or tool records, although local side effects may remain.
Each turn allows at most 10 model requests and 40 tool calls.

To work on another project, run this from that project's directory:

```sh
uv run --project /path/to/harness harness
```

To add a tool, add its function schema to `TOOLS` in
`src/harness_app/tools/registry.py`, implement a handler in the appropriate
module, and register it in `ProjectTools.execute()`. The loop in `agent.py`
handles calls and returns their results to the model.

The custom `apply_patch` accepts `path`, `old_text`, and `new_text`. Null
`old_text` creates a file. Null `new_text` deletes only when `old_text` matches
the complete file. Updates require exactly one match. This is not OpenAI's
built-in patch format.

## Local data and login

The ignored `.harness/chat.sqlite3` database contains:

- `sessions`: session ID, registered account identity, working directory, and model.
- `turns`: prompts, complete model output and tool calls/results, usage, and model.

Complete output items support stateless Responses replay, including opaque
encrypted reasoning returned by the model. These items do not expose readable
internal reasoning. Treat the database as private: it contains conversation
text, tool arguments, command output, and file content returned by tools.
`/new` keeps old conversations; session browsing and deletion are not implemented.

OAuth access and refresh tokens are stored in `.harness/credentials.json`
using atomic replacement and Unix `0600` permissions. ID tokens are not
persisted. Credentials are bound to the registered account and host ID. The
authentication flow does not put them into SQLite, terminal output, or Git.
Do not share the credential file or include it in public backups.

`.harness/host.json` stores a stable host ID. `registration.json` stores the
issued client ID and verified account identifier. Before each model request,
the harness checks expiration and persists rotated tokens after refresh.
Network or server failures retain the existing credentials. Confirmed terminal
refresh failures start a new browser login. Inference requests that have
already started are never automatically replayed.

When upgrading from the original memory-only version, sign in once to create
the persistent cache. To force a new login, stop the harness, delete this
checkout's `.harness/credentials.json`, and restart.

To switch accounts, stop the harness and delete both
`.harness/registration.json` and `.harness/credentials.json`. Keep the host ID
and SQLite database; another account will not load previous conversations.
Deleting local files does not revoke remote authorization; manage application
access in ChatGPT settings.

## Install in a clean environment

Requirements: macOS or Linux, uv, Python 3.9 or newer, a browser, and ripgrep.
The POSIX process lock allows one running instance per checkout. uv manages
the environment through `pyproject.toml` and `uv.lock`.

```sh
uv sync --locked
uv run harness.py
```

## Verify

```sh
uv run python -m unittest -v
```

To run one area, such as file tools:

```sh
uv run python -m unittest tests.tools.test_files -v
```

Offline tests use temporary SQLite databases, fake HTTP responses, and a local
callback. They do not sign in or consume plan usage. Live account authorization
and inference have not been validated. Context is replayed in full without
automatic compaction; use `/new` when a conversation becomes too large.

## References

- [Sign-in and dynamic registration](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Saving and renewing login](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
- [Stateless reasoning](https://developers.openai.com/api/docs/guides/reasoning)
- [Function calling](https://developers.openai.com/api/docs/guides/function-calling)
- [prompt-toolkit input](https://python-prompt-toolkit.readthedocs.io/en/stable/pages/asking_for_input.html)
- [Rich Live Display](https://rich.readthedocs.io/en/stable/live.html)
