import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness_app.context import BASE_INSTRUCTIONS, load_context
from harness_app.errors import HarnessError
from harness_app.skills import discover_skills, read_skill_file, skill_prompt, typed_prompt


def write_skill(root, directory, frontmatter, body='Do the task.\n'):
    path = Path(root) / directory / 'SKILL.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('---\n' + frontmatter + '\n---\n' + body)
    return path


class SkillTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_valid_skills_load_and_invalid_ones_only_warn(self):
        write_skill(self.root, 'pdf', 'name: pdf-tools\ndescription: >-\n  Read PDFs.\n  Use for PDF files.')
        write_skill(self.root, 'manual', 'name: manual\ndescription: Manual only.\n'
                    'disable-model-invocation: true')
        cases = {'bad-name': 'name: Bad_Name\ndescription: x', 'no-description': 'name: empty',
                 'long': 'name: long\ndescription: ' + 'x' * 1025, 'yaml': 'name: [unclosed',
                 'flag': 'name: flag\ndescription: x\ndisable-model-invocation: yes please',
                 'large': 'name: large\ndescription: x\nnotes: ' + 'x' * 33000}
        for directory, frontmatter in cases.items():
            write_skill(self.root, directory, frontmatter)
        (self.root / 'nofront').mkdir()
        (self.root / 'nofront' / 'SKILL.md').write_text('# no frontmatter\n')
        skills, warnings = discover_skills([self.root, self.root / 'missing'])
        self.assertEqual(sorted(skills), ['manual', 'pdf-tools'])
        self.assertEqual(skills['pdf-tools']['description'], 'Read PDFs. Use for PDF files.')
        self.assertFalse(skills['manual']['model_invocable'])
        self.assertEqual(len(warnings), len(cases) + 1)

    def test_earlier_root_wins_a_name_collision(self):
        project, user = self.root / 'project', self.root / 'user'
        write_skill(project, 'a', 'name: shared\ndescription: project copy')
        write_skill(user, 'b', 'name: shared\ndescription: user copy')
        skills, warnings = discover_skills([project, user])
        self.assertEqual(skills['shared']['description'], 'project copy')
        self.assertIn('重複', warnings[0])

    def test_skill_files_are_bounded_to_the_skill_directory(self):
        write_skill(self.root, 'tool', 'name: tool\ndescription: x')
        (self.root / 'tool' / 'references').mkdir()
        (self.root / 'tool' / 'references' / 'api.md').write_text('API notes\n')
        (self.root / 'secret.txt').write_text('outside\n')
        os.symlink(self.root / 'secret.txt', self.root / 'tool' / 'escape.txt')
        skill = discover_skills([self.root])[0]['tool']
        loaded = read_skill_file(skill)
        self.assertIn('Do the task.', loaded['content'])
        self.assertEqual(loaded['files'], ['SKILL.md', 'escape.txt', 'references/api.md'])
        self.assertEqual(read_skill_file(skill, 'references/api.md')['content'], 'API notes\n')
        for path in ['../secret.txt', 'escape.txt', str(self.root / 'secret.txt'), 'missing.md', '']:
            with self.subTest(path=path), self.assertRaises(HarnessError):
                read_skill_file(skill, path)

    def test_private_files_are_neither_listed_nor_readable(self):
        write_skill(self.root, 'tool', 'name: tool\ndescription: x')
        for path in ['.env', '.git/config', 'node_modules/pkg/index.js', 'NODE_MODULES/x.js']:
            target = self.root / 'tool' / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('secret\n')
        os.symlink('.env', self.root / 'tool' / 'alias.txt')
        skill = discover_skills([self.root])[0]['tool']
        self.assertEqual(read_skill_file(skill)['files'], ['SKILL.md', 'alias.txt'])
        for path in ['.env', '.git/config', 'node_modules/pkg/index.js', 'NODE_MODULES/x.js', 'alias.txt']:
            with self.subTest(path=path), self.assertRaises(HarnessError):
                read_skill_file(skill, path)

    def test_unreadable_skill_root_is_a_warning(self):
        with patch.object(Path, 'glob', side_effect=PermissionError()):
            skills, warnings = discover_skills([self.root])
        self.assertEqual(skills, {})
        self.assertIn('無法讀取 skill 目錄', warnings[0])

    def test_skill_that_grew_past_the_limit_is_not_attached_partially(self):
        manifest = write_skill(self.root, 'tool', 'name: tool\ndescription: x')
        skill = discover_skills([self.root])[0]['tool']
        manifest.write_text(manifest.read_text() + 'x' * 33000)
        with self.assertRaises(HarnessError):
            skill_prompt('/skill:tool', skill)

    def test_explicit_invocation_keeps_typed_text_for_display(self):
        write_skill(self.root, 'tool', 'name: tool\ndescription: x', 'Step one.\n')
        skill = discover_skills([self.root])[0]['tool']
        prompt = skill_prompt('/skill:tool fix\nthe bug', skill)
        self.assertIn('Step one.', prompt)
        self.assertEqual(typed_prompt(prompt), '/skill:tool fix\nthe bug')
        quoted = '/skill:tool explain\n\n<skill name="tool" directory="x">'
        self.assertEqual(typed_prompt(skill_prompt(quoted, skill)), quoted)
        self.assertEqual(typed_prompt('plain\n\n<skill name="x">'), 'plain\n\n<skill name="x">')


class ContextTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_agents_and_visible_skills_extend_the_base_instructions(self):
        (self.root / 'AGENTS.md').write_text('Use uv.\n')
        write_skill(self.root / '.agents' / 'skills', 'a', 'name: auto\ndescription: Auto skill.')
        write_skill(self.root / 'home', 'b', 'name: hidden\ndescription: Hidden.\n'
                    'disable-model-invocation: true')
        context = load_context(self.root, self.root / 'home')
        self.assertTrue(context['instructions'].startswith(BASE_INSTRUCTIONS))
        self.assertIn('Project instructions from AGENTS.md:\nUse uv.', context['instructions'])
        self.assertIn('- auto: Auto skill.', context['instructions'])
        self.assertNotIn('hidden', context['instructions'])
        self.assertEqual(sorted(context['skills']), ['auto', 'hidden'])

    def test_home_workspace_does_not_load_user_skills_twice(self):
        write_skill(self.root / '.agents' / 'skills', 'a', 'name: auto\ndescription: Auto skill.')
        context = load_context(self.root, self.root / '.agents' / 'skills')
        self.assertEqual((list(context['skills']), context['warnings']), (['auto'], []))

    def test_missing_context_uses_only_the_base_instructions(self):
        context = load_context(self.root)
        self.assertEqual(context, {'instructions': BASE_INSTRUCTIONS, 'agents': False,
                                   'skills': {}, 'warnings': []})

    def test_unsafe_or_oversized_agents_file_is_skipped_with_a_warning(self):
        outside = self.root / 'outside.md'
        outside.write_text('secret\n')
        workspace = self.root / 'project'
        workspace.mkdir()
        os.symlink(outside, workspace / 'AGENTS.md')
        context = load_context(workspace)
        self.assertEqual(context['instructions'], BASE_INSTRUCTIONS)
        self.assertEqual(len(context['warnings']), 1)
        (workspace / 'AGENTS.md').unlink()
        (workspace / 'AGENTS.md').write_text('x' * 40000)
        self.assertIn('32 KiB', load_context(workspace)['warnings'][0])

    def test_agents_symlink_inside_the_workspace_is_allowed(self):
        (self.root / 'CLAUDE.md').write_text('Shared rules.\n')
        os.symlink('CLAUDE.md', self.root / 'AGENTS.md')
        self.assertIn('Shared rules.', load_context(self.root)['instructions'])
