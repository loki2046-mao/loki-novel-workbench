"""Registered local Markdown libraries for Loki Writer Space."""
import json
import os
import re
import tempfile
import threading
import uuid
from pathlib import Path

CONFIG = Path(__file__).with_name("workspaces.json")
LOCK = threading.RLock()
CHAPTER_DIRS = ("正文", "番外", "04-正文")
CORE = re.compile("伏笔|时间线|总纲|设定|圣经|大纲|细纲|人物小传|人物状态卡|进度|证据链|一致性|全本")
SKIP = {"workbench", "loki-themes", "node_modules"}
COUNT = re.compile(r"```[\s\S]*?```|[#>*`_\-\[\]()|\s]+")


def config():
    if not CONFIG.exists():
        root = CONFIG.parent.resolve()
        return {"default": "local", "workspaces": [{"id": "local", "name": root.name, "path": str(root)}]}
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def atomic(path, text):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as out:
        temp = Path(out.name)
        try:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def change(data):
    with LOCK:
        cfg = config()
        items = cfg["workspaces"]
        if data["action"] == "add":
            path = Path(data["path"]).expanduser()
            if not path.is_absolute() or not path.is_dir():
                raise ValueError("请输入已存在的目录绝对路径")
            path = path.resolve()
            if any(i["path"] == str(path) for i in items):
                raise ValueError("目录已登记")
            item = dict(id=uuid.uuid4().hex, name=path.name, path=str(path))
            items.append(item)
            cfg["default"] = cfg["default"] or item["id"]
        elif data["action"] == "remove":
            items[:] = [i for i in items if i["id"] != data["id"]]
            if cfg["default"] == data["id"]:
                cfg["default"] = items[0]["id"] if items else None
        else:
            raise ValueError("未知操作")
        atomic(CONFIG, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
        return cfg


def workspace(key):
    for item in config()["workspaces"]:
        if item["id"] == key:
            path = Path(item["path"])
            if not path.is_absolute() or path.resolve() != path or not path.is_dir():
                raise ValueError("登记目录不可访问")
            return path
    raise ValueError("工作目录未登记")


def document(key, rel):
    root = workspace(key)
    parts = rel.split("/")
    if any(not p or p.startswith(".") or "\\" in p for p in parts) or not rel.lower().endswith(".md"):
        raise ValueError("需要工作目录内的 Markdown 相对路径")
    path = root.joinpath(*parts)
    if path.resolve() != path or (path.exists() and not path.is_file()):
        raise ValueError("文件路径不受支持")
    return path


def save(data):
    with LOCK:
        path = document(data["workspace"], data["path"])
        if not isinstance(data["text"], str):
            raise ValueError("需要文本")
        if data.get("createOnly") and path.exists():
            raise ValueError("同名文件已存在")
        if "expectedText" in data:
            current = path.read_text(encoding="utf-8") if path.exists() else None
            if current != data["expectedText"]:
                raise ValueError("文件已被其他窗口修改，请刷新后重试")
        path.parent.mkdir(parents=True, exist_ok=True)
        text = data["text"]
        if data.get("append"):
            if path.exists():
                existing = path.read_text(encoding="utf-8-sig", errors="replace").rstrip()
                text = (existing + "\n\n" + text.lstrip()) if existing else text
            elif data.get("header"):
                text = str(data["header"]).rstrip() + "\n\n" + text.lstrip()
        atomic(path, text)
        return {"ok": True}


def scan(root):
    books = []
    def book(folder, loose=False):
        core, chapters, ledger = [], [], ""
        def collect(directory, prefix=""):
            nonlocal ledger
            for f in directory.iterdir():
                if f.is_symlink() or not f.is_file() or f.suffix != ".md" or f.name.lower() == "readme.md" or f.name.startswith("."):
                    continue
                text = f.read_text(encoding="utf-8-sig", errors="replace")
                body = re.sub(r"^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)", "", text, count=1)
                is_core = not prefix and (f.name.startswith("00-") if loose else bool(CORE.search(f.name)))
                if f.name == "02-伏笔与时间线.md" and not prefix:
                    ledger = text
                (core if is_core else chapters).append(dict(path=prefix+f.name,
                    name=("【番外】" if prefix == "番外/" else "")+f.stem,
                    isChapter=not is_core, words=len(COUNT.sub("", body).encode("utf-16-le"))//2))
        collect(folder)
        dirs = [] if loose else [n for n in CHAPTER_DIRS if not (folder/n).is_symlink() and (folder/n).is_dir()]
        for n in dirs:
            collect(folder/n, n+"/")
        if not loose or core or chapters:
            books.append(dict(name="根目录总览 / 散落文档" if loose else folder.name,
                isLooseBucket=loose, chapterDirName="正文" if "正文" in dirs else "04-正文",
                coreFiles=core, chapters=chapters, totalWords=sum(c["words"] for c in chapters), ledger=ledger))
    def scan_assets(dirname):
        folder = root / dirname
        items = []
        if folder.is_dir() and not folder.is_symlink():
            for f in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
                if f.is_symlink() or not f.is_file() or f.suffix != ".md" or f.name.lower() == "readme.md" or f.name.startswith("."):
                    continue
                items.append(dict(path=f"{dirname}/{f.name}", name=f.stem))
        return items
    book(root, True)
    for folder in sorted(root.iterdir()):
        if not folder.name.startswith((".", "_")) and folder.name not in SKIP and not folder.is_symlink() and folder.is_dir():
            book(folder)
    return {"books": books, "assets": {"styleCards": scan_assets("_style-cards"), "skills": scan_assets("_写作方法论")}}
