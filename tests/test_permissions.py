import io
import unittest
from unittest.mock import patch

from rich.console import Console

from harness_app.permissions import confirm_tool


class PermissionTests(unittest.TestCase):
    def test_confirmation_displays_control_characters_without_hiding_them(self):
        rendered = io.StringIO()
        accepted = confirm_tool('run_shell', 'echo a\r\x1b[31m',
                               console=Console(file=rendered, color_system=None), reader=lambda: 'y')
        self.assertTrue(accepted)
        self.assertIn(r'\r\x1b[31m', rendered.getvalue())

    def test_noninteractive_confirmation_declines_without_reading_stdin(self):
        with patch('sys.stdin.isatty', return_value=False), \
                patch('builtins.input', side_effect=AssertionError('must not prompt')):
            self.assertFalse(confirm_tool('run_shell', 'echo hello', console=Console(file=io.StringIO())))
