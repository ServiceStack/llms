import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    'recover_project_threads', Path(__file__).parents[1] / 'scripts/recover-project-threads.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class ProjectRecoveryTests(unittest.TestCase):
    def test_requires_unambiguous_tool_workspace_evidence(self):
        directories = {'/projects/a': 'a', '/projects/b': 'b'}
        a = {'role': 'tool', 'content': 'Allowed directories:\n/projects/a'}
        b = {'role': 'tool', 'content': 'Allowed directories:\n/projects/b'}
        self.assertEqual(recovery.recover_project([a, a], directories), 'a')
        self.assertIsNone(recovery.recover_project([a, b], directories))
        self.assertIsNone(recovery.recover_project([{**a, 'role': 'assistant'}], directories))
        self.assertIsNone(recovery.recover_project([
            {**a, 'content': 'Allowed directories:\n/projects/ab'}], directories))
        self.assertEqual(recovery.recover_project([
            {**a, 'content': 'Access denied: /tmp/x is not within allowed directories:\n/projects/a'}],
            directories), 'a')
