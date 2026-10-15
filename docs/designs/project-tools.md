# Project tools

Status: implemented and verified offline (2026-10-15). The user approved adding the previously discussed
read/search/edit/shell tools to the Python harness. Existing OAuth, terminal
interaction and SQLite conversation replay remain in use.

## Observable contract

- Each Responses request advertises four custom function tools: `read_file`,
  `grep`, `apply_patch`, and `run_shell`. A completed tool-call response is
  appended with all its output items (including opaque reasoning), followed by
  matching `function_call_output` items, before the next request. Execute calls
  sequentially; reject malformed/unknown calls as tool errors. Never execute an
  incomplete stream. Bound a turn to ten model requests and forty tool calls;
  never retry a completed action automatically.
- File paths are relative to the current working directory. Dedicated file
  tools reject traversal, absolute paths, symlinks, non-regular files, and
  private/internal paths (`.harness`, `.git`, `.env*`, `.venv`, `node_modules`),
  including case variants on case-insensitive filesystems.
  Reads have a line window and a file-size limit. Search uses ripgrep with
  bounded output, respects ignore rules, excludes private paths and does not
  follow symlinks. Missing ripgrep produces an actionable tool error.
- `apply_patch` is a custom single-file exact-text edit, not the OpenAI built-in
  patch format: path plus nullable old/new text. Null old text creates a new
  file; null new text deletes only when old text equals the entire current
  file. An update requires one unique nonempty match. Display a unified diff
  before confirmation, recheck the snapshot after confirmation, then atomically
  replace the file preserving executable permissions. Decline changes nothing.
- `run_shell` displays the exact command and working directory before explicit
  per-call confirmation (default no); runs `/bin/bash -c` with closed stdin,
  bounded stdout/stderr and a 60-second limit. Timeout, output overflow or
  cancellation kills the process group and reaps the child. Normal completion
  also cleans remaining group members; background daemons are unsupported.
  Return exit code
  and failure reason to the model. Do not inherit credential environment vars.
  Bash runs with the user's OS permissions: cwd is NOT a security sandbox and
  shell commands can access files outside these dedicated file-tool limits.
- Read/search calls show activity; edits and commands stop the live Markdown
  display while asking for confirmation. Ctrl+C stops the current turn; EOF
  at confirmation declines. Noninteractive single-shot mode can read/search,
  but declines edits/commands without prompting on stdin.
- Successful turns preserve all model output, tool calls and tool results in
  the existing SQLite output JSON, in replay order. No schema migration is
  needed. Aggregate token usage over all requests; missing usage stays unknown.
  Failed/cancelled turns retain the existing no-context-commit behavior. Already
  approved file or shell side effects are not rolled back; explicitly warn
  about this when such a turn fails. Restart never executes stored calls.

## Decomposition and implementation sequence

Trigger: multiple related observable behaviors and the provider/persistence
boundary. Keep one coherent feature, implemented and verified in slices:

1. Dedicated tools: deterministic temporary-project read/search/edit tests,
   approval rejection and conflict checks, real small Bash scripts, output cap
   and process cancellation/timeout tests.
2. Provider loop: fake SSE sequences prove call/output ordering, arguments,
   per-request authorization refresh, bounded execution, malformed-call errors,
   no execution on incomplete streams, and combined usage/output.
3. Terminal + persistence: interactive confirmation and noninteractive denial,
   SQLite reopen/replay of full tool transcripts, interruption diagnostics;
   retain the existing OAuth/chat regression suite.

Use the current checkout: the repository has no initial commit, so a linked
worktree cannot preserve the existing untracked project. No dependency additions
or commits are part of this task.

## Delivery evidence

The full offline suite passed 106 tests. Tool and provider scenarios were
observed failing before implementation or their corresponding fixes, then
passing. A real PromptSession pipe-input test confirms an approved file creation;
a real temporary SQLite reopen test confirms shell-call/result replay without
reexecution. Tests use synthetic credentials/provider responses and temporary
projects; live account inference/tool selection remains unverified.

Fresh-context review found private-path case bypasses, malformed output/streamed
item crashes, surviving redirected background children and weak missing-target
fixtures. All were fixed and rechecked by the same reviewer. Full regression
also exposed a duplicate-cleanup race; a deterministic denied-second-cleanup
test and 30 output-limit/30 normal-exit probes verify idempotent termination.
No material findings remain deferred.

Decomposition decision: keep one feature. The final task exceeds 1,000 changed
lines including tests, README and design/plan records; earlier OAuth/UI source
is excluded using the pre-task snapshot. Read/search, approved edits, bounded
processes and provider continuation jointly deliver the requested coding loop.
Verification maps to `ToolTests`, `ToolLoopTests`, `CLIToolTests` and `UITests`;
the review explicitly strengthened path/symlink fixtures and process cleanup.
No generated/lock files changed and no additional approval gate is required.

References (checked 2026-10-15):
- https://developers.openai.com/api/docs/guides/function-calling
- https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
