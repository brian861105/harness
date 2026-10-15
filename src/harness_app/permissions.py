"""Present proposed edits/commands and require explicit interactive approval."""

import sys

from rich.console import Console
from rich.panel import Panel
from rich.text import Text


def confirm_tool(name, preview, console=None, reader=None):
    console = console or Console(stderr=True)
    # Escape controls so the preview cannot hide bytes of the proposed command.
    visible = ''.join(char if char.isprintable() or char in '\n\t' else repr(char)[1:-1]
                      for char in preview)
    console.print(Panel(Text(visible), title=name, border_style='yellow'))
    if reader is None:
        if not sys.stdin.isatty():
            console.print('非互動輸入：已拒絕修改或執行命令。', style='yellow')
            return False
        reader = lambda: input('允許這次操作？[y/N] ')
    try:
        return reader().strip().lower() in ('y', 'yes')
    except EOFError:
        return False
