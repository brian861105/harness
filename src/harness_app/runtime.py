"""Session state and completed-turn orchestration, independent of the terminal."""

from .agent import ask
from .errors import HarnessError
from .provider import choose_model
from .tools.registry import ProjectTools


class ChatRuntime:
    def __init__(self, client, access_token, catalog, store, owner, workspace,
                 requested_model=None):
        self._client = client
        self._access_token = access_token
        self._store = store
        self._owner = owner
        self.catalog = catalog
        self.workspace = workspace
        default_model = choose_model(catalog, requested_model)
        session = store.session(owner, workspace, default_model)
        self.session_id = session['id']
        self.model_fallback = False
        try:
            model = choose_model(catalog, requested_model or session['model'])
        except HarnessError:
            model = default_model
            self.model_fallback = True
        self.set_model(model)
        turns = self.turns()
        self.last_usage = turns[-1]['usage'] if turns else None
        self.side_effects = False

    def turns(self):
        return self._store.turns(self.session_id)

    def prompts(self):
        return self._store.prompts(self._owner, self.workspace)

    def set_model(self, model):
        model = choose_model(self.catalog, model)
        self._store.set_model(self.session_id, model)
        self.model = model

    def new_session(self):
        session = self._store.session(self._owner, self.workspace, self.model, new=True)
        self.session_id = session['id']
        self.last_usage = None
        self.side_effects = False

    def run_turn(self, prompt, output, approve=None, notify=None):
        self.side_effects = False
        tools = ProjectTools(self.workspace, approve=approve, notify=notify)
        try:
            response = ask(self._client, self._access_token, prompt, self.model, output,
                           history=self._store.inputs(self.session_id), tools=tools)
            self._store.append_turn(self.session_id, prompt, response, self.model)
            self.last_usage = response.get('usage')
            return response
        finally:
            self.side_effects = tools.side_effects
