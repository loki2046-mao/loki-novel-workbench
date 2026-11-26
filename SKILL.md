---
name: loki-novel-workbench
version: 1.0.1
description: Build a pure-local, zero-API-key personal novel writing workbench (loki-novel-workbench) inside a user's local folder using bundled templates, an eye-care single-page HTML editor, an init script, and the user's existing AI subscription. Use when the user sends the repository URL, asks to update 小说工作台 / this Skill / an existing workbench, or asks to build a local novel workbench, set up an Obsidian-compatible fiction writing space, organize novel drafts with character sheets and outlines, or create a zero-API writing tool powered by their existing AI agent subscription.
---

# loki-novel-workbench

Build a complete, pure-local, zero-API-key personal novel writing workbench. Instead of giving the user a wall of instructions, this skill ships a real scaffold script (`scripts/init_library.py`), five novel archive templates (`templates/`), a 49-theme eye-care HTML workbench (`assets/novel-workbench.html`), context-packing protocols (`references/`), and an optional local CLI bridge (`assets/cli/`).

## When to Use

- User wants to build a personal novel writing workspace on their computer without configuring paid API keys or cloud databases.
- User wants to organize novel chapters, character sheets, worldbuilding, foreshadowing ledgers, and progress notes into a clean Obsidian-compatible folder.
- User wants a distraction-free local HTML writing UI with split-pane reference view (outline/lore on one side, chapter prose on the other) and one-click context packing.

升级请求直接跳到下方「Agent 升级已有书架」，不要重新初始化。

## Steps

### Step 1: Confirm Target Directory

Ask the user which local folder should host their novel library (for example, `~/Documents/MyNovels`), unless they already specified a path.

### Step 2: Run the Scaffold Script

Run the bundled zero-dependency Python script to initialize the workspace safely (existing files are never overwritten):

```bash
python3 scripts/init_library.py "<target_dir>"
```

This creates:

- `00-书架总览.md` (Obsidian-compatible bookshelf index)
- `_模板/` (copied from `templates/`: `00-总纲与世界观.md`, `01-人物状态卡.md`, `02-伏笔与时间线.md`, `03-当前进度.md`, `章节模板.md`)
- `_写作规则与Skill/` (clean folder for the user's own style guides and writing skills)
- `示例作品-雨夜来信/` (generic fictional demo novel so the user can test immediately)
- `_style-cards/` and `_写作方法论/` (optional Markdown assets; bind one card and multiple skills per book)
- `server.py`, `library.py`, `loki-themes.js` and `启动写作台.command` (optional Python bridge and macOS launcher; no third-party Python dependencies)
- `小说台.html` (copied from `assets/novel-workbench.html`, 49 built-in eye-care themes, 5 writing modes, one-click Writing Pack copy; includes the bundled 35-theme kit plus 14 writing themes)

### Step 3: Guide the User Through First Launch and Writing Pack Flow

1. For static editing and clipboard Writing Packs, open `<target_dir>/小说台.html` in Chrome, Edge, or Arc and select `<target_dir>`. For in-page generation, run `python3 server.py --open` from the generated folder, or use the macOS launcher; the initialized library opens by default, and other libraries can be registered explicitly. Python 3.9+ is required for the bridge. If port 8788 is occupied, use `--port 8789`; the UI follows the serving origin.
2. To upgrade an existing installation, follow the Agent upgrade workflow below and run `python3 scripts/update_workbench.py "<target_dir>"` from the latest stable release. This backs up and updates only the UI, themes, server, library module and launcher; manuscripts, templates and author rules are preserved. Do not replace a user's custom UI without explaining that the old application files are backed up.
3. Follow `references/writing-pack-protocol.md` whenever the user asks to generate an outline, continue a chapter, polish prose, or sync post-chapter character/foreshadowing updates.
4. Follow `references/agent-workflow-guide.md` to match the user's preferred zero-API mode (direct desktop agent on the local folder, clipboard Writing Pack for web subscriptions, or optional local official CLI integration).

### Step 4: Verify Before Finishing

- Confirm `<target_dir>/小说台.html` and `<target_dir>/示例作品-雨夜来信/` exist.
- Confirm `node --check` passes if you customized any inline JavaScript in `小说台.html`.
- When enabling CLI mode, verify `/api/engines` returns the installed clients' model catalogs and defaults; test generation with a small non-private prompt. Do not invent model IDs when a lookup fails; retain the client default and supported manual entry.
- Preserve the installed CLI's actual capabilities: agy and Codex support per-request `--model`; the tested Cola `message` CLI follows the client's current global model and has no per-message model flag. Do not implement selection by silently changing Cola's global settings.
- In-page generation uses live SSE streaming (`/api/generate/stream`) with real-time chunk-by-chunk backup to `<target_dir>/_ai-drafts/`, backend subprocess termination on Stop (`/api/generate/cancel`) releasing the generation lock immediately, oversized `agy` prompt spill-to-`prompt.md` (avoiding macOS `ARG_MAX`), and `agy` post-output 30s idle auto-completion preventing 5m exit-code-3 hangs.
- Blank/template documents (<120 words), continue, and new chapter stream live directly into the main editor with real-time auto-save and conflict-checked one-click undo (including partial/interrupted runs), while the right sidebar shows only a compact progress status. Editing existing documents defaults to surgical Search/Replace patch blocks (`<<<<<<< 原文 / ======= / >>>>>>> 改为`) to avoid rewriting thousands of unchanged words, then opens zero-modal inline paper revision mode (single-page track changes or side-by-side split view, per-hunk accept/reject, accept/reject all, finish & save).
- Verify draft protection, navigation, and live metadata sync: 3-way unsaved guard (`Save & Continue / Discard / Stay`), `localStorage` crash recovery, `beforeunload` warning, non-destructive debounced auto-save preserving cursor/`⌘Z`/mid-save typing, browser Back/Forward (`history`/`popstate`), race-safe chapter switching, modal Esc/backdrop/in-flight creation locks, IME `isComposing` Enter guard, narrow-screen rightbar drawer, live sidebar word count sync, and automatic top-left book selector title sync from `00-总纲与世界观.md` (`book:` YAML or `- **书名**：...`).
- Verify multi-chapter revision with fictional numbered chapters: recent N, explicit ranges, whole book, manual 3/5/8/custom scope, prior-chapter continuity, full batch preview, conflict-safe writeback and batch undo. Preserve the existing explicit Confirm Write step. On partial write failure, retain an undo action for every successfully written chapter; never overwrite concurrent edits.
- Verify selectable chat text, Copy (including execCommand fallback), and refill of user messages.
- After confirmed continue/new chapter, sync the existing progress card's chapter count, U+4E00–U+9FFF count (body including headings, excluding YAML), local update date and generated ending/next-step summaries. Summary generation makes one additional CLI request; edit/batch/manual save/undo only sync statistics. Preserve author notes and refuse conflicting progress writes. Under Smart mode, core archives route automatically to editing the current archive with sibling core archives attached as reference context (never misrouted to prose continuation).
- Verify smart intent, chapter naming, same-chapter split view, optional asset folders and binding persistence using fictional data. Never copy a runtime `workspaces.json`, `assets-fallback.json`, `_ai-drafts/`, author card or manuscript into this repository.
- The service hot-reloads library.py; restarting remains necessary after server.py updates.
- Confirm no account credentials, personal library paths, private manuscripts or logs are bundled. The bridge inherits existing proxy variables and fills missing ones from macOS system settings without hardcoding a proxy address. An existing system network proxy is different from an untrusted model API gateway.
- Explain that storage is local, but an explicit generation request sends the assembled Writing Pack to the selected CLI's provider under that client's account and terms.

## Gotchas

- **Never require external API keys or third-party reverse proxies**: Reuse the user's existing subscription safely via desktop Agent file access, clipboard Writing Packs, or official locally installed CLIs.
- **Keep templates clean and fictional**: Never embed real people's names or private author style cards into public templates or demo chapters.
- **Avoid heavy stacks**: Keep the workbench zero-build (plain Markdown + single-file HTML) so manuscripts remain portable and never break on dependency updates.

## Agent 升级已有书架（优先于初始化步骤）

用户发来 https://github.com/loki2046-mao/loki-novel-workbench，或说「更新小说工作台 / 更新这个 Skill / 帮我升级写作台」时，走本流程。只有链接且无法判断安装还是升级时，先定位已有书架。用户已安装的 Skill 即使很旧，也无需先更新它；目标是升级已有书架的应用文件。

1. 使用以前初始化的路径；不知道时，在当前工作目录及附近按文件名找 `小说台.html` / `server.py`，不读取稿件。仍无法唯一定位时，只问一次书架路径，不新建替代书架。
2. 查询 https://github.com/loki2046-mao/loki-novel-workbench/releases/latest 对应的最新稳定 Release（排除 draft / prerelease）。可用 `gh api repos/loki2046-mao/loki-novel-workbench/releases/latest --jq .tag_name`。网络失败应说明原因并停止，不能把旧缓存称为最新。
3. 将官方仓库 clone 到临时目录，再检出该 Release 的准确 tag；或在干净的已有仓库中 fetch 并检出该 tag。不要覆盖仓库未提交修改，不要只 pull 默认分支就当作稳定版。读取该版本的 `SKILL.md`，核对 `VERSION` 与 tag 一致。获取最新应用文件与脚本即可，不要求更新已安装 Skill。
4. 在该稳定版仓库执行一条命令（由 Agent 执行，不交给用户）：

   ```bash
   python3 scripts/update_workbench.py "<已初始化的书架目录>"
   ```

   包装脚本离线运行，复用 `init_library.py --update-app` 的应用备份逻辑，只操作应用文件，不读写小说正文、作者规则或已有模板。旧应用文件（包括自定义页面）备份到 `_workbench-backups/`。若版本相同会成功退出，无需升级；本地更高版本不会被降级。若所检出的旧 Release 尚无包装脚本，Agent 执行 `python3 scripts/init_library.py "<书架目录>" --update-app`。
5. 实际发生升级且对应书架服务在跑时，确认进程属于该书架，按原启动方式、工作目录、端口及参数重启并验证 HTTP 可用；不要泛杀所有 Python 进程，不为静态用户额外启动服务。失败需明确汇报，不能称升级全部完成。
6. 汇报升级前后版本、备份目录及服务状态，明确正文、作者规则和已有模板没有被覆盖。已是最新则告诉用户并结束。

不要让用户自己敲 `git pull` 或 `--update-app`，不要把 Watch 当作必做步骤。写作台「检查更新」仍是手动操作，默认不自动联网，仅读取 GitHub 公开版本信息，不发送用户数据或遥测。

版本以根目录 `VERSION` 为唯一来源；维护者运行 `python3 scripts/sync_version.py` 同步本文件与 HTML。
