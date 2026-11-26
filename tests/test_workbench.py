"""Offline regressions: python3 -m unittest discover -s tests -v"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

sys.path.insert(0, str(ROOT / "assets/cli"))
bridge = load("bridge", ROOT / "assets/cli/server.py")
scaffold = load("scaffold", ROOT / "scripts/init_library.py")

class InstallTests(unittest.TestCase):
    def test_upgrade_preserves_manuscripts_and_backs_up_custom_app(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            scaffold.init_library(root)
            chapter = root / scaffold.DEMO_BOOK / "正文/第01章-停电的钟表店.md"
            chapter.write_text("author revision")
            rules = root / "_写作规则与Skill/private.md"
            rules.write_text("author rules")
            html = root / "小说台.html"
            html.write_text("custom UI")
            scaffold.init_library(root)
            self.assertEqual(html.read_text(), "custom UI")
            scaffold.init_library(root, update_app=True)
            self.assertEqual(chapter.read_text(), "author revision")
            self.assertEqual(rules.read_text(), "author rules")
            self.assertEqual(html.read_bytes(), scaffold.HTML_SRC.read_bytes())
            backups = list((root / "_workbench-backups").glob("*/小说台.html"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), "custom UI")
            self.assertTrue((root / "server.py").is_file())
            self.assertTrue(os.access(root / "启动写作台.command", os.X_OK))

    def test_upgrade_refuses_app_symlinks(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            outside = root / "manuscript.md"
            outside.write_text("must survive")
            (root / "小说台.html").symlink_to(outside)
            with self.assertRaises(ValueError):
                scaffold.install_app(root, update_app=True)
            self.assertEqual(outside.read_text(), "must survive")

class CliTests(unittest.TestCase):
    def test_system_proxy_fallback_preserves_explicit_environment_and_local_bypass(self):
        output = "HTTPEnable : 1\nHTTPProxy : 127.0.0.1\nHTTPPort : 8089\nHTTPSEnable : 1\nHTTPSProxy : 127.0.0.1\nHTTPSPort : 8089\n"
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://explicit:9999", "NO_PROXY": "internal"}, clear=True), patch.object(bridge.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")):
            env = bridge.child_environment()
        self.assertEqual(env["http_proxy"], "http://127.0.0.1:8089")
        self.assertEqual(env["https_proxy"], "http://explicit:9999")
        self.assertEqual(env["https_proxy"], env["HTTPS_PROXY"])
        self.assertEqual(set(env["no_proxy"].split(",")), {"internal", "localhost", "127.0.0.1", "::1"})

    def test_non_macos_environment_still_works(self):
        with patch.dict(os.environ, {"http_proxy": "http://configured:8080"}, clear=True), patch.object(bridge.subprocess, "run", side_effect=FileNotFoundError):
            env = bridge.child_environment()
        self.assertEqual(env["HTTP_PROXY"], "http://configured:8080")
        self.assertIn("127.0.0.1", env["NO_PROXY"])

    def test_primary_model_error_survives_shutdown_noise(self):
        self.assertEqual(bridge.cli_detail("ERROR: model unsupported\n2026 WARN unrelated MCP shutdown"), "ERROR: model unsupported")

    def test_selected_models_reach_cli_and_codex_stdin(self):
        seen = []
        def run(binary, args, env, **kwargs):
            seen.append((binary, args, kwargs))
            if binary == "codex":
                Path(args[args.index("-o") + 1]).write_text("OK")
            return "OK"
        with patch.object(bridge, "child_environment", return_value={}), patch.object(bridge, "run_cli", side_effect=run):
            self.assertEqual(bridge.generate("agy", "agy", "只回复 OK", "selected-agy"), "OK")
            self.assertEqual(bridge.generate("codex", "codex", "只回复 OK", "selected-codex"), "OK")
        for binary, args, kwargs in seen:
            self.assertEqual(args[args.index("--model") + 1], "selected-" + binary)
        self.assertEqual(seen[1][2]["prompt"], "只回复 OK")

    def test_cola_does_not_mutate_global_model(self):
        with patch.object(bridge, "child_environment", return_value={}), patch.object(bridge, "run_cli", return_value='{"currentModel":"current"}') as run:
            with self.assertRaisesRegex(ValueError, "不支持逐次指定模型"):
                bridge.generate("cola", "cola", "只回复 OK", "another")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[1], ["model", "current", "--json"])

    def test_failed_discovery_has_no_fabricated_models(self):
        with patch.object(bridge, "child_environment", return_value={}), patch.object(bridge, "executable", return_value="installed"), patch.object(bridge, "discover_models", side_effect=ValueError("offline")):
            catalog = bridge.engine_catalog(force=True)
        self.assertTrue(all(x["models"] == [] and x["defaultModel"] == "" and "offline" in x["modelError"] for x in catalog))

    def test_cancel_active_generation_terminates_running_cli(self):
        outcome = {}
        def worker():
            with bridge.ACTIVE_LOCK:
                bridge.ACTIVE_GEN["proc"] = None
                bridge.ACTIVE_GEN["cancelled"] = False
            bridge.GEN_LOCAL.active = True
            try:
                bridge.run_cli(sys.executable, ["-c", "import time; time.sleep(30)"], os.environ.copy(), timeout=30)
            except ValueError as err:
                outcome["error"] = str(err)
            finally:
                bridge.GEN_LOCAL.active = False
                with bridge.ACTIVE_LOCK:
                    bridge.ACTIVE_GEN["proc"] = None
                    bridge.ACTIVE_GEN["cancelled"] = False
        thread = threading.Thread(target=worker)
        thread.start()
        deadline = bridge.time.monotonic() + 3
        while bridge.time.monotonic() < deadline:
            with bridge.ACTIVE_LOCK:
                if bridge.ACTIVE_GEN["proc"] is not None:
                    break
            bridge.time.sleep(0.02)
        self.assertTrue(bridge.cancel_active_generation())
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertIn("已停止", outcome.get("error", ""))

    def test_stream_generate_emits_live_deltas_and_preserves_partial_on_timeout(self):
        events = []
        script = (
            "import sys, time\n"
            "sys.stdout.write('第一段正文。\\n'); sys.stdout.flush()\n"
            "time.sleep(0.05)\n"
            "sys.stdout.write('第二段正文。\\n'); sys.stdout.flush()\n"
            "time.sleep(5)\n"
        )
        with patch.object(bridge, "child_environment", return_value=os.environ.copy()), \
             patch.object(bridge, "_build_generate_cmd", return_value=(["-c", script], "")):
            res = bridge.stream_generate("agy", sys.executable, "test", emit=lambda ev, data: events.append((ev, data)), timeout=0.35)
        self.assertTrue(res["partial"])
        self.assertIn("第一段正文", res["text"])
        self.assertIn("第二段正文", res["text"])
        deltas = "".join(d["text"] for ev, d in events if ev == "delta")
        self.assertIn("第一段正文", deltas)
        self.assertIn("第二段正文", deltas)

    def test_stream_generate_parses_agy_stream_json_events_live(self):
        events = []
        script = (
            "import json, sys, time\n"
            "for obj in [\n"
            "  {'type':'stream_event','event':{'type':'content_block_delta','delta':{'type':'text_delta','text':'雨夜，'}}},\n"
            "  {'type':'stream_event','event':{'type':'content_block_delta','delta':{'type':'text_delta','text':'钟表停了。'}}},\n"
            "  {'type':'result','result':'雨夜，钟表停了。'}\n"
            "]:\n"
            "  sys.stdout.write(json.dumps(obj, ensure_ascii=False) + '\\n'); sys.stdout.flush(); time.sleep(0.02)\n"
        )
        with patch.object(bridge, "child_environment", return_value=os.environ.copy()), \
             patch.object(bridge, "_build_generate_cmd", return_value=(["-c", script], "")):
            res = bridge.stream_generate("agy", sys.executable, "test", emit=lambda ev, data: events.append((ev, data)), timeout=5)
        self.assertFalse(res["partial"])
        self.assertEqual(res["text"], "雨夜，钟表停了。")
        deltas = [d["text"] for ev, d in events if ev == "delta"]
        self.assertEqual(deltas, ["雨夜，", "钟表停了。"])

    def test_oversized_agy_prompt_spills_to_file_avoiding_arg_max(self):
        with tempfile.TemporaryDirectory() as work:
            result_file = Path(work) / "reply.md"
            huge_prompt = "长风格卡内容。" * 20000  # > 100 KB UTF-8
            args, stdin_text = bridge._build_generate_cmd("agy", "agy", huge_prompt, "m1", {}, result_file, streaming=True)
            self.assertEqual(stdin_text, "")
            self.assertIn("--add-dir", args)
            self.assertNotIn("--output-format", args)
            self.assertLess(len(args[args.index("-p") + 1].encode("utf-8")), 1024)
            prompt_file = Path(work) / "prompt.md"
            self.assertTrue(prompt_file.is_file())
            self.assertEqual(prompt_file.read_text(encoding="utf-8"), huge_prompt)

    def test_agy_post_output_idle_completes_without_timeout_error(self):
        events = []
        script = (
            "import sys, time\n"
            "sys.stdout.write('完整正文第一段。\\n完整正文第二段。\\n'); sys.stdout.flush()\n"
            "time.sleep(10)\n"
        )
        with patch.object(bridge, "child_environment", return_value=os.environ.copy()), \
             patch.object(bridge, "_build_generate_cmd", return_value=(["-c", script], "")), \
             patch.object(bridge, "AGY_POST_OUTPUT_IDLE_SECS", 0.25):
            res = bridge.stream_generate("agy", sys.executable, "test", emit=lambda ev, data: events.append((ev, data)), timeout=10)
        self.assertFalse(res["partial"])
        self.assertEqual(res["text"], "完整正文第一段。\n完整正文第二段。")
        self.assertEqual(res["warning"], "")





class HttpTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        root = Path(self.work.name).resolve()
        (root / "小说台.html").write_text("workbench")
        (root / "private.md").write_text("private manuscript")
        self.http = bridge.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **kw: bridge.Handler(*a, directory=str(root), **kw))
        self.url = "http://127.0.0.1:" + str(self.http.server_port)
        self.patches = [patch.object(bridge, "ROOT", root), patch.object(bridge, "HOSTS", {self.url.split("//")[1]}), patch.object(bridge, "ORIGINS", {self.url})]
        for p in self.patches: p.start()
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.client = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join()
        for p in self.patches: p.stop()
        self.work.cleanup()

    def request(self, path, data=None, headers=None):
        req = urllib.request.Request(self.url + path, data=json.dumps(data).encode() if data else None,
            headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with self.client.open(req, timeout=5) as reply: return reply.status, reply.read()
        except urllib.error.HTTPError as error:
            with error: return error.code, error.read()

    def test_only_ui_is_served_not_manuscripts_or_directories(self):
        self.assertEqual(self.request("/")[0], 200)
        self.assertEqual(self.request("/api/health")[0], 200)
        for path in ["/private.md", "/server.py", "/../private.md", "/templates/"]:
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request("/", headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.request("/", headers={"Origin": "null"})[0], 403)

    def test_generation_passes_model_and_returns_diagnostic(self):
        payload = dict(engine="codex", model="selected", prompt="只回复 OK")
        with patch.object(bridge, "executable", return_value="codex"), patch.object(bridge, "generate", return_value="OK") as generate:
            status, body = self.request("/api/generate", payload)
            self.assertEqual(status, 200); self.assertEqual(json.loads(body)["text"], "OK")
            generate.assert_called_once_with("codex", "codex", "只回复 OK", "selected")
        with patch.object(bridge, "executable", return_value="codex"), patch.object(bridge, "generate", side_effect=ValueError("model unsupported")):
            status, body = self.request("/api/generate", payload)
            self.assertEqual(status, 502); self.assertIn("model unsupported", json.loads(body)["error"])
        self.assertEqual(self.request("/api/generate", {**payload, "model": "bad;command"})[0], 400)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.config = patch.object(bridge.library, 'CONFIG', self.root / 'workspaces.json')
        self.config.start()

    def tearDown(self):
        self.config.stop()
        self.tmp.cleanup()

    def test_missing_config_registers_only_application_directory(self):
        cfg = bridge.library.config()
        self.assertEqual(cfg['default'], 'local')
        self.assertEqual(cfg['workspaces'][0]['path'], str(self.root))
        self.assertFalse((self.root / 'workspaces.json').exists())

    def test_optional_assets_scan_and_missing_folders(self):
        self.assertEqual(bridge.library.scan(self.root)['assets'], {'styleCards': [], 'skills': []})
        for name in ('_style-cards', '_写作方法论'):
            (self.root / name).mkdir()
            (self.root / name / 'example.md').write_text('fictional style')
        result = bridge.library.scan(self.root)
        self.assertEqual(len(result['assets']['styleCards']), 1)
        self.assertEqual(len(result['assets']['skills']), 1)
        self.assertFalse(any(x['name'].startswith('_') for x in result['books']))

    def test_conflicting_save_and_undo_never_overwrite_newer_text(self):
        p = self.root / 'chapter.md'; p.write_text('concurrent author edit')
        with self.assertRaisesRegex(ValueError, '其他窗口'):
            bridge.library.save(dict(workspace='local', path='chapter.md', text='AI result', expectedText='old draft'))
        self.assertEqual(p.read_text(), 'concurrent author edit')
        bridge.library.save(dict(workspace='local', path='chapter.md', text='accepted result', expectedText='concurrent author edit'))
        self.assertEqual(p.read_text(), 'accepted result')

    def test_path_traversal_symlinks_and_duplicate_creation_rejected(self):
        for name in ('../outside.md', '/outside.md', '.hidden.md', 'folder/../../outside.md'):
            with self.assertRaises(ValueError): bridge.library.document('local', name)
        (self.root / 'link.md').symlink_to(self.root / 'real.md')
        with self.assertRaises(ValueError): bridge.library.document('local', 'link.md')
        (self.root / 'existing.md').write_text('keep')
        with self.assertRaises(ValueError):
            bridge.library.save(dict(workspace='local',path='existing.md',text='replace',createOnly=True))

    def test_scan_counts_only_body_han_characters(self):
        folder = self.root / 'Fictional' / '正文'
        folder.mkdir(parents=True)
        (folder / '第01章.md').write_text('---\nbook: 私有元数据\n---\n# 第一章\n汉字 abc 123！𠀀')
        scanned = bridge.library.scan(self.root)
        chapter = next(b for b in scanned['books'] if b['name'] == 'Fictional')['chapters'][0]
        self.assertEqual(chapter['cnWords'], 5)
        self.assertGreater(chapter['words'], chapter['cnWords'])

    def test_new_skill_append_preserves_existing_text(self):
        data = dict(workspace='local',path='_写作方法论/example.md',text='first',expectedText=None)
        bridge.library.save(data)
        bridge.library.save(dict(data, text='second', append=True, expectedText='first'))
        self.assertEqual((self.root / data['path']).read_text(), 'first\n\nsecond')

if __name__ == "__main__":
    unittest.main()
