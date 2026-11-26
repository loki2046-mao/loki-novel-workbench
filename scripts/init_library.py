#!/usr/bin/env python3
"""
loki-novel-workbench 初始化脚手架（零第三方依赖）
用法：python3 scripts/init_library.py /path/to/MyNovels
"""

import argparse
import datetime
import uuid
import re
import shutil
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = SKILL_ROOT / "templates"
HTML_SRC = SKILL_ROOT / "assets" / "novel-workbench.html"

DEMO_BOOK = "示例作品-雨夜来信"

DEMO_BIBLE = """---
type: novel-bible
book: "雨夜来信"
genre: "悬疑 / 轻奇幻"
status: 示例演示
target_words_per_chapter: 2800
---

# 总纲与世界观

## 基本信息

- **书名**：雨夜来信
- **题材与基调**：微悬疑、克制生活流
- **一句话梗概**：旧城区修表匠林安在停电雨夜收到一封寄自十年前的信，发现每修好一只旧怀表，就能听见一段尚未寄出的对话。
- **叙事视角**：第三人称有限视角（林安）

## 文风与避雷

- **想要的文字质感**：用具体物件、天气和小动作带情绪，对话自然有留白。
- **不要的 AI 腔**：不替读者做心理总结，不在段尾强行升华道理。
"""

DEMO_CHARS = """---
type: novel-characters
book: "雨夜来信"
updated_chapter: 1
---

# 人物状态卡

## 主角：林安

- **身份**：旧城区钟表铺店主，二十八岁
- **核心性格底色**：话少、手上稳、习惯先看细节再下判断
- **OOC 红线**：绝不情绪失控地长篇大论，不轻易把客人的旧物转手
- **当前动态**（更新至第 1 章）：
  - 所在位置：老街钟表铺柜台后
  - 持有物品：一只停在十一点四十分的黄铜怀表、一封未署名的牛皮纸信
  - 当前目标：弄清信封里那张当票为什么盖着十年前的日期
"""

DEMO_LEDGER = """---
type: novel-ledger
book: "雨夜来信"
---

# 伏笔与时间线

## 伏笔账本

| 编号 | 伏笔内容 | 埋设章节 | 计划回收窗口 | 状态 | 回收说明 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| F01 | 停在 11:40 的黄铜怀表背面刻着编号 047 | 第 1 章 | 第 4~6 章 | 待回收 | |

## 故事时间线

| 故事时间 | 对应章节 | 地点 | 关键事件 | 影响的人物 |
| :--- | :--- | :--- | :--- | :--- |
| 秋雨夜 21:30 | 第 1 章 | 旧城区钟表铺 | 街区停电，门缝下塞进一封牛皮纸信 | 林安 |
"""

DEMO_PROGRESS = """---
type: novel-progress
book: "雨夜来信"
latest_chapter: 1
next_chapter: 2
---

# 当前进度

- **最新已完成章节**：第 1 章 停电的钟表店
- **下一章编号**：第 2 章
- **当前阶段**：第一卷 开篇

## 上一章结尾锚点

- 时间与地点：停电后的老街钟表铺，雨越下越大
- 刚发生的画面：林安点亮应急台灯，拆开牛皮纸信封，里面滑出一张十年前的当票和一只旧怀表

## 下一章写作备忘

- 本章推进：林安借着台灯光检查怀表机芯，发现齿轮里卡着半张车票
- 伏笔提醒：呼应 F01 编号 047
"""

DEMO_CH1 = """---
type: novel-chapter
book: "雨夜来信"
chapter: 1
status: 示例初稿
---

# 第 1 章 停电的钟表店

九点半刚过，整条老街的灯同时暗了下去。

林安手里的镊子停在半空，没急着放下，先凭着记忆把刚拆下来的那颗游丝螺丝挪进左侧磁碟里。窗外的雨敲在铁皮雨棚上，声音闷成一片，玻璃上全是顺着往下淌的水线。

他摸出柜台底下的充电台灯按下开关，暖黄的光只照亮了工作台那一小块地方。

就在这时候，门缝底下传来一声很轻的摩擦声。

林安抬头看过去，卷帘门没完全拉死的那两寸空隙里，被塞进来一个边角发软的牛皮纸信封。外面没有脚步声，只有积水被车轮碾过去的一阵哗啦响。

他走过去把信封捡起来，入手比想象中沉。拆开封口，先滑出来的是一张泛黄的旧当票，日期印在十年前的秋天；紧跟着落在掌心的，是一只表盘停在十一点四十分的黄铜怀表。
"""

RULES_README = """# 写作规则与自定义 Skill 存放区

把你自己平时攒下来的写小说 Skill、文风要求、去 AI 味提示词或世界观参考放进这个目录。

- 写作台会自动隐藏下划线 `_` 开头的目录，保持主书架清爽。
- 点击写作台右侧「一键复制写作包」或调用本机 Agent 时，可以一并带上这里的规则。
"""

INDEX_MD = """---
type: bookshelf-index
---

# 书架总览

- [[示例作品-雨夜来信/00-总纲与世界观|示例作品-雨夜来信]]
- [[_写作规则与Skill/README|写作规则与 Skill 存放区]]
"""


def write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def install_app(target_dir: Path, update_app: bool = False) -> None:
    version = (SKILL_ROOT / "VERSION").read_text().strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or f'<meta name="workbench-version" content="{version}">' not in HTML_SRC.read_text():
        raise ValueError("版本标记不一致，请先运行 python3 scripts/sync_version.py")
    sources = [(SKILL_ROOT / "VERSION", "VERSION"), (HTML_SRC, "小说台.html"),
               (SKILL_ROOT / "assets/cli/server.py", "server.py"),
               (SKILL_ROOT / "assets/cli/library.py", "library.py"),
               (SKILL_ROOT / "assets/loki-themes.js", "loki-themes.js"),
               (SKILL_ROOT / "assets/cli/启动写作台.command", "启动写作台.command")]
    backup = None
    for source, name in sources:
        dest = target_dir / name
        if dest.exists() or dest.is_symlink():
            if not update_app:
                continue
            if dest.is_symlink() or not dest.is_file():
                raise ValueError(f"拒绝覆盖软链接或非文件：{dest}")
            if dest.read_bytes() == source.read_bytes():
                continue
            if backup is None:
                stamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")
                backup = target_dir / "_workbench-backups" / (stamp + "-" + uuid.uuid4().hex[:8])
                backup.mkdir(parents=True)
            shutil.copy2(dest, backup / name)
        shutil.copy2(source, dest)
    if backup:
        print(f"    应用文件备份：{backup}")


def init_library(target_dir: Path, update_app: bool = False) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. Copy templates
    tpl_out = target_dir / "_模板"
    tpl_out.mkdir(exist_ok=True)
    if TEMPLATES_DIR.exists():
        for f in TEMPLATES_DIR.glob("*.md"):
            dest = tpl_out / f.name
            if not dest.exists():
                shutil.copy2(f, dest)

    # 2. Rules folder & bookshelf index
    write_if_missing(target_dir / "_写作规则与Skill" / "README.md", RULES_README)
    write_if_missing(target_dir / "00-书架总览.md", INDEX_MD)

    write_if_missing(target_dir / "_style-cards" / "README.md", "# 风格卡\n每张卡保存为 Markdown；在工作台按书选择。\n")
    write_if_missing(target_dir / "_写作方法论" / "README.md", "# 写作 Skill\n每份方法保存为 Markdown；可按书勾选多份。\n")

    # 3. Clean fictional demo novel
    demo = target_dir / DEMO_BOOK
    write_if_missing(demo / "00-总纲与世界观.md", DEMO_BIBLE)
    write_if_missing(demo / "01-人物状态卡.md", DEMO_CHARS)
    write_if_missing(demo / "02-伏笔与时间线.md", DEMO_LEDGER)
    write_if_missing(demo / "03-当前进度.md", DEMO_PROGRESS)
    write_if_missing(demo / "正文" / "第01章-停电的钟表店.md", DEMO_CH1)

    # 4. Install UI and optional CLI runtime; never touch manuscripts on upgrade.
    install_app(target_dir, update_app)
    print(f"OK: 已初始化本地小说写作台 -> {target_dir}")
    print(f"    静态模式：打开 {target_dir / '小说台.html'} 并选择该文件夹。")
    print("    台内生成：双击 启动写作台.command，或在该目录运行 python3 server.py --open。")
    print("    已有安装：加 --update-app 备份并更新应用文件；正文和规则保持原样。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="初始化或升级本地小说工作台")
    parser.add_argument("target_dir", type=Path)
    parser.add_argument("--update-app", action="store_true", help="备份并更新 HTML、桥接服务和启动器，不覆盖小说")
    args = parser.parse_args()
    init_library(args.target_dir.expanduser().resolve(), args.update_app)
