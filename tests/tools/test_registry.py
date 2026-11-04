from tests.tools.support import ProjectTestCase


class RegistryTests(ProjectTestCase):
    def test_unknown_tool_or_invalid_arguments_return_errors(self):
        self.write('app.py', 'print(1)\n')
        self.assertNotIn('load_skill', [tool['name'] for tool in self.tools.definitions])
        for name, arguments in [('unknown', {}), ('read_file', []),
                                ('load_skill', {'name': 'any', 'path': None}),
                                ('run_shell', {'command': 12}),
                                ('read_file', {'path': 'app.py', 'start_line': True})]:
            with self.subTest(name=name, arguments=arguments):
                self.assertFalse(self.tools.execute(name, arguments)['ok'])
