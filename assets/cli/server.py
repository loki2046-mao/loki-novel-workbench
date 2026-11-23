"""Loki 写手空间的本机 CLI 桥接。"""
import json
import os
import re
import selectors
from concurrent.futures import ThreadPoolExecutor
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import argparse

ROOT = Path(__file__).resolve().parent
HOSTS = {"localhost:8788", "127.0.0.1:8788"}
ORIGINS = {"http://" + host for host in HOSTS}
LIMIT = 1024 * 1024
SLOT = threading.Lock()
ENGINES = {
    "agy": [Path.home() / ".local/bin/agy", Path("/opt/homebrew/bin/agy"), Path("/usr/local/bin/agy")],
    "codex": [Path.home() / ".local/bin/codex", Path("/opt/homebrew/bin/codex"), Path("/usr/local/bin/codex")],
    "cola": [Path.home() / ".local/bin/cola", Path("/opt/homebrew/bin/cola"), Path("/usr/local/bin/cola")],
}


def executable(engine):
    found = shutil.which(engine)
    if found:
        return found
    for candidate in ENGINES[engine]:
        hit = shutil.which(str(candidate))
        if hit:
            return hit
    return None


MODEL_FLAGS = {"agy": "--model", "codex": "--model"}
CATALOG_LOCK = threading.Lock()
CATALOG_CACHE = (0, [])


def child_environment():
    """CLI processes inherit the terminal; missing proxies fall back to macOS."""
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join(dict.fromkeys(env.get("PATH", "").split(os.pathsep) +
        [str(Path.home() / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]))
    proxies = {}
    try:
        result = subprocess.run(["/usr/sbin/scutil", "--proxy"], capture_output=True,
                                text=True, timeout=3, check=True)
        settings = dict(re.findall(r"^\s*(\w+)\s*:\s*(.*?)\s*$", result.stdout, re.M))
        for prefix, variable, scheme in (("HTTP", "http_proxy", "http"),
                                         ("HTTPS", "https_proxy", "http"),
                                         ("SOCKS", "all_proxy", "socks5h")):
            host, port = settings.get(prefix + "Proxy"), settings.get(prefix + "Port")
            if settings.get(prefix + "Enable") == "1" and host and port and port.isdigit():
                host = f"[{host}]" if ":" in host and not host.startswith("[") else host
                proxies[variable] = f"{scheme}://{host}:{port}"
    except (OSError, subprocess.SubprocessError):
        pass  # Existing environment still works on non-macOS hosts.
    for key in ("http_proxy", "https_proxy", "all_proxy"):
        value = env.get(key) or env.get(key.upper()) or proxies.get(key)
        if value:
            env[key] = env[key.upper()] = value
    bypass = [part.strip() for key in ("no_proxy", "NO_PROXY")
              for part in env.get(key, "").split(",") if part.strip()]
    env["NO_PROXY"] = env["no_proxy"] = ",".join(dict.fromkeys(bypass + ["localhost", "127.0.0.1", "::1"]))
    return env


def cli_detail(stderr, stdout=""):
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", stderr or stdout or "")
    text = re.sub(r"(?i)(Bearer\s+)[^\s\"']+", r"\1[redacted]", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    # Codex's actual model error often precedes unrelated MCP shutdown warnings.
    primary = [line for line in lines if re.match(r"(?i)^(?:error:|error\b|✖|×)", line)]
    return "\n".join(primary or lines[-12:])[-4000:]


def run_cli(binary, args, env, *, cwd=None, prompt=None, timeout=20):
    result = subprocess.run([binary, *args], cwd=cwd, env=env, shell=False,
                            input=prompt, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        detail = cli_detail(result.stderr, result.stdout)
        raise ValueError(f"{Path(binary).name} 退出码 {result.returncode}：{detail or '无错误输出'}")
    return result.stdout


class CliRPC:
    """Short-lived JSON-lines stdio client, with one deadline and no shell."""
    def __init__(self, binary, args, env, cwd, timeout=25):
        self.error_file = tempfile.TemporaryFile()
        self.process = subprocess.Popen([binary, *args], cwd=cwd, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.error_file)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.buffer = b""
        self.sequence = 0
        self.deadline = time.monotonic() + timeout

    def send(self, method, params, request_id=None):
        payload = dict(jsonrpc="2.0", method=method, params=params)
        if request_id is not None:
            payload["id"] = request_id
        self.process.stdin.write((json.dumps(payload) + "\n").encode())
        self.process.stdin.flush()

    def call(self, method, params):
        self.sequence += 1
        self.send(method, params, self.sequence)
        while True:
            if b"\n" not in self.buffer:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0 or not self.selector.select(remaining):
                    raise ValueError(f"{method} 查询超时")
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    self.error_file.seek(0)
                    raise ValueError(cli_detail(self.error_file.read().decode(errors="replace")) or "CLI 协议连接已关闭")
                self.buffer += chunk
                continue
            line, self.buffer = self.buffer.split(b"\n", 1)
            if not line.strip():
                continue
            response = json.loads(line)
            if response.get("id") != self.sequence or "method" in response:
                continue
            if "error" in response:
                raise ValueError(str(response["error"].get("message", response["error"])))
            return response["result"]

    def close(self):
        self.selector.close()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()
        self.error_file.close()


def discover_models(engine, binary, env):
    if engine == "cola":
        choices = json.loads(run_cli(binary, ["model", "list", "--json"], env))
        current = json.loads(run_cli(binary, ["model", "current", "--json"], env))
        return dict(models=[dict(id=m["id"], name=m.get("name", m["id"]))
                            for m in choices if m.get("selectable")],
                    defaultModel=current.get("currentModel") or current.get("modelConfig", {}).get("default", {}).get("model", ""),
                    modelSource="cola model list/current --json")
    with tempfile.TemporaryDirectory(prefix="loki-models-") as work:
        rpc = CliRPC(binary, ["acp"] if engine == "agy" else ["app-server", "--stdio"], env, work)
        try:
            rpc.call("initialize", dict(protocolVersion=1, clientCapabilities={},
                     clientInfo=dict(name="loki-writing", version="1")))
            if engine == "agy":
                result = rpc.call("session/new", dict(cwd=work, mcpServers=[]))["models"]
                return dict(models=[dict(id=m["modelId"], name=m.get("name", m["modelId"]))
                                    for m in result["availableModels"]],
                            defaultModel=result.get("currentModelId", ""), modelSource="agy ACP session/new")
            rpc.send("initialized", {})
            models, cursor = [], None
            while True:
                page = rpc.call("model/list", dict(limit=100, **({"cursor": cursor} if cursor else {})))
                models.extend(page["data"])
                cursor = page.get("nextCursor")
                if not cursor:
                    break
            config = rpc.call("config/read", {"includeLayers": False})["config"]
            default = config.get("model") or next((m["model"] for m in models if m.get("isDefault")), "")
            return dict(models=[dict(id=m["model"], name=m.get("displayName", m["model"]))
                                for m in models if not m.get("hidden")], defaultModel=default,
                        modelSource="codex app-server model/list + config/read")
        finally:
            rpc.close()


def engine_catalog(force=False):
    global CATALOG_CACHE
    with CATALOG_LOCK:
        timestamp, cached = CATALOG_CACHE
        if not force and cached and time.monotonic() - timestamp < 60:
            return cached
        env = child_environment()
        def discover(name):
            path = executable(name)
            item = dict(engine=name, available=bool(path), path=path,
                        supportsModel=name in MODEL_FLAGS, models=[], defaultModel="",
                        reason="" if path else "未找到可执行文件", modelError="")
            if name == "cola":
                item["modelSelectionNote"] = "当前 Cola CLI 仅支持全局切模；此处使用客户端默认模型。"
            if path:
                try:
                    item.update(discover_models(name, path, env))
                except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
                    item["modelError"] = f"模型查询失败：{error}"
            return item
        with ThreadPoolExecutor(max_workers=3) as pool:
            engines = list(pool.map(discover, ENGINES))
        # Retry failures promptly; never substitute invented or stale models.
        CATALOG_CACHE = (time.monotonic() if not any(e["modelError"] for e in engines) else 0, engines)
        return engines


def generate(engine, binary, prompt, model=""):
    env = child_environment()
    with tempfile.TemporaryDirectory(prefix="loki-writing-") as work:
        result_file = Path(work) / "reply.md"
        if engine == "codex":
            args = ["exec", "--skip-git-repo-check", "--sandbox", "read-only", "-o", str(result_file)]
            if model:
                args += ["--model", model]
            args += ["-"]
        elif engine == "agy":
            args = ["-p", prompt, "--print-timeout", "5m", "--sandbox"]
            if model:
                args += ["--model", model]
        else:
            # This installed CLI has no per-message --model. Never change global settings silently.
            if model:
                current = json.loads(run_cli(binary, ["model", "current", "--json"], env))
                if model != current.get("currentModel"):
                    raise ValueError("当前 Cola CLI 不支持逐次指定模型；请使用客户端默认模型")
            args = ["message", "--session", "writer-" + uuid.uuid4().hex, "--json", "--", prompt]
            env["COLA_CLI_TIMEOUT_MS"] = "300000"
        output = run_cli(binary, args, env, cwd=work,
                         prompt=prompt if engine == "codex" else "", timeout=315)
        if engine == "codex":
            if not result_file.exists():
                raise ValueError("Codex 成功退出但未生成回复文件")
            return result_file.read_text(encoding="utf-8").strip()
        if engine == "cola":
            reply = json.loads(output)
            if not isinstance(reply, dict) or reply.get("timedOut"):
                raise ValueError("Cola 未完成生成")
            if model and reply.get("model") != model:
                raise ValueError("Cola 实际模型与所选模型不一致，请重新检测客户端默认模型")
            return str(reply.get("response") or "").strip()
        return output.strip()

class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        origin = self.headers.get("Origin")
        if origin in ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def reply(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def allowed(self):
        if (self.headers.get("Host") not in HOSTS or self.headers.get("Origin") not in ORIGINS | {None}
                or self.headers.get("Sec-Fetch-Site") == "cross-site"):
            self.reply(403, {"ok": False, "error": "仅限本地工作台"})
            return False
        return True

    def do_OPTIONS(self):
        if self.allowed():
            self.send_response(204)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

    def do_GET(self):
        if not self.allowed():
            return
        if urlsplit(self.path).path == "/api/health":
            return self.reply(200, {"ok": True, "service": "loki-novel-workbench"})
        if urlsplit(self.path).path == "/api/engines":
            force = parse_qs(urlsplit(self.path).query).get("refresh") == ["1"]
            self.reply(200, {"ok": True, "engines": engine_catalog(force)})
        else:
            super().do_GET()

    def send_head(self):
        if self.command == "HEAD" and not self.allowed():
            return
        if urlsplit(self.path).path == "/":
            self.path = "/小说台.html"
        # A novel library is not a public web directory. Serve only the workbench UI.
        path = Path(self.translate_path(self.path)).resolve()
        if path != ROOT / "小说台.html" or not path.is_file():
            self.send_error(404)
            return
        return super().send_head()

    def do_POST(self):
        if not self.allowed():
            return
        if urlsplit(self.path).path != "/api/generate":
            return self.reply(404, {"ok": False, "error": "接口不存在"})
        if self.headers.get_content_type() != "application/json":
            return self.reply(415, {"ok": False, "error": "需要 JSON"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= LIMIT:
                raise ValueError
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError
            engine, prompt = data["engine"], data["prompt"]
            model = data.get("model", "")
            if not isinstance(model, str) or (model and not re.fullmatch(r"[A-Za-z0-9._:/-]+", model)):
                return self.reply(400, {"ok": False, "error": "模型 ID 格式无效"})
            if engine not in ENGINES or not isinstance(prompt, str) or not prompt.strip() or "\0" in prompt:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return self.reply(400, {"ok": False, "error": "请检查引擎和提示词，JSON 上限为 1 MiB"})
        binary = executable(engine)
        if not binary or not SLOT.acquire(blocking=False):
            return self.reply(503, {"ok": False, "error": "引擎未安装或正在生成"})
        start = time.monotonic()
        try:
            text = generate(engine, binary, prompt, model)
            if not text:
                raise ValueError("CLI 未返回正文")
            self.reply(200, dict(ok=True, engine=engine, text=text,
                                 durationMs=round((time.monotonic() - start) * 1000)))
        except subprocess.TimeoutExpired:
            self.reply(504, {"ok": False, "error": "CLI 生成超时"})
        except (ValueError, OSError) as error:
            print(f"[generate:{engine}] {error}", flush=True)
            self.reply(502, {"ok": False, "error": str(error)})
        finally:
            SLOT.release()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Loki 小说工作台本机 CLI 桥接")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口范围为 1–65535")
    HOSTS = {f"localhost:{args.port}", f"127.0.0.1:{args.port}"}
    ORIGINS = {"http://" + host for host in HOSTS}
    os.chdir(ROOT)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as error:
        parser.exit(1, f"服务启动失败：{error}。可用 --port 指定其他端口。\n")
    url = f"http://127.0.0.1:{args.port}/"
    print("工作台：", url, flush=True)
    if args.open:
        import webbrowser
        threading.Timer(0.2, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
