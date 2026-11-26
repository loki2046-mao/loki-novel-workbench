"""Loki 写手空间的本机 CLI 桥接。"""
import importlib
import argparse
import json
import os
import re
import secrets
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
import library

LIBRARY_MTIME = Path(library.__file__).stat().st_mtime


def fresh_library():
    global LIBRARY_MTIME
    try:
        mtime = Path(library.__file__).stat().st_mtime
        if mtime != LIBRARY_MTIME:
            importlib.reload(library)
            LIBRARY_MTIME = mtime
    except OSError:
        pass
    return library

ROOT = Path(__file__).resolve().parent
HOSTS = {"localhost:8788", "127.0.0.1:8788"}
ORIGINS = {"http://" + host for host in HOSTS}
LIMIT = 4 * 1024 * 1024
AGY_INLINE_PROMPT_MAX_BYTES = 100_000
AGY_POST_OUTPUT_IDLE_SECS = 30.0
FILE_CODE = secrets.token_urlsafe(12)
SLOT = threading.Lock()
ACTIVE_LOCK = threading.Lock()
ACTIVE_GEN = {"proc": None, "cancelled": False}
GEN_LOCAL = threading.local()
ENGINES = {
    "agy": [Path.home() / ".local/bin/agy", Path("/opt/homebrew/bin/agy"), Path("/usr/local/bin/agy")],
    "codex": [Path.home() / ".local/bin/codex", Path("/opt/homebrew/bin/codex"), Path("/usr/local/bin/codex")],
    "cola": [Path.home() / ".local/bin/cola", Path("/opt/homebrew/bin/cola"), Path("/usr/local/bin/cola")],
}


def executable(engine):
    # Finder and terminal PATHs differ; prefer the same known client in both.
    for candidate in ENGINES[engine]:
        hit = shutil.which(str(candidate))
        if hit:
            return hit
    return shutil.which(engine)


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


def cancel_active_generation():
    with ACTIVE_LOCK:
        proc = ACTIVE_GEN["proc"]
        ACTIVE_GEN["cancelled"] = True
    if proc is None:
        return False
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.5)
        return True
    except OSError:
        return False


def run_cli(binary, args, env, *, cwd=None, prompt=None, timeout=20):
    tracked = getattr(GEN_LOCAL, "active", False)
    if tracked:
        with ACTIVE_LOCK:
            if ACTIVE_GEN["cancelled"]:
                raise ValueError("已停止本次生成")
    proc = subprocess.Popen([binary, *args], cwd=cwd, env=env, shell=False,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if tracked:
        with ACTIVE_LOCK:
            if ACTIVE_GEN["cancelled"]:
                proc.terminate()
                proc.wait()
                raise ValueError("已停止本次生成")
            ACTIVE_GEN["proc"] = proc
    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise
    finally:
        if tracked:
            with ACTIVE_LOCK:
                if ACTIVE_GEN["proc"] is proc:
                    ACTIVE_GEN["proc"] = None
    if tracked:
        with ACTIVE_LOCK:
            if ACTIVE_GEN["cancelled"]:
                raise ValueError("已停止本次生成")
    if proc.returncode:
        detail = cli_detail(stderr, stdout)
        raise ValueError(f"{Path(binary).name} 退出码 {proc.returncode}：{detail or '无错误输出'}")
    return stdout


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


def _build_generate_cmd(engine, binary, prompt, model, env, result_file, streaming=False):
    if engine == "codex":
        args = ["exec", "--skip-git-repo-check", "--sandbox", "read-only", "-o", str(result_file)]
        if model:
            args += ["--model", model]
        args += ["-"]
        return args, prompt
    if engine == "agy":
        prompt_bytes = len(prompt.encode("utf-8"))
        if prompt_bytes > AGY_INLINE_PROMPT_MAX_BYTES:
            prompt_file = result_file.parent / "prompt.md"
            prompt_file.write_text(prompt, encoding="utf-8")
            short_prompt = "请完整阅读当前工作目录下的 prompt.md 文件，严格遵循其中的全部设定、风格卡、方法论与指令，直接输出最终正文结果，不要输出读取过程说明。"
            args = ["-p", short_prompt, "--mode", "auto", "--add-dir", str(result_file.parent), "--print-timeout", "0", "--disable-slash-commands"]
        else:
            args = ["-p", prompt, "--print-timeout", "0", "--disable-slash-commands"]
        if model:
            args += ["--model", model]
        return args, ""
    # This installed CLI has no per-message --model. Never change global settings silently.
    if model:
        current = json.loads(run_cli(binary, ["model", "current", "--json"], env))
        if model != current.get("currentModel"):
            raise ValueError("当前 Cola CLI 不支持逐次指定模型；请使用客户端默认模型")
    args = ["message", "--session", "writer-" + uuid.uuid4().hex, "--json", "--", prompt]
    env["COLA_CLI_TIMEOUT_MS"] = "900000"
    return args, ""


def _extract_stream_json_text(obj, accumulated):
    """Extract incremental text delta and optional authoritative result from an agy stream-json event."""
    if not isinstance(obj, dict):
        return "", None
    ev_type = obj.get("type")
    if ev_type == "stream_event" and isinstance(obj.get("event"), dict):
        return _extract_stream_json_text(obj["event"], accumulated)
    if ev_type == "content_block_delta":
        delta = obj.get("delta")
        if isinstance(delta, dict) and isinstance(delta.get("text"), str):
            return delta["text"], None
    if ev_type in {"delta", "text", "text_delta", "agent_message_chunk"}:
        for key in ("text", "delta", "content"):
            if isinstance(obj.get(key), str):
                return obj[key], None
    if ev_type in {"assistant", "message"}:
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
        if msg.get("role", "assistant") == "assistant":
            content = msg.get("content")
            full = ""
            if isinstance(content, str):
                full = content
            elif isinstance(content, list):
                full = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
            if full:
                if full.startswith(accumulated):
                    return full[len(accumulated):], None
                if accumulated.endswith(full):
                    return "", None
                return full, None
    if ev_type == "result":
        res = obj.get("result")
        if isinstance(res, str) and res:
            delta = res[len(accumulated):] if res.startswith(accumulated) else ("" if accumulated else res)
            return delta, res
    if isinstance(obj.get("delta"), str):
        return obj["delta"], None
    return "", None


def generate(engine, binary, prompt, model=""):
    env = child_environment()
    with tempfile.TemporaryDirectory(prefix="loki-writing-") as work:
        result_file = Path(work) / "reply.md"
        args, stdin_text = _build_generate_cmd(engine, binary, prompt, model, env, result_file)
        output = run_cli(binary, args, env, cwd=work, prompt=stdin_text, timeout=915)
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


def _open_live_draft(engine):
    try:
        draft_dir = ROOT / "_ai-drafts"
        draft_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        draft_path = draft_dir / f"{stamp}-{engine}-{uuid.uuid4().hex[:6]}.md"
        return draft_path, draft_path.open("w", encoding="utf-8")
    except OSError:
        return None, None


def stream_generate(engine, binary, prompt, model="", emit=None, timeout=1800):
    """Run CLI generation with live stdout/stream-json streaming, live disk persistence, and partial-output preservation."""
    emit = emit or (lambda event, payload: None)
    env = child_environment()
    start = time.monotonic()
    draft_path, draft_fp = _open_live_draft(engine)
    with tempfile.TemporaryDirectory(prefix="loki-writing-") as work:
        result_file = Path(work) / "reply.md"
        args, stdin_text = _build_generate_cmd(engine, binary, prompt, model, env, result_file, streaming=True)
        tracked = getattr(GEN_LOCAL, "active", False)
        if tracked:
            with ACTIVE_LOCK:
                if ACTIVE_GEN["cancelled"]:
                    raise ValueError("已停止本次生成")
        proc = subprocess.Popen([binary, *args], cwd=work, env=env, shell=False,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if tracked:
            with ACTIVE_LOCK:
                if ACTIVE_GEN["cancelled"]:
                    proc.terminate()
                    proc.wait()
                    raise ValueError("已停止本次生成")
                ACTIVE_GEN["proc"] = proc
        try:
            if stdin_text:
                proc.stdin.write(stdin_text.encode("utf-8"))
            proc.stdin.close()
        except OSError:
            pass

        import codecs
        out_dec = codecs.getincrementaldecoder("utf-8")("replace")
        err_dec = codecs.getincrementaldecoder("utf-8")("replace")
        stdout_parts, stderr_parts = [], []
        agy_line_buf = ""
        agy_text_parts = []
        agy_final_result = None
        agy_seen_json = False
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ, "out")
        selector.register(proc.stderr, selectors.EVENT_READ, "err")
        deadline = start + timeout
        last_tick = start
        last_output_at = None
        timed_out = False
        idle_finished = False
        total_chars = 0

        def write_draft(text_chunk):
            if draft_fp and text_chunk:
                try:
                    draft_fp.write(text_chunk)
                    draft_fp.flush()
                except OSError:
                    pass

        def handle_agy_line(line):
            nonlocal agy_final_result, agy_seen_json, total_chars, last_output_at
            stripped = line.strip()
            if not stripped:
                return
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    obj = json.loads(stripped)
                    if isinstance(obj, dict):
                        agy_seen_json = True
                        accumulated = "".join(agy_text_parts)
                        delta, final_res = _extract_stream_json_text(obj, accumulated)
                        if final_res:
                            agy_final_result = final_res
                        if delta:
                            agy_text_parts.append(delta)
                            total_chars += len(delta)
                            last_output_at = time.monotonic()
                            write_draft(delta)
                            emit("delta", {"text": delta, "elapsedMs": round((time.monotonic() - start) * 1000)})
                        return
                except json.JSONDecodeError:
                    pass
            if not agy_seen_json:
                agy_text_parts.append(line)
                total_chars += len(line)
                last_output_at = time.monotonic()
                write_draft(line)
                emit("delta", {"text": line, "elapsedMs": round((time.monotonic() - start) * 1000)})

        try:
            while selector.get_map():
                now = time.monotonic()
                remaining = deadline - now
                if remaining <= 0:
                    timed_out = True
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=2)
                    break
                if engine == "agy" and total_chars > 0 and last_output_at is not None and (now - last_output_at) >= AGY_POST_OUTPUT_IDLE_SECS:
                    idle_finished = True
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=2)
                    break
                events = selector.select(min(1.5, remaining))
                if not events:
                    now = time.monotonic()
                    if engine == "agy" and total_chars > 0 and last_output_at is not None and (now - last_output_at) >= AGY_POST_OUTPUT_IDLE_SECS:
                        idle_finished = True
                        proc.terminate()
                        try:
                            proc.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait(timeout=2)
                        break
                    if now - last_tick >= 1.5:
                        last_tick = now
                        emit("status", {"elapsedMs": round((now - start) * 1000), "chars": total_chars})
                    continue
                for key, _ in events:
                    raw = os.read(key.fd, 4096)
                    if not raw:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == "out":
                        chunk = out_dec.decode(raw, False)
                        if chunk:
                            stdout_parts.append(chunk)
                            last_output_at = time.monotonic()
                            if engine == "agy":
                                agy_line_buf += chunk
                                while "\n" in agy_line_buf:
                                    line, agy_line_buf = agy_line_buf.split("\n", 1)
                                    handle_agy_line(line + "\n")
                            else:
                                total_chars += len(chunk)
                                write_draft(chunk)
                    else:
                        chunk = err_dec.decode(raw, False)
                        if chunk:
                            stderr_parts.append(chunk)
            tail_out = out_dec.decode(b"", True)
            if tail_out:
                stdout_parts.append(tail_out)
                if engine == "agy":
                    agy_line_buf += tail_out
                else:
                    total_chars += len(tail_out)
                    write_draft(tail_out)
            if engine == "agy" and agy_line_buf:
                handle_agy_line(agy_line_buf)
                agy_line_buf = ""
            tail_err = err_dec.decode(b"", True)
            if tail_err:
                stderr_parts.append(tail_err)
            if not timed_out and not idle_finished:
                proc.wait(timeout=5)
        finally:
            selector.close()
            proc.stdout.close()
            proc.stderr.close()
            if draft_fp:
                try:
                    draft_fp.close()
                except OSError:
                    pass
            if tracked:
                with ACTIVE_LOCK:
                    if ACTIVE_GEN["proc"] is proc:
                        ACTIVE_GEN["proc"] = None

        stdout_text = "".join(stdout_parts).strip()
        agy_text = (agy_final_result or "".join(agy_text_parts)).strip() if engine == "agy" else ""
        stderr_text = "".join(stderr_parts)
        cancelled = False
        if tracked:
            with ACTIVE_LOCK:
                cancelled = bool(ACTIVE_GEN["cancelled"])

        partial_reason = ""
        if cancelled:
            partial_reason = "已手动停止，已保留已生成的内容"
        elif timed_out:
            partial_reason = "生成已达超时上限，已保留已生成的内容"
        elif idle_finished:
            partial_reason = ""
        elif proc.returncode != 0:
            detail = cli_detail(stderr_text, stdout_text)
            has_agy_partial = engine == "agy" and bool(agy_text or (not agy_seen_json and stdout_text))
            if has_agy_partial and (proc.returncode == 3 or "partial" in detail.lower() or "timeout" in detail.lower()):
                partial_reason = f"Antigravity 超时截断（已保留已生成的内容）：{detail}"
            else:
                raise ValueError(f"{Path(binary).name} 退出码 {proc.returncode}：{detail or '无错误输出'}")

        draft_rel = f"_ai-drafts/{draft_path.name}" if draft_path else ""
        if partial_reason:
            partial_text = ""
            if engine == "agy":
                partial_text = agy_text or (stdout_text if not agy_seen_json else "")
            elif engine == "codex" and result_file.exists():
                partial_text = result_file.read_text(encoding="utf-8").strip()
            if not partial_text and engine != "agy":
                partial_text = stdout_text
            if partial_text:
                if draft_path:
                    try:
                        draft_path.write_text(partial_text, encoding="utf-8")
                    except OSError:
                        pass
                return {"text": partial_text, "partial": True, "warning": partial_reason, "draftFile": draft_rel, "durationMs": round((time.monotonic() - start) * 1000)}
            if draft_path:
                draft_path.unlink(missing_ok=True)
            raise ValueError(partial_reason if not timed_out else "CLI 生成超时且尚未产生正文")

        if engine == "codex":
            if not result_file.exists():
                raise ValueError("Codex 成功退出但未生成回复文件")
            final_text = result_file.read_text(encoding="utf-8").strip()
        elif engine == "cola":
            reply = json.loads(stdout_text)
            if not isinstance(reply, dict) or reply.get("timedOut"):
                raise ValueError("Cola 未完成生成")
            if model and reply.get("model") != model:
                raise ValueError("Cola 实际模型与所选模型不一致，请重新检测客户端默认模型")
            final_text = str(reply.get("response") or "").strip()
        else:
            final_text = agy_text or (stdout_text if not agy_seen_json else "")

        if not final_text:
            if draft_path:
                draft_path.unlink(missing_ok=True)
            raise ValueError("CLI 未返回正文")
        if draft_path:
            try:
                draft_path.write_text(final_text, encoding="utf-8")
            except OSError:
                pass
        return {"text": final_text, "partial": False, "warning": "", "draftFile": draft_rel, "durationMs": round((time.monotonic() - start) * 1000)}

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

    def library_api(self, method):
        url = urlsplit(self.path)
        if url.path not in {"/api/books", "/api/workspaces", "/api/file"}:
            return False
        if self.headers.get("Origin") == "null" or self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.reply(403, {"error": "请从本机工作台访问"})
            return True
        try:
            lib = fresh_library()
            args = {k: v[0] for k, v in parse_qs(url.query).items()}
            if method == "GET":
                if url.path == "/api/workspaces":
                    result = lib.config()
                elif url.path == "/api/books":
                    result = lib.scan(lib.workspace(args["workspace"]))
                else:
                    result = {"text": lib.document(args["workspace"], args["path"]).read_text(encoding="utf-8")}
            else:
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("需要 JSON")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16 * 1024 * 1024:
                    raise ValueError("文件过大或为空请求")
                data = json.loads(self.rfile.read(size))
                if url.path == "/api/workspaces":
                    result = lib.change(data)
                elif url.path == "/api/file":
                    result = lib.save(data)
                else:
                    raise ValueError("不支持该操作")
            self.reply(200, result)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.reply(400, {"ok": False, "error": str(error)})
        return True

    def do_GET(self):
        if not self.allowed():
            return
        if urlsplit(self.path).path == "/api/health":
            return self.reply(200, {"ok": True, "service": "loki-novel-workbench"})
        if self.library_api("GET"):
            return
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
        path = Path(self.translate_path(self.path)).resolve()
        if path not in {ROOT / "小说台.html", ROOT / "loki-themes.js"} or not path.is_file():
            self.send_error(404)
            return
        return super().send_head()

    def parse_generate_request(self):
        if self.headers.get_content_type() != "application/json":
            self.reply(415, {"ok": False, "error": "需要 JSON"})
            return None
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= LIMIT:
                raise ValueError
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError
            if self.headers.get("Origin") == "null" and data.get("fileCode") != FILE_CODE:
                self.reply(403, {"ok": False, "error": "本地连接码不正确"})
                return None
            engine, prompt = data["engine"], data["prompt"]
            model = data.get("model", "")
            if not isinstance(model, str) or (model and not re.fullmatch(r"[A-Za-z0-9._:/-]+", model)):
                self.reply(400, {"ok": False, "error": "模型 ID 格式无效"})
                return None
            if engine not in ENGINES or not isinstance(prompt, str) or not prompt.strip() or "\0" in prompt:
                raise ValueError
            return engine, prompt, model
        except (ValueError, KeyError, TypeError):
            self.reply(400, {"ok": False, "error": "请检查引擎和提示词，JSON 上限为 4 MiB"})
            return None

    def do_POST(self):
        if not self.allowed():
            return
        if self.library_api("POST"):
            return
        path = urlsplit(self.path).path
        if path == "/api/generate/cancel":
            stopped = cancel_active_generation()
            return self.reply(200, {"ok": True, "cancelled": stopped})
        if path not in {"/api/generate", "/api/generate/stream"}:
            return self.reply(404, {"ok": False, "error": "接口不存在"})
        parsed = self.parse_generate_request()
        if not parsed:
            return
        engine, prompt, model = parsed
        binary = executable(engine)
        if not binary or not SLOT.acquire(blocking=False):
            return self.reply(503, {"ok": False, "error": "引擎未安装或正在生成"})
        start = time.monotonic()
        with ACTIVE_LOCK:
            ACTIVE_GEN["proc"] = None
            ACTIVE_GEN["cancelled"] = False
        GEN_LOCAL.active = True
        try:
            if path == "/api/generate/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "close")
                self.end_headers()
                def emit(event, payload):
                    frame = f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
                    try:
                        self.wfile.write(frame)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        cancel_active_generation()
                        raise
                try:
                    result = stream_generate(engine, binary, prompt, model, emit=emit)
                    emit("done", {"ok": True, "engine": engine, **result})
                except (BrokenPipeError, ConnectionResetError):
                    cancel_active_generation()
                except (ValueError, OSError) as error:
                    print(f"[generate-stream:{engine}] {error}", flush=True)
                    try:
                        emit("error", {"ok": False, "error": str(error)})
                    except OSError:
                        pass
                return
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
            GEN_LOCAL.active = False
            with ACTIVE_LOCK:
                ACTIVE_GEN["proc"] = None
                ACTIVE_GEN["cancelled"] = False
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
