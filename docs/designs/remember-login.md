# Remember OAuth login

Status: implemented and verified offline (2026-10-15). The user wants startup to reuse login rather than require the
browser every time. Implement this through OAuth access/refresh tokens; do not
read browser cookies or another application's credential store.

## Contract

- After verified browser login, atomically save the issued client ID, verified
  subject, stable host ID, access/refresh tokens, granted scope and expiration
  in ignored `.harness/credentials.json`. Use an owner-only regular file (0600)
  under the existing private state directory. Credentials never enter SQLite,
  printed diagnostics, command arguments or conversation messages.
- On startup, reuse an unexpired access token without browser or token exchange.
  Cache identity must match `registration.json` and `host.json` exactly.
- Before each model request, obtain a current access token; renew near expiry
  using the issued client ID, refresh token and original resource, without scope.
  Persist the rotating refresh token with the new access token in one replacement.
- The existing lifetime process lock serializes local refreshes. A valid token
  remains reusable on a later run, including tokens renewed during a long chat.
- Only terminal refresh errors (`invalid_grant`, `invalid_refresh_token`,
  `token_expired`, `refresh_token_expired`, `refresh_token_invalidated`,
  `refresh_token_reused`) clear unusable credentials and request browser login.
  A browser login only happens before a request, never as an automatic replay
  of an inference request that may already have started.
- Network, 5xx, unknown permission failures and malformed refresh responses do
  not erase saved credentials or cause a browser-login loop. Report a sanitized
  error and allow a later retry. No live model request is automatically retried.
- Reject symlink, non-regular, broadly readable or malformed credential files
  and mismatched identity before any refresh or inference.
- If no persistent credentials exist (including upgrades from the earlier
  version), perform the existing browser login once. Existing chat data remains.

## Decomposition and checks

Credential persistence, renewal and runtime wiring are one coherent remembered-
login feature, implemented as testable slices. Deterministic clock + fake HTTP
tests cover reuse, rotation across restart, terminal versus transient failures,
identity binding, secure file permissions, and live-chat token lookup. Real
loopback login tests and the SQLite/UI suite remain covered. The full offline
suite passes 58 tests. An authentication-boundary review prompted neutral error
diagnostics with sanitized machine codes and account-reset documentation; both
were addressed. No new dependency was needed. Real account login, refresh and
inference still require live verification after the user signs in.

References (checked 2026-10-15):
- https://developers.openai.com/siwc/token-sharing-open-source/sign-in
- https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions
- https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery
