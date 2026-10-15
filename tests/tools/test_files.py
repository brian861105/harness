import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from harness_app.tools.registry import ProjectTools
from tests.tools.support import ProjectTestCase


class FileTests(ProjectTestCase):
    def test_reads_requested_code_lines_with_line_numbers(self):
        self.write('app.py', 'first\nsecond\nthird\n')
        result = self.tools.execute('read_file', {'path': 'app.py', 'start_line': 2, 'end_line': 2})
        self.assertEqual(result['content'], '2: second\n')
        self.approve.assert_not_called()

    def test_read_output_is_bounded_even_for_a_single_long_line(self):
        self.write('app.py', 'x' * 1000)
        with patch('harness_app.tools.files.OUTPUT_LIMIT', 128):
            result = self.tools.execute('read_file', {'path': 'app.py'})
        self.assertLessEqual(len(result['content'].encode()), 128)
        self.assertTrue(result['truncated'])

    def test_diff_distinguishes_lines_when_the_file_has_no_final_newline(self):
        self.write('app.py', 'before')
        self.tools.execute('apply_patch', {'path': 'app.py', 'old_text': 'before', 'new_text': 'after'})
        preview = self.approve.call_args.args[1]
        self.assertIn('-before\n', preview)
        self.assertIn('+after\n', preview)

    def test_read_file_rejects_paths_outside_project_or_private_state(self):
        self.write('.harness/credentials.json', 'fake-private-token')
        self.write('.env', 'fake-env-value')
        absolute = self.write('app.py', 'absolute-file-content')
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / 'outside.py'
            target.write_text('outside-file-content')
            for path in [os.path.relpath(target, self.root), str(absolute), '.harness/credentials.json', '.env']:
                with self.subTest(path=path):
                    result = self.tools.execute('read_file', {'path': path})
                    self.assertFalse(result['ok'])

    def test_private_path_case_variants_are_rejected_by_each_file_tool(self):
        secret = self.write('.harness/credentials.json', 'FAKE_PRIVATE_MARKER')
        actions = [('read_file', {'path': '.HARNESS/credentials.json'}),
                   ('grep', {'path': '.HARNESS/credentials.json', 'pattern': 'FAKE_PRIVATE'}),
                   ('apply_patch', {'path': '.HARNESS/credentials.json',
                                    'old_text': 'FAKE_PRIVATE_MARKER', 'new_text': 'changed'})]
        for name, arguments in actions:
            with self.subTest(name=name):
                result = self.tools.execute(name, arguments)
                self.assertFalse(result['ok'])
                self.assertNotIn('FAKE_PRIVATE_MARKER', json.dumps(result))
        self.assertEqual(secret.read_text(), 'FAKE_PRIVATE_MARKER')
        self.approve.assert_not_called()

    def test_symlink_cannot_bypass_file_tool_path_policy(self):
        target = self.write('.harness/credentials.json', 'fake-secret')
        (self.root / 'alias.py').symlink_to(target)
        self.assertFalse(self.tools.execute('read_file', {'path': 'alias.py'})['ok'])

    def test_parent_directory_symlink_cannot_expose_outside_code(self):
        with tempfile.TemporaryDirectory() as outside:
            (Path(outside) / 'app.py').write_text('outside-file-content')
            (self.root / 'linked').symlink_to(outside, target_is_directory=True)
            result = self.tools.execute('read_file', {'path': 'linked/app.py'})
            self.assertFalse(result['ok'])

    def test_search_returns_code_locations_without_private_or_symlink_content(self):
        self.write('src/app.py', 'import sqlite3\n')
        secret = self.write('.harness/credentials.json', 'sqlite3 private')
        self.write('nested/.harness/secret', 'sqlite3 private')
        self.write('other/.HARNESS/secret', 'sqlite3 private')
        self.write('.env', 'sqlite3 private')
        (self.root / 'alias.py').symlink_to(secret)
        result = self.tools.execute('grep', {'pattern': 'sqlite3', 'path': '.'})
        self.assertEqual(result['stdout'], './src/app.py:1:import sqlite3\n')

    def test_search_with_no_match_is_successful(self):
        self.write('app.py', 'hello\n')
        result = self.tools.execute('grep', {'pattern': 'absent', 'path': '.'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['stdout'], '')

    def test_search_reports_missing_ripgrep(self):
        with patch('harness_app.tools.files.shutil.which', return_value=None):
            result = self.tools.execute('grep', {'pattern': 'x', 'path': '.'})
        self.assertFalse(result['ok'])
        self.assertIn('ripgrep', result['error'])

    def test_confirmed_edit_shows_diff_and_preserves_executable_permission(self):
        path = self.write('test.sh', 'echo before\n')
        path.chmod(0o755)
        result = self.tools.execute('apply_patch', {
            'path': 'test.sh', 'old_text': 'before', 'new_text': 'after'})
        self.assertTrue(result['ok'])
        self.assertEqual(path.read_text(), 'echo after\n')
        self.assertEqual(path.stat().st_mode & 0o777, 0o755)
        name, preview = self.approve.call_args.args
        self.assertEqual(name, 'apply_patch')
        self.assertIn('-echo before', preview)
        self.assertIn('+echo after', preview)

    def test_declined_edit_leaves_file_unchanged(self):
        path = self.write('app.py', 'before')
        self.approve.return_value = False
        result = self.tools.execute('apply_patch', {'path': 'app.py', 'old_text': 'before', 'new_text': 'after'})
        self.assertFalse(result['ok'])
        self.assertEqual(path.read_text(), 'before')

    def test_edit_conflict_does_not_overwrite_changes_made_during_confirmation(self):
        path = self.write('app.py', 'before')
        def confirm(*args):
            path.write_text('human edit')
            return True
        self.tools = ProjectTools(self.root, approve=confirm)
        result = self.tools.execute('apply_patch', {'path': 'app.py', 'old_text': 'before', 'new_text': 'after'})
        self.assertFalse(result['ok'])
        self.assertEqual(path.read_text(), 'human edit')

    def test_ambiguous_edit_is_rejected_before_confirmation(self):
        path = self.write('app.py', 'same same')
        result = self.tools.execute('apply_patch', {'path': 'app.py', 'old_text': 'same', 'new_text': 'new'})
        self.assertFalse(result['ok'])
        self.assertEqual(path.read_text(), 'same same')
        self.approve.assert_not_called()

    def test_create_does_not_overwrite_an_existing_file(self):
        path = self.write('app.py', 'existing')
        result = self.tools.execute('apply_patch', {'path': 'app.py', 'old_text': None, 'new_text': 'new'})
        self.assertFalse(result['ok'])
        self.assertEqual(path.read_text(), 'existing')

    def test_confirmed_create_makes_a_new_code_file(self):
        result = self.tools.execute('apply_patch', {
            'path': 'src/new.py', 'old_text': None, 'new_text': 'print(1)\n'})
        self.assertTrue(result['ok'])
        self.assertEqual((self.root / 'src/new.py').read_text(), 'print(1)\n')

    def test_delete_requires_the_entire_expected_file_content(self):
        path = self.write('app.py', 'first\nsecond\n')
        refused = self.tools.execute('apply_patch', {'path': 'app.py', 'old_text': 'first', 'new_text': None})
        self.assertFalse(refused['ok'])
        result = self.tools.execute('apply_patch', {
            'path': 'app.py', 'old_text': 'first\nsecond\n', 'new_text': None})
        self.assertTrue(result['ok'])
        self.assertFalse(path.exists())
