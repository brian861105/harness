"""SQLite conversation persistence for the terminal lesson."""

import json
import os
import sqlite3
import uuid
from pathlib import Path


class ChatStore:
    def __init__(self, path):
        if str(path) != ':memory:':
            path = Path(path)
            if path.is_symlink():
                raise ValueError('對話資料庫不可使用符號連結。')
            fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys = ON')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                workspace TEXT NOT NULL,
                model TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS turns (
                id INTEGER PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id),
                prompt TEXT NOT NULL,
                output_json TEXT NOT NULL,
                usage_json TEXT,
                model TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS turns_session ON turns(session_id, id);
        ''')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()

    def session(self, owner, workspace, model, new=False):
        if not new:
            row = self.db.execute('''
                SELECT * FROM sessions WHERE owner=? AND workspace=? ORDER BY rowid DESC LIMIT 1
            ''', (owner, workspace)).fetchone()
            if row:
                return dict(row)
        result = {'id': uuid.uuid4().hex, 'owner': owner, 'workspace': workspace, 'model': model}
        with self.db:
            self.db.execute('INSERT INTO sessions VALUES (?, ?, ?, ?)', tuple(result.values()))
        return result

    def turns(self, session_id):
        rows = self.db.execute('SELECT * FROM turns WHERE session_id=? ORDER BY id', (session_id,))
        return [{'prompt': row['prompt'], 'output': json.loads(row['output_json']),
                 'usage': json.loads(row['usage_json']) if row['usage_json'] else None,
                 'model': row['model']} for row in rows]

    def inputs(self, session_id):
        items = []
        for turn in self.turns(session_id):
            items.append({'role': 'user', 'content': turn['prompt']})
            items.extend(turn['output'])
        return items

    def append_turn(self, session_id, prompt, response, model):
        if response.get('status') != 'completed' or not isinstance(response.get('output'), list):
            raise ValueError('只能儲存完整完成的回應。')
        with self.db:
            self.db.execute('''
                INSERT INTO turns(session_id, prompt, output_json, usage_json, model)
                VALUES (?, ?, ?, ?, ?)
            ''', (session_id, prompt, json.dumps(response['output'], ensure_ascii=False),
                  json.dumps(response['usage']) if response.get('usage') is not None else None, model))

    def set_model(self, session_id, model):
        with self.db:
            self.db.execute('UPDATE sessions SET model=? WHERE id=?', (model, session_id))

    def prompts(self, owner, workspace):
        return [row['prompt'] for row in self.db.execute('''
            SELECT turns.prompt FROM turns JOIN sessions ON turns.session_id=sessions.id
            WHERE sessions.owner=? AND sessions.workspace=? ORDER BY turns.id
        ''', (owner, workspace))]
