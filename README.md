# Python Harness

A Python terminal coding harness using ChatGPT OAuth and the Responses API.
It keeps conversations in SQLite and can read, search, edit, and run commands
in your project. Repository documentation is written in English.

## Start

Requirements: macOS or Linux, Python 3.9+, uv, a browser, and ripgrep (`rg`).
On macOS, install ripgrep with `brew install ripgrep`.

```sh
cd /path/to/harness
uv sync --locked
uv run harness.py
```

`uv run harness` and `uv run python -m harness_app` use the same interactive
flow. Start in a terminal, then enter prompts; positional prompts and piped
input are rejected. Select a model with `uv run harness.py --model MODEL_SLUG`.

On the first run, sign in with your ChatGPT Pro account and authorize
**Python Harness Lesson**. Return to the terminal and enter a prompt. Later
runs reuse saved OAuth credentials and resume the latest conversation for the
same registered account and resolved working directory. Login expires after
five minutes; if the browser does not open, use the one-time terminal link on
the same computer. Access depends on your account, plan, and preview eligibility.

To work on another project, run from that project's directory:

```sh
uv run --project /path/to/harness harness
```

This project supports source-checkout execution. Local state stays in the
harness checkout's ignored `.harness/` directory. One process per checkout
is allowed.

## Terminal controls

| Input | Action |
| --- | --- |
| Enter | Submit a prompt |
| Alt+Enter | Insert a newline; Esc followed by Enter also works |
| Up / Down | Recall previous input |
| Tab | Complete slash commands |
| Ctrl+C | Clear input, or stop generation and return to the prompt |
| Ctrl+D or `/exit` | Exit |
| `/help` | Show commands |
| `/model [SLUG]` | List available models or switch the active model |
| `/new` | Start an empty conversation, keeping previous data, and reload AGENTS.md and skills |
| `/skill:NAME [REQUEST]` | Send a request with the named skill's `SKILL.md` attached |

Only complete turns are saved. Failed, incomplete, or cancelled generation
is excluded from later context. Interrupting display cleanup after a turn has
been saved keeps that turn and reports it as saved. Usage shows the turn's
input/output/total tokens, not remaining Pro allowance; missing usage stays
unknown. Context is replayed in full without compaction; use `/new` when needed.
Session browsing and deletion are not implemented.

## Project instructions and skills

At startup and on `/new`, the harness loads model instructions for the session:

- `AGENTS.md` in the working directory is appended to the built-in instructions.
  It must be UTF-8, at most 32 KiB, and resolve inside the project (a symlink
  such as `AGENTS.md -> CLAUDE.md` is allowed). Edits apply after `/new`.
- Skills use the [Agent Skills](https://agentskills.io) layout shared by tools
  such as pi and Claude Code: a directory containing `SKILL.md` with YAML
  frontmatter, plus optional `scripts/`, `references/`, or `assets/`.

```text
.agents/skills/review/SKILL.md     # project skill, versioned with the project
~/.agents/skills/review/SKILL.md   # user skill, available in every project
```

```markdown
---
name: review
description: Review a diff for bugs and missing tests. Use before committing.
---
Read the staged diff, then ...
```

`name` uses lowercase letters, digits, and single hyphens (64 characters max);
`description` is required (1024 characters max). Invalid skills are skipped
with a startup warning; on a name collision the project skill wins. Set
`disable-model-invocation: true` to hide a skill from the model so that only
`/skill:NAME` uses it. The startup panel shows what was loaded.

Only each skill's name and description go into the instructions. When a task
matches, the model calls `load_skill` to read `SKILL.md` and the skill's file
list, and can read other files inside that skill directory. Skill scripts run
through `run_shell` with the usual approval. `/skill:NAME` attaches `SKILL.md`
directly; history shows only the typed command.

AGENTS.md and skills are trusted instructions. Review them before working in a
project you did not write, just as you would review its scripts.

## Project tools

Ask naturally: "Find uses of sqlite3", "Update README.md", or "Read test.sh,
explain it, then run it". The model chooses tools and continues from results.

| Tool | Behavior |
| --- | --- |
| `read_file` | Read UTF-8 code with line numbers, up to 200 lines per call |
| `grep` | Search with ripgrep; return paths, line numbers, and matches |
| `apply_patch` | Preview and approve an exact-text file create, update, or delete |
| `run_shell` | Preview and approve Bash commands, including `.sh` scripts |
| `load_skill` | Read a loaded skill's files; offered only when skills exist |

Edits and commands require `y` or `yes` for each call. Other input or EOF
declines; Ctrl+C stops the turn. Approved effects are not rolled back if a
later request fails. Failed turns with local effects produce a warning.

Dedicated file tools accept relative project paths, reject traversal and
symlinks, and exclude `.harness`, `.git`, `.env*`, `.venv`, and `node_modules`,
including case variants. Search respects ignore rules. Files are limited to
1 MiB; read results and combined process output are capped at 32 KiB. Search
and shell processes stop after 60 seconds or output overflow and have no
interactive stdin. Each turn permits up to 10 model requests and 40 tool calls.

**Bash runs with your local OS permissions; the working directory is not a
sandbox.** Commands can access files outside the project and use the network.
Review commands and scripts before approval. Timeout, overflow, or cancellation
kills the ordinary process group; background daemons are unsupported.

## Local data and login

| File in `.harness/` | Contents |
| --- | --- |
| `chat.sqlite3` | Sessions, prompts, full model/tool transcripts, model, and nullable usage |
| `credentials.json` | OAuth access/refresh tokens, expiration, scope, and bound identity |
| `registration.json` | Issued client ID and verified account identifier |
| `host.json` | Stable host ID |
| `run.lock` | Process lock |

The database is private: it includes conversation text, tool arguments, file
content, command output, and opaque encrypted reasoning. Restart replays stored
context without executing tools again. Authentication code never writes
credentials to SQLite, printed diagnostics, or Git; ID tokens are not saved.
Token files use atomic replacement and Unix `0600` permissions. No browser
cookie extraction is needed.

Before each model request, the harness renews expiring tokens and saves rotated
credentials. Network/server failures retain the cache; confirmed terminal refresh
failures request browser login. Inference requests are never automatically retried.

To force login, stop the harness and delete `.harness/credentials.json`. To switch
accounts, also delete `.harness/registration.json`. Keep the host ID and database;
another account cannot load the previous account's conversations. Local deletion
does not revoke remote authorization; manage application access in ChatGPT settings.

## Find the code

Code lives in `src/harness_app/`; root `harness.py` only launches it. Tests mirror
responsibilities under `tests/`. See [architecture.md](docs/architecture.md) for
execution flow, boundaries, and extension rules.

| Responsibility | Source | Tests |
| --- | --- | --- |
| Startup and application wiring | [cli.py](src/harness_app/cli.py) | [test_cli.py](tests/test_cli.py) |
| Session, model, turn execution and saving | [runtime.py](src/harness_app/runtime.py) | [test_runtime.py](tests/test_runtime.py) |
| Model/tool loop | [agent.py](src/harness_app/agent.py) | [test_agent.py](tests/test_agent.py) |
| Instructions, AGENTS.md, and skills | [context.py](src/harness_app/context.py), [skills.py](src/harness_app/skills.py) | [test_skills.py](tests/test_skills.py) |
| Model selection and HTTP/SSE | [provider.py](src/harness_app/provider.py) | [test_provider.py](tests/test_provider.py) |
| Browser OAuth | [auth/login.py](src/harness_app/auth/login.py) | [test_login.py](tests/auth/test_login.py) |
| Credential cache and renewal | [auth/session.py](src/harness_app/auth/session.py) | [test_session.py](tests/auth/test_session.py) |
| File policy, reading, search and edits | [tools/files.py](src/harness_app/tools/files.py) | [test_files.py](tests/tools/test_files.py) |
| Bash and process cleanup | [tools/shell.py](src/harness_app/tools/shell.py) | [test_shell.py](tests/tools/test_shell.py) |
| Tool schemas and dispatch | [tools/registry.py](src/harness_app/tools/registry.py) | [test_registry.py](tests/tools/test_registry.py) |
| Approval presentation | [permissions.py](src/harness_app/permissions.py) | [test_permissions.py](tests/test_permissions.py) |
| Terminal input and rendering | [ui.py](src/harness_app/ui.py) | [test_ui.py](tests/test_ui.py) |
| SQLite | [store.py](src/harness_app/store.py) | [test_store.py](tests/test_store.py) |

Endpoints/state location are in `config.py`, limits in `tools/limits.py`, and
sanitized errors in `errors.py`.

## Verify

```sh
uv run python -m unittest -v
uv run python -m unittest tests.tools.test_files -v
```

Offline tests use fake HTTP, a local callback, and temporary databases/projects.
They do not sign in or consume plan usage. Live login and inference remain
unverified.

## References

- [Sign-in and registration](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Saved login and renewal](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
- [Stateless reasoning](https://developers.openai.com/api/docs/guides/reasoning)
- [Function calling](https://developers.openai.com/api/docs/guides/function-calling)
