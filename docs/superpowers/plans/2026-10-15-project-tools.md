# Project tools implementation

Contract: [project-tools.md](../../designs/project-tools.md).
Execution: inline; retain the current checkout and use standard-library adapters.

- [x] Prove read/search/edit/shell behavior through `ProjectTools.execute()` in
  `test_project_tools.py`, then implement `project_tools.py`. Confirm red/green.
- [x] Prove the tool loop through `ask(..., tools=...)` in `test_harness.py`, then
  extend request construction and replay in `harness.py`; preserve no-tool callers.
- [x] Wire confirmations and activity into `chat_ui.py` and the CLI entrypoint;
  verify real SQLite transcript replay and cancellation behavior in existing tests.
- [x] Update README with tool use, exact-edit format, confirmation behavior,
  process limits and persistence semantics. Run full offline tests, review the
  feature and its tests, address material findings, and record final evidence.

Task baseline: 58 offline tests passed. Snapshot for review/diff measurement:
`/private/tmp/harness-tools-before-xhh548yh` (source/docs only; no credentials).

Final evidence: 106 offline tests passed; CLI help and Python compilation passed.
Fresh-context review and its follow-ups found no remaining material issue after
the private-path, malformed-response, cleanup and fixture fixes. No live account
login/inference was exercised. See the contract's delivery evidence for mapping.
