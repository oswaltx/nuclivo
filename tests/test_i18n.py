import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
sys.path.insert(0, str(SRC))
from nuclivo_i18n import EN, set_language, tr

class LanguageTests(unittest.TestCase):
    def test_translation_catalog_covers_marked_ui_text(self):
        tree = ast.parse((SRC / 'nuclivo.py').read_text())
        missing = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == '_' and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value not in EN):
                missing.append((node.lineno, node.args[0].value))
        self.assertEqual(missing, [])

    def test_startup_uses_saved_language(self):
        with tempfile.TemporaryDirectory() as config:
            settings = Path(config) / 'nuclivo' / 'settings.json'
            settings.parent.mkdir()
            settings.write_text(json.dumps({'language': 'en'}))
            env = dict(os.environ, XDG_CONFIG_HOME=config, LANG='de_DE.UTF-8')
            result = subprocess.run([sys.executable, '-c',
                'import nuclivo; print(nuclivo.SECTIONS["my-files"][1]); print(nuclivo._("Abmelden"))'],
                env=dict(env, PYTHONPATH=str(SRC)), capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.splitlines(), ['My files', 'Sign out'])

    def test_selection_keeps_german_available(self):
        set_language('de')
        self.assertEqual(tr('Meine Dateien'), 'Meine Dateien')
        set_language('en')
        self.assertEqual(tr('Meine Dateien'), 'My files')
        set_language('de')

if __name__ == '__main__':
    unittest.main()
