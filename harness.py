"""Launch the installed harness package with uv run harness.py."""

from harness_app.cli import run


if __name__ == '__main__':
    raise SystemExit(run())
