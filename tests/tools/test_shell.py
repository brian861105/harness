import os
import signal
from unittest.mock import patch

from tests.tools.support import ProjectTestCase


class ShellTests(ProjectTestCase):
    def test_shell_executes_a_script_in_the_project_and_returns_both_streams(self):
        self.write('test.sh', 'printf "hello"; printf "problem" >&2; exit 3\n')
        result = self.tools.execute('run_shell', {'command': 'bash ./test.sh'})
        self.assertEqual((result['stdout'], result['stderr'], result['exit_code']), ('hello', 'problem', 3))
        self.assertIn('bash ./test.sh', self.approve.call_args.args[1])

    def test_declined_shell_does_not_execute_the_command(self):
        self.approve.return_value = False
        result = self.tools.execute('run_shell', {'command': 'touch marker'})
        self.assertFalse(result['ok'])
        self.assertFalse((self.root / 'marker').exists())

    def test_shell_does_not_inherit_provider_credentials(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fake-secret', 'BASH_ENV': '/absent'}):
            result = self.tools.execute('run_shell', {'command': 'printf "%s" "${OPENAI_API_KEY-unset}"'})
        self.assertEqual(result['stdout'], 'unset')

    def test_shell_output_limit_stops_an_unbounded_command(self):
        with patch('harness_app.tools.shell.OUTPUT_LIMIT', 128):
            result = self.tools.execute('run_shell', {'command': 'while :; do printf 1234567890; done'})
        self.assertEqual(result['reason'], 'output_limit')
        self.assertLessEqual(len(result['stdout'].encode()) + len(result['stderr'].encode()), 128)

    def test_output_limit_is_reported_when_a_second_cleanup_would_be_denied(self):
        killpg = os.killpg
        killed = False
        def kill_once(pid, sig):
            nonlocal killed
            if killed:
                raise PermissionError('process group is already gone')
            killed = True
            return killpg(pid, sig)
        with patch('harness_app.tools.shell.OUTPUT_LIMIT', 128), patch('harness_app.tools.shell.os.killpg', side_effect=kill_once):
            result = self.tools.execute('run_shell', {'command': 'while :; do printf 1234567890; done'})
        self.assertEqual(result.get('reason'), 'output_limit')

    def test_shell_timeout_kills_the_background_child(self):
        with patch('harness_app.tools.shell.PROCESS_TIMEOUT', 0.15):
            result = self.tools.execute('run_shell', {
                'command': 'sleep 10 & child=$!; echo "$child" > child.pid; wait'})
        self.assertEqual(result['reason'], 'timeout')
        pid = int((self.root / 'child.pid').read_text())
        # A killed orphan can briefly remain a zombie; it must not be running.
        import subprocess
        state = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
        self.assertTrue(not state or state.startswith('Z'), state)

    def test_successful_shell_does_not_leave_a_redirected_background_child(self):
        import subprocess
        result = self.tools.execute('run_shell', {
            'command': 'sleep 10 </dev/null >/dev/null 2>&1 & echo $! > child.pid'})
        pid = int((self.root / 'child.pid').read_text())
        try:
            state = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
        finally:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.assertTrue(result['ok'])
        self.assertTrue(not state or state.startswith('Z'), state)

    def test_shell_interrupt_terminates_the_child_before_propagating(self):
        import selectors
        real_select = selectors.DefaultSelector.select
        def interrupt(selector, timeout=None):
            events = real_select(selector, timeout)
            if not events:
                return events
            raise KeyboardInterrupt()
        with patch.object(selectors.DefaultSelector, 'select', interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.tools.execute('run_shell', {'command': 'echo $$ > shell.pid; echo ready; sleep 10'})
        pid = int((self.root / 'shell.pid').read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
