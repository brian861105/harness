# Architecture

This is a synchronous, single-process harness. The
[README](../README.md#find-the-code) maps features to source and tests and covers
startup, controls, tool limits, and local data.

## Execution flow

```text
CLI -> UI -> Runtime -> Agent -> Provider
                |         |
                v         v
              Store     Tools -> approval/activity callbacks -> UI
```

1. `cli.main()` rejects nonterminal stdin and positional prompts before creating
   state. It holds the process lock and wires the HTTP client, OAuth session,
   model catalog, SQLite store, runtime, and UI.
2. `ui.run_chat(runtime)` handles terminal input, rendering, slash commands, and
   confirmations. `/model` and `/new` delegate state changes to the runtime.
3. `ChatRuntime.run_turn()` creates tools, loads committed context, and invokes
   `agent.ask()` with an output stream and approval/activity callbacks.
4. The agent calls the provider, executes complete tool calls sequentially, and
   continues with every model output item and matching tool result. The provider
   handles HTTP/SSE and resolves a current access token before each request.
5. The runtime saves the completed turn atomically and updates usage. Failure or
   cancellation during generation leaves committed context unchanged. Display
   interruption after completion must report the turn as saved. Approved local
   effects remain after failure and the UI warns about them.

All launchers use this flow. Single-prompt mode was removed because it bypassed
conversation persistence without serving the interactive coding workflow.
Runtime, agent, provider, auth, and tools never import UI or CLI. The runtime can
also be called directly with a stream and callbacks; missing approval declines
edits and commands. There is no separate headless CLI mode.

## State and trust boundaries

- `store.py` owns SQLite. Sessions are isolated by verified client ID, subject,
  and resolved workspace; startup resumes the latest matching session. Validate
  requested models before changing state; fall back if a saved model disappears.
  Preserve full output items, including opaque reasoning and tool results, for
  replay without reexecution. Missing usage stays unknown.
- `auth/login.py` binds a loopback callback with a five-minute timeout and fresh
  state, nonce, and PKCE. Validate callback parameters, issued client ID, signed
  ID-token issuer/audience/expiry/nonce/subject, and required inference scope.
- `auth/session.py` binds cached credentials to registration and host identity,
  rejects unsafe/malformed files, and atomically saves refresh rotation. Only
  terminal refresh failures trigger login; transient failures retain credentials.
  Never print secrets or automatically replay inference after a failure.
- File path restrictions belong to `tools/files.py`; execution/output limits and
  process cleanup belong to `tools/shell.py` and `tools/limits.py`. Bash uses OS
  permissions, not a filesystem sandbox. `permissions.py` only presents approval.
- State stays in the source checkout's `.harness/`; no schema migration is needed.
  A global data directory for standalone wheel installs is not implemented.

## Changes and tests

Add tools by defining a function schema in `tools/registry.py`, implementing a
handler in its owning module, and registering it in `ProjectTools.execute()`.
Return JSON-serializable results. The custom `apply_patch` accepts `path`,
`old_text`, and `new_text`: null old text creates; null new text deletes only
with a complete-file match; updates require one unique match. Approved edits
recheck the previewed snapshot and preserve executable permissions.

Keep session/turn policy in `runtime.py`, model/tool continuation in `agent.py`,
transport in `provider.py`, and input/display in `ui.py`. Tests follow those
boundaries: runtime tests cover state not already exercised by terminal flows;
UI tests verify command/approval/recovery integration with real SQLite and fake
HTTP. Provider, auth, and tool tests cover protocol and safety edge cases.
Shared synthetic responses live in `tests/fakes.py`.
