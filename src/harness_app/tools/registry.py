"""Model-facing tool schemas and dispatch to their owning implementations."""

from ..errors import HarnessError
from .files import FileTools
from .shell import ShellTool


def function(name, description, properties):
    return {'type': 'function', 'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': properties,
                           'required': list(properties), 'additionalProperties': False},
            'strict': True}


TOOLS = [
    function('read_file', 'Read UTF-8 project code with line numbers; maximum 200 lines per call.', {
        'path': {'type': 'string'}, 'start_line': {'type': 'integer', 'minimum': 1},
        'end_line': {'type': 'integer', 'minimum': 1}}),
    function('grep', 'Search project code using a ripgrep regex; returns path:line:content. Use path="." for the project.', {
        'pattern': {'type': 'string'}, 'path': {'type': 'string'}}),
    function('apply_patch', 'Propose a single-file exact-text edit for user approval. old_text=null creates; '
             'new_text=null deletes only if old_text is the entire file; otherwise replace one unique match.', {
                 'path': {'type': 'string'}, 'old_text': {'type': ['string', 'null']},
                 'new_text': {'type': ['string', 'null']}}),
    function('run_shell', 'Propose a Bash command for user approval, executed in the project. '
             'Can run scripts with bash ./script.sh. No interactive input; 60-second timeout.', {
                 'command': {'type': 'string'}}),
]


class ProjectTools:
    definitions = TOOLS

    def __init__(self, workspace, approve=None, notify=None):
        self.files = FileTools(workspace, approve, notify)
        self.shell = ShellTool(workspace, approve)

    @property
    def side_effects(self):
        return self.files.side_effects or self.shell.side_effects

    def execute(self, name, arguments):
        handlers = {'read_file': self.files.read_file, 'grep': self.files.grep,
                    'apply_patch': self.files.apply_patch, 'run_shell': self.shell.run_shell}
        try:
            if not isinstance(name, str) or name not in handlers or not isinstance(arguments, dict):
                raise HarnessError('未知工具或參數格式不正確。')
            return handlers[name](**arguments)
        except HarnessError as error:
            return {'ok': False, 'error': str(error)}
        except (TypeError, ValueError):
            return {'ok': False, 'error': '工具參數或 UTF-8 檔案格式不正確。'}
        except OSError:
            return {'ok': False, 'error': '工具執行失敗；請檢查路徑、權限與本機程式。'}
