# First response with ChatGPT sign-in

Status: implemented; verified offline. Live account authorization and inference
remain to be verified by the user. A local Python learning exercise, invoked with a prompt, opens
the system browser for ChatGPT plan authorization and streams one response.
The user's decisions are Python, ChatGPT Pro, OAuth rather than an API key,
and an application-owned harness. No Codex runtime is used.

## Contract

- Bind only `127.0.0.1` on an available port, using `/auth/callback`. Start the
  listener before launching the browser; expire the attempt after five minutes.
- Generate fresh state, nonce, and PKCE for each attempt. Reject mismatched or
  duplicate callback parameters, OAuth denial, missing authorization code, and
  missing or changed issued client ID before exchanging a code.
- Exchange with the issued client ID and exact callback URI. Verify the ID
  token signature using OpenAI's JWKS, issuer, audience, expiration, nonce, and
  subject. A returning registration must retain the same subject. Require the
  token response's `chatgpt.tokens.use.direct` scope before inference.
- Persist a stable host ID before sign-in and an issued client ID before token
  exchange, so a failed exchange does not lose the registration. Store only
  registration metadata in ignored `.harness/`; tokens remain in memory.
- Fetch the signed-in account's visible models. Use the first listed model by
  default; an explicit model must be available to that account.
- Send the prompt to the public Responses endpoint with array input,
  `store=false`, and `stream=true`. Success requires `response.completed`;
  failures, incomplete responses, and interrupted streams exit unsuccessfully.
- No response request is automatically retried. Print no token, callback URL,
  authorization code, raw server error, or JWT validation diagnostic.

## Scope and review

Related behaviors (OAuth, registration, and streaming) are kept together because
each is necessary for the first runnable exercise. This is an inline
decomposition review: callback and ID-token rejection scenarios, metadata-only
persistence, account model selection, and stream terminal-state tests map to
the behaviors above. The change remains one first-response exercise; it has no
independent tool-execution feature. Generated `uv.lock` is excluded from the
decomposition line-count trigger. Dependencies have one source in
`pyproject.toml`; `uv run` manages the local environment.

Verification: `uv run --locked --offline python -m unittest -v` passed 18 tests,
including a real loopback callback with a fake OpenAI provider, signed-token
validation and rejection, registration retention after a failed exchange,
metadata-only persistence, account model selection, and streaming terminal
states. The CLI help command also passed. A reviewer found hidden safe error
details and missing login wiring tests; both were addressed. No real account or
plan quota was used during these checks.

The original milestone authorized each invocation in the browser; reusable
login now follows [remember-login.md](remember-login.md). Conversation persistence and the
terminal UI are now covered by [interactive-cli.md](interactive-cli.md).
Account switching UI, tools, and an agent loop remain later lessons.
Only one process per project is supported; concurrent runs are rejected.

Sources (checked 2026-10-15):
- https://developers.openai.com/siwc/token-sharing-open-source/sign-in
- https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
- https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations
