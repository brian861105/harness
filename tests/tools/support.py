"""Temporary project fixture; never reads real harness state."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from harness_app.tools.registry import ProjectTools


class ProjectTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.approve = Mock(return_value=True)
        self.tools = ProjectTools(self.root, approve=self.approve)

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return target
