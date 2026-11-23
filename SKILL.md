---
name: loki-novel-workbench
description: Build a pure-local, zero-API-key personal novel writing workbench (Loki Novel Workbench) inside a user's local folder using bundled templates, an eye-care single-page HTML editor, an init script, and the user's existing AI subscription. Use when the user asks to build a local novel workbench, set up an Obsidian-compatible fiction writing space, organize novel drafts with character sheets and outlines, or create a zero-API writing tool powered by their existing AI agent subscription.
---

# loki-novel-workbench

Build a complete, pure-local, zero-API-key personal novel writing workbench. Instead of giving the user a wall of instructions, this skill ships a real scaffold script (`scripts/init_library.py`), five novel archive templates (`templates/`), a 49-theme eye-care HTML workbench (`assets/novel-workbench.html`), context-packing protocols (`references/`), and an optional local CLI bridge (`assets/cli/`).

## When to Use

- User wants to build a personal novel writing workspace on their computer without configuring paid API keys or cloud databases.
- User wants to organize novel chapters, character sheets, worldbuilding, foreshadowing ledgers, and progress notes into a clean Obsidian-compatible folder.
- User wants a distraction-free local HTML writing UI with split-pane reference view (outline/lore on one side, chapter prose on the other) and one-click context packing.

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
2. To upgrade an existing installation, use `python3 scripts/init_library.py "<target_dir>" --update-app`. This backs up and updates only the UI, themes, server, library module and launcher; manuscripts, templates and author rules are preserved. Do not replace a user's custom UI without explaining that the old application files are backed up.
3. Follow `references/writing-pack-protocol.md` whenever the user asks to generate an outline, continue a chapter, polish prose, or sync post-chapter character/foreshadowing updates.
4. Follow `references/agent-workflow-guide.md` to match the user's preferred zero-API mode (direct desktop agent on the local folder, clipboard Writing Pack for web subscriptions, or optional local official CLI integration).

### Step 4: Verify Before Finishing

- Confirm `<target_dir>/小说台.html` and `<target_dir>/示例作品-雨夜来信/` exist.
- Confirm `node --check` passes if you customized any inline JavaScript in `小说台.html`.
- When enabling CLI mode, verify `/api/engines` returns the installed clients' model catalogs and defaults; test generation with a small non-private prompt. Do not invent model IDs when a lookup fails; retain the client default and supported manual entry.
- Preserve the installed CLI's actual capabilities: agy and Codex support per-request `--model`; the tested Cola `message` CLI follows the client's current global model and has no per-message model flag. Do not implement selection by silently changing Cola's global settings.
- Results enter a full preview first. Only an explicit Confirm Write action saves; cancel keeps the generated text in chat. Continue/edit/skill append support conflict-checked undo. New chapters remain separate drafts. Stop aborts reception and writeback but the upstream CLI may keep running.
- Verify smart intent, chapter naming, same-chapter split view, optional asset folders and binding persistence using fictional data. Never copy a runtime workspaces.json, assets-fallback.json, author card or manuscript into this repository.
- The service hot-reloads library.py; restarting remains necessary after server.py updates.
- Confirm no account credentials, personal library paths, private manuscripts or logs are bundled. The bridge inherits existing proxy variables and fills missing ones from macOS system settings without hardcoding a proxy address. An existing system network proxy is different from an untrusted model API gateway.
- Explain that storage is local, but an explicit generation request sends the assembled Writing Pack to the selected CLI's provider under that client's account and terms.

## Gotchas

- **Never require external API keys or third-party reverse proxies**: Reuse the user's existing subscription safely via desktop Agent file access, clipboard Writing Packs, or official locally installed CLIs.
- **Keep templates clean and fictional**: Never embed real people's names or private author style cards into public templates or demo chapters.
- **Avoid heavy stacks**: Keep the workbench zero-build (plain Markdown + single-file HTML) so manuscripts remain portable and never break on dependency updates.
