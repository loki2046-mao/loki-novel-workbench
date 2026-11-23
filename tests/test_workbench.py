"""Offline regressions: python3 -m unittest discover -s tests -v"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
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

if __name__ == "__main__":
    unittest.main()
