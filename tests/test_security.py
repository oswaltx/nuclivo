import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from nuclivo_security import is_proton, photo_target, remote_path, safe_name, save_json

class SecurityTests(unittest.TestCase):
    def test_names(self):
        for name in ('../secret', '/etc/passwd', '..', '.', 'a/b', 'a\\b', 'bad\0name', ''):
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_name(name)
        self.assertEqual(safe_name('Bild 1.jpg'), 'Bild 1.jpg')

    def test_dates_cannot_escape(self):
        with tempfile.TemporaryDirectory() as root:
            for date in ('../../x', '/tmp/xx', '2026-99', '0000-01', None):
                self.assertEqual(photo_target(root, date), os.path.join(root, 'Unbekannt'))
            self.assertEqual(photo_target(root, '2026-10-03T12:00:00Z'), os.path.join(root, '2026', '10'))

    def test_symlink_destination_rejected(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            os.symlink(outside, os.path.join(root, '2026'))
            with self.assertRaises(ValueError):
                photo_target(root, '2026-10-03')
            self.assertEqual(os.listdir(outside), [])

    def test_private_atomic_json_and_symlink(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / 'private'
            path = folder / 'state.json'
            outside = Path(root) / 'outside'
            outside.write_text('untouched')
            folder.mkdir()
            path.symlink_to(outside)
            save_json(str(path), {'ok': True})
            self.assertEqual(outside.read_text(), 'untouched')
            self.assertEqual(json.loads(path.read_text()), {'ok': True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ['state.json'])

    def test_url_allowlist(self):
        self.assertTrue(is_proton('https://docs.proton.me/doc?mode=open'))
        for uri in ('http://account.proton.me', 'https://proton.me.evil.com',
                    'https://evil.proton.me', 'file:///tmp/x', 'javascript:alert(1)',
                    'https://user@account.proton.me', 'https://docs.proton.me:444',
                    'https://docs.proton.me:invalid'):
            self.assertFalse(is_proton(uri), uri)

    def test_remote_boundaries(self):
        self.assertEqual(remote_path('/my-files/a/b'), 'nuclivo-proton:a/b')
        self.assertEqual(remote_path('/my-files'), 'nuclivo-proton:')
        for remote in ('/my-files-other/x', '/shared-with-me/x', '/my-files/../x', '/my-files/a\\/b'):
            with self.assertRaises(ValueError):
                remote_path(remote)

if __name__ == '__main__':
    unittest.main()
