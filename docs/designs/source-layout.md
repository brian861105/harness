# Source layout

Status: implemented and verified offline (2026-10-15). The user wants the existing harness organized so
code is easy to find. This is the implementation of the discussed responsibility
split, with existing behavior and data locations preserved.

## Boundaries

- Source lives under `src/harness_app/`; the name avoids collision with the
  existing root `harness.py` launcher. Tests live under `tests/`, grouped by the
  same responsibilities. `uv run harness.py` remains supported.
- `cli.py` assembles the application and handles command-line errors. `agent.py`
  owns the tool-call loop. `provider.py` owns model selection and HTTP/SSE.
  `auth/login.py` owns browser sign-in; `auth/session.py` owns saved credentials.
  `store.py` owns SQLite; `ui.py` owns terminal interaction; `permissions.py`
  owns interactive approval presentation/default denial. Shared endpoints and
  limits have explicit homes in `config.py` and `tools/limits.py`.
- `tools/registry.py` owns schemas and dispatch. `tools/files.py` owns read,
  search, exact edits and file policy. `tools/shell.py` owns Bash/process cleanup.
  Tools receive approval/activity callbacks; they do not import terminal UI.
  Agent/provider/auth do not import UI or the CLI. Remove the existing circular
  script/UI import and duplicate endpoint constants.
- Keep OAuth validation, credential permissions/rotation, complete transcript
  replay, tool safeguards, approval behavior, cancellation and diagnostics.
  Keep the source checkout's `.harness/` state location; no data migration,
  real login, credential inspection, new provider or extra agent feature.
- Use a standard editable `uv` package so imports work from another working
  directory. Provide `harness` and `python -m harness_app` entrypoints alongside
  the original launcher. Add only a build backend, with no runtime dependency.
- README becomes the navigation entry with a feature-to-file map and tree.
  Current architecture is documented separately from historical milestone notes.

## Decomposition and verification

Keep one coherent layout refactor. Sequence source extraction, caller/test
imports, then documentation and packaging verification. The total textual diff
will exceed 1,000 lines because files move; count unchanged function/class bodies
as moves, not new behavior. Existing 106 observable regression tests are the
behavior contract. Preserve their assertions, regroup by responsibility and
update mock targets to the owning module. New packaging/entrypoint behavior gets
fresh-process smoke checks, including another cwd and clean editable install.
The existing independent reviewer checks move fidelity, state-path compatibility,
import direction and test discovery. No commit/worktree is needed: the repository
has no initial commit and all existing source is untracked.

## Delivery evidence

- All 106 original scenario names remain, and the default
  `uv run --locked --offline python -m unittest -q` passes all 106 tests.
  Assertions are preserved; the launcher error test now exercises the actual
  script exit and displayed model guidance rather than duplicate module identity.
- AST comparison confirms unchanged runtime function bodies apart from the
  documented callee rename, CLI argument/state injection and tool delegation.
  Runtime source changes from about 1,050 to 1,137 lines; the larger textual
  add/delete diff consists primarily of verified file and test moves.
- A clean temporary source copy installs offline in editable mode. Root launcher,
  module and console help work from another cwd without creating auth state.
  Independent isolated installation and entrypoint checks also pass.
- Headless agent/tool imports do not load UI or CLI. The default state path
  remains the original source checkout's `.harness/`.
- README/architecture/index local links resolve. The same reviewer found no
  actionable regression and confirmed the decomposition decision to keep this
  coherent layout refactor. No real credentials or SQLite were read/migrated.

Live OAuth/inference and global wheel installation were not exercised; use is
documented as source-checkout execution through uv.
