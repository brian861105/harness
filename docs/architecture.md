# Architecture and navigation

The application separates startup, the agent loop, model transport, tools,
authentication, persistence, and presentation. For routine changes, start with
the [feature-to-file map](../README.md#find-the-code).

```text
harness.py                  Original launcher
pyproject.toml              uv, package, and command configuration
src/harness_app/
  cli.py                    Arguments, wiring, process lock, exit diagnostics
  agent.py                  Model/tool loop and complete turn transcripts
  provider.py               Model selection and HTTP/SSE
  config.py                 Endpoints and this checkout's .harness location
  errors.py                 Shared sanitized errors
  auth/
    login.py                Browser login, callback, identity verification
    session.py              Credential cache and token renewal
  tools/
    registry.py             Model-facing schemas and handler registration
    files.py                Path policy, read/search, exact-text edits
    shell.py                Bash and child-process lifecycle
    limits.py               File, output, and execution limits
  permissions.py            Diff/command previews and explicit approval
  store.py                  SQLite sessions and conversation replay
  ui.py                     Terminal display, input, interactive flow
tests/                      Behavior tests grouped by responsibility
docs/                       Architecture, contracts, implementation records
.harness/                   Local state, excluded from Git
```

## One turn through the application

1. `cli.main()` creates the HTTP client, authenticated session, model catalog,
   and SQLite store before starting the UI. Single-prompt mode calls the agent
   directly without conversation persistence.
2. `ui.run_chat()` receives input, loads committed history from `store`, and
   calls `agent.ask()`.
3. `agent.ask()` calls `provider.request_response()`. The provider handles one
   HTTP/SSE request; the agent decides whether another request is needed.
4. After a complete model response requests tools, the agent calls
   `tools.registry.ProjectTools.execute()`. File or Bash handlers execute the
   operation, and the agent sends their results back with the original calls.
5. Edits and commands invoke the injected approval callback. The UI pauses its
   live display and uses `permissions.confirm_tool()` to present the proposal.
   Tool implementations do not read terminal input directly.
6. After the full turn completes, the UI saves the prompt, all model output,
   and tool results to SQLite.

Agent, provider, auth, and tool modules do not import `cli` or `ui`. This removes
the earlier circular dependency between the terminal UI and the launcher.
`permissions.py` presents approval prompts; path restrictions belong to
`tools/files.py`, and process limits belong to `tools/shell.py`.

## Common changes

- **Add a tool:** implement it in its owning module and add its schema and
  handler in `tools/registry.py`. Return JSON-serializable results. Put tests
  under `tests/tools/`.
- **Change model requests or streaming:** edit `provider.py`. Change tool
  continuation order in `agent.py`.
- **Change the terminal or commands:** edit `ui.py`; approval presentation
  belongs to `permissions.py`.
- **Change persistence:** edit `store.py`, preserving account and working
  directory isolation.
- **Debug login:** browser sign-in belongs to `auth/login.py`; saved credentials
  and renewal belong to `auth/session.py`.

The layout refactor preserves credentials and SQLite in `.harness/`, without
migrating user data. Run the package in editable mode through uv from a source
checkout. Global data-directory behavior for standalone wheel installation is
not implemented. See the [tool contract](designs/project-tools.md) for execution,
approval, and cancellation semantics.
