---
name: loki-novel-workbench
description: Build a pure-local, zero-API-key personal novel writing workbench (Loki Novel Workbench) inside a user's local folder using bundled templates, an eye-care single-page HTML editor, an init script, and the user's existing AI subscription. Use when the user asks to build a local novel workbench, set up an Obsidian-compatible fiction writing space, organize novel drafts with character sheets and outlines, or create a zero-API writing tool powered by their existing AI agent subscription.
---

# loki-novel-workbench

Build a complete, pure-local, zero-API-key personal novel writing workbench. Instead of giving the user a wall of instructions, this skill ships a real scaffold script (`scripts/init_library.py`), five novel archive templates (`templates/`), a standalone 6-theme eye-care HTML workbench (`assets/novel-workbench.html`), and context-packing protocols (`references/`).

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
- `小说台.html` (copied from `assets/novel-workbench.html`, 6 eye-care themes, 4 writing modes, one-click Writing Pack copy)

### Step 3: Guide the User Through First Launch and Writing Pack Flow

1. Tell the user to open `<target_dir>/小说台.html` in Chrome, Edge, or Arc and select `<target_dir>`.
2. Follow `references/writing-pack-protocol.md` whenever the user asks to generate an outline, continue a chapter, polish prose, or sync post-chapter character/foreshadowing updates.
3. Follow `references/agent-workflow-guide.md` to match the user's preferred zero-API mode (direct desktop agent on the local folder, clipboard Writing Pack for web subscriptions, or optional local official CLI integration).

### Step 4: Verify Before Finishing

- Confirm `<target_dir>/小说台.html` and `<target_dir>/示例作品-雨夜来信/` exist.
- Confirm `node --check` passes if you customized any inline JavaScript in `小说台.html`.
- Confirm zero API keys, zero third-party proxy URLs, and zero private personal data appear anywhere in generated files.

## Gotchas

- **Never require external API keys or third-party reverse proxies**: Reuse the user's existing subscription safely via desktop Agent file access, clipboard Writing Packs, or official locally installed CLIs.
- **Keep templates clean and fictional**: Never embed real people's names or private author style cards into public templates or demo chapters.
- **Avoid heavy stacks**: Keep the workbench zero-build (plain Markdown + single-file HTML) so manuscripts remain portable and never break on dependency updates.
