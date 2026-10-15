# Repository conventions

- Write all repository documentation in English, including README files,
  architecture guides, behavior contracts, and implementation notes.
- Write commit titles and bodies in English. Titles describe concrete changes;
  bodies explain the actual motivation and maintenance tradeoffs of the staged diff.
- Preserve the current language of runtime messages unless a task requests a change.
- Use uv for environment management and test execution. The full regression
  command is `uv run python -m unittest -v`.
- Keep credentials, conversations, virtual environments, and IDE state out of Git.
