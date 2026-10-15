import tempfile
import unittest
from pathlib import Path

from harness_app.store import ChatStore


OUTPUT = [
    {'type': 'reasoning', 'id': 'r1', 'summary': [], 'encrypted_content': 'opaque'},
    {'type': 'message', 'id': 'm1', 'role': 'assistant',
     'content': [{'type': 'output_text', 'text': 'Hello', 'annotations': []}]},
]
RESPONSE = {'status': 'completed', 'output': OUTPUT,
            'usage': {'input_tokens': 9, 'output_tokens': 3, 'total_tokens': 12}}


class StoreTests(unittest.TestCase):
    def test_reopen_resumes_session_and_preserves_replay_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'chat.sqlite3'
            with ChatStore(path) as store:
                session = store.session('account-a', '/project', 'model-a')
                store.append_turn(session['id'], 'Hi', RESPONSE, 'model-a')
            with ChatStore(path) as store:
                resumed = store.session('account-a', '/project', 'model-b')
                self.assertEqual(resumed['id'], session['id'])
                self.assertEqual(store.inputs(resumed['id']),
                                 [{'role': 'user', 'content': 'Hi'}] + OUTPUT)

    def test_other_accounts_and_workspaces_do_not_resume_existing_context(self):
        with ChatStore(':memory:') as store:
            original = store.session('account-a', '/project', 'model')
            store.append_turn(original['id'], 'private prompt', RESPONSE, 'model')
            for owner, workspace in [('account-b', '/project'), ('account-a', '/other')]:
                with self.subTest(owner=owner, workspace=workspace):
                    session = store.session(owner, workspace, 'model')
                    self.assertEqual(store.inputs(session['id']), [])
                    self.assertEqual(store.prompts(owner, workspace), [])

    def test_new_session_keeps_previous_conversation_but_becomes_default(self):
        with ChatStore(':memory:') as store:
            first = store.session('account', '/project', 'model')
            store.append_turn(first['id'], 'Hi', RESPONSE, 'model')
            fresh = store.session('account', '/project', 'model', new=True)
            self.assertEqual(store.inputs(fresh['id']), [])
            self.assertEqual(store.session('account', '/project', 'model')['id'], fresh['id'])
            self.assertEqual(store.inputs(first['id']), [{'role': 'user', 'content': 'Hi'}] + OUTPUT)

    def test_incomplete_response_does_not_record_a_user_prompt(self):
        with ChatStore(':memory:') as store:
            session = store.session('account', '/project', 'model')
            with self.assertRaises(ValueError):
                store.append_turn(session['id'], 'unfinished', {'status': 'incomplete'}, 'model')
            self.assertEqual(store.inputs(session['id']), [])

    def test_unknown_usage_stays_unknown(self):
        with ChatStore(':memory:') as store:
            session = store.session('account', '/project', 'model')
            store.append_turn(session['id'], 'Hi', {'status': 'completed', 'output': OUTPUT}, 'model')
            self.assertIsNone(store.turns(session['id'])[0]['usage'])

    def test_model_change_survives_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'chat.sqlite3'
            with ChatStore(path) as store:
                session = store.session('account', '/project', 'model-a')
                store.set_model(session['id'], 'model-b')
            with ChatStore(path) as store:
                self.assertEqual(store.session('account', '/project', 'fallback')['model'], 'model-b')


if __name__ == '__main__':
    unittest.main()
