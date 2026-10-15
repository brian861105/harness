# Source layout implementation

Goal: make existing functionality easy to locate without changing its behavior.
Contract: [source-layout.md](../../designs/source-layout.md).
Execution: inline; reuse the existing independent reviewer for the final review.

- [x] Extract source into `src/harness_app`, keeping a thin root launcher.
  Separate browser auth/session, provider/agent/CLI, UI/approval/store, and
  file/shell/registry tools. Preserve code bodies and shared error identity.
- [x] Regroup existing tests in `tests/`; update imports/mock targets and retain
  all existing behavior scenarios. Verify default unittest discovery.
- [x] Configure editable uv packaging and verify original launcher, module and
  console command from root and another cwd, with synthetic-only integration.
- [x] Replace README reading order with a feature-to-file map; add current
  architecture/docs index. Check links and remove outdated extension directions.
- [x] Run full offline regression and independent review; resolve material
  findings and record verification.

Baseline: 106 tests passed. Safe source snapshot for move comparison:
`/private/tmp/harness-layout-before-og1uux6x` (no credentials or SQLite).

Result: 106/106 tests pass with default discovery; all original scenario names
retained. Clean/isolated editable installation and launcher/module/console help
checks pass from another cwd. Navigation links resolve. Independent review found
no actionable issue; no production state or live inference was exercised.
