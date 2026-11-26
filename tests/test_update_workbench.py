"""Exercise the actual offline Agent upgrade command on fictional shelves."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class UpdateTests(unittest.TestCase):
    def run_update(self, shelf):
        return subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/update_workbench.py'), str(shelf)], capture_output=True, text=True)

    def make_shelf(self, root):
        (root / '小说台.html').write_text('custom old page')
        (root / 'VERSION').write_text('1.0.0\n')
        protected = ['我的书/正文/第一章.md', '_写作规则与Skill/private.md', '_模板/章节模板.md']
        for name in protected:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('作者自己的内容：' + name)
        return {name: (root / name).read_bytes() for name in protected}

    def test_upgrade_and_repeat_preserve_author_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            shelf = Path(tmp)
            protected = self.make_shelf(shelf)
            result = self.run_update(shelf)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('升级前版本：1.0.0', result.stdout)
            self.assertIn('升级后版本：1.0.1', result.stdout)
            self.assertIn('_workbench-backups', result.stdout)
            self.assertEqual(next((shelf / '_workbench-backups').glob('*/小说台.html')).read_text(), 'custom old page')
            for name, content in protected.items():
                self.assertEqual((shelf / name).read_bytes(), content)
            self.assertFalse((shelf / '示例作品-雨夜来信').exists())
            before = {p.relative_to(shelf): p.stat().st_mtime_ns for p in shelf.rglob('*')}
            result = self.run_update(shelf)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('无需升级', result.stdout)
            self.assertEqual(before, {p.relative_to(shelf): p.stat().st_mtime_ns for p in shelf.rglob('*')})

    def test_legacy_without_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            shelf = Path(tmp)
            self.make_shelf(shelf)
            (shelf / 'VERSION').unlink()
            result = self.run_update(shelf)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('旧版无 VERSION', result.stdout)

    def test_invalid_target_is_not_initialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            shelf = Path(tmp) / 'missing'
            self.assertNotEqual(self.run_update(shelf).returncode, 0)
            self.assertFalse(shelf.exists())

    def test_failure_does_not_touch_manuscripts_or_version(self):
        for obstruction in ('server.py', '_workbench-backups'):
            with self.subTest(obstruction=obstruction), tempfile.TemporaryDirectory() as tmp:
                shelf = Path(tmp)
                protected = self.make_shelf(shelf)
                manuscript = shelf / next(iter(protected))
                (shelf / obstruction).symlink_to(manuscript)
                result = self.run_update(shelf)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('升级失败', result.stderr)
                self.assertEqual((shelf / 'VERSION').read_text(), '1.0.0\n')
                self.assertEqual((shelf / '小说台.html').read_text(), 'custom old page')
                for name, content in protected.items():
                    self.assertEqual((shelf / name).read_bytes(), content)

    def test_refuses_downgrade_and_invalid_version(self):
        for version in ('99.0.0', 'invalid'):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as tmp:
                shelf = Path(tmp)
                self.make_shelf(shelf)
                (shelf / 'VERSION').write_text(version)
                self.assertNotEqual(self.run_update(shelf).returncode, 0)
                self.assertEqual((shelf / '小说台.html').read_text(), 'custom old page')

    def test_release_markers(self):
        self.assertEqual((ROOT / 'VERSION').read_text().strip(), '1.0.1')
        subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/sync_version.py'), '--check'], check=True)
