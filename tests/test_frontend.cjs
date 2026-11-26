// Offline frontend regressions. Run: node --test tests/test_frontend.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../assets/novel-workbench.html'), 'utf8');
function section(start, end) { return html.slice(html.indexOf(start), html.indexOf(end, html.indexOf(start))); }
function runtime() {
  const fields = {cliMultiScope:{value:'auto'},cliMultiRangeInput:{value:''},cliMessage:{textContent:''}};
  const context = vm.createContext({console, Date, DOMException, state:{currentFile:null}, selectedIntent:'auto', $:id=>fields[id]});
  vm.runInContext(section('    function parseCnCount(', '    async function copyPlainText(') +
    section('    const chapterNumber =', '    function formatNextChapterFilename('), context);
  vm.runInContext(`book={chapters:Array.from({length:20},(_,i)=>({name:'第'+(i+1)+'章',path:'正文/第'+(i+1)+'章.md'}))};
    state.currentFile=book.chapters[0];`,context);
  return {context,fields,run:s=>vm.runInContext(s,context)};
}
test('whole book and recent N never silently reduce the range',()=>{
  const {run}=runtime();
  assert.equal(run('resolveMultiChapters(book,"通篇").length'),20);
  assert.equal(run('resolveMultiChapters(book,"最近18章").length'),18);
  assert.equal(run('resolveMultiChapters(book,"最近几章")[0].name'),'第18章');
  assert.equal(run('resolveMultiChapters(book,"第2-4章").length'),3);
  assert.equal(run('resolveMultiChapters(book,"第90-99章").length'),0);
});
test('manual scope and invalid custom range are explicit',()=>{
  const {run,fields}=runtime(); run('selectedIntent="edit_multi"');
  for(const n of [3,5,8]){fields.cliMultiScope.value=String(n);assert.equal(run('resolveMultiChapters(book,"改").length'),n)}
  fields.cliMultiScope.value='custom';fields.cliMultiRangeInput.value='2,4,6';
  assert.equal(run('resolveMultiChapters(book,"改").map(c=>chapterNumber(c.name)).join(",")'),'2,4,6');
  fields.cliMultiRangeInput.value='999';assert.throws(()=>run('resolveMultiChapters(book,"改")'),/没有匹配/);
});
test('smart intent routes supported multi-chapter expressions',()=>{
  const {run,context}=runtime();
  vm.runInContext(section('    function detectIntent(', '    function parseCnCount('),context);
  for(const input of ['最近几章','最近5章去AI味','第2-4章对齐情节','通篇修订','多章修改'])assert.equal(run(`detectIntent(${JSON.stringify(input)})`),'edit_multi');
});
test('progress stats preserve notes and reject concurrent card edits',async()=>{
  const {run,context}=runtime();
  let card='---\nlatest_chapter: 0\n---\n# 进度\n\n作者备忘：不可覆盖\n';
  let conflict=false;
  Object.assign(context, {
    readHandleText:async h=>h==='card'?card:'# 第一章\n汉字 ABC 123。',
    writeHandleText:async(h,text,expected)=>{if(conflict)card+='作者并发修改';if(card!==expected)throw Error('conflict');card=text},
    toast:()=>{}, chatHistory:[], renderChatLog:()=>{},
  });
  vm.runInContext(section('    function parseFrontmatter(', '    function countWords(') +
    section('    function countCnWords(', '    function parseForeshadowing(') +
    section('    function findProgressFile(', '    async function saveCurrentFile('),context);
  run('book={name:"Fictional",coreFiles:[{path:"进度.md",handle:"card"}],chapters:[{name:"第1章",path:"正文/第1章.md",handle:"chapter"}]};state.currentBook=book;state.rootHandle={};state.currentFile=null;');
  const result=await run('syncBookProgress(book)');
  assert.equal(result.updated,true);assert.equal(result.totalCnWords,5);assert(card.includes('作者备忘：不可覆盖'));assert(card.includes('chapters: 1'));assert(card.includes('words_cn: 5'));
  const previous=card;conflict=true;const failed=await run('syncBookProgress(book)');
  assert.equal(failed.updated,false);assert.equal(card,previous+'作者并发修改');
});

function navigationRuntime() {
  const makeClassList = (initial = []) => {
    const set = new Set(initial);
    return {
      add: (...cls) => cls.forEach(c => set.add(c)),
      remove: (...cls) => cls.forEach(c => set.delete(c)),
      toggle: (c, force) => force === undefined ? (set.has(c) ? set.delete(c) : set.add(c)) : (force ? set.add(c) : set.delete(c)),
      contains: c => set.has(c),
    };
  };
  const elements = {
    editorArea: { value: '' },
    bookSelect: { value: '' },
    docTitle: { textContent: '' },
    dirtyDot: { classList: makeClassList() },
    btnSave: { disabled: true },
    btnZenSave: { disabled: true },
    btnToggleEdit: { disabled: true },
    btnCopyPack: { disabled: true },
    btnCopyPackSide: { disabled: true },
    statusSaved: { textContent: '' },
    cliMessage: { textContent: '' },
    modalBook: { classList: makeClassList() },
    modalChapter: { classList: makeClassList() },
  };
  const storage = new Map();
  const historyStates = [];
  const context = vm.createContext({
    console, Date, DOMException, encodeURIComponent,
    localStorage: {
      getItem: k => storage.has(k) ? storage.get(k) : null,
      setItem: (k, v) => storage.set(k, String(v)),
      removeItem: k => storage.delete(k),
    },
    history: {
      state: null,
      pushState(s) { this.state = s; historyStates.push({ op: 'push', s }); },
      replaceState(s) { this.state = s; historyStates.push({ op: 'replace', s }); },
    },
    confirm: () => false,
    $: id => elements[id],
    toast: () => {},
    renderCurrentDoc: () => {},
    renderNav: () => {},
    updateRightPanel: () => {},
    applyWsMode: () => {},
    syncBookBindingUI: () => {},
    renderChatLog: () => {},
    updateCliButtons: () => {},
    syncBookProgress: async () => ({ updated: true }),
    parseForeshadowing: () => [],
    countWords: s => (s || '').replace(/\s+/g, '').length,
    countCnWords: s => ((s || '').match(/[\u4e00-\u9fff]/g) || []).length,
    serviceMode: true,
    chatRoot: null, chatBook: '', chatHistory: [], undoSnapshots: {},
    lastAiBaseline: { filePath: '', text: '', source: '' },
  });
  const initSnippet = section('    const state = {', '    const $ = (id) =>');
  const selectSnippet = section('    async function selectBook(', '    function renderNav(');
  const openSnippet = section('    function commitOpenedFile(', '    function renderCurrentDoc(');
  const saveSnippet = section('    async function saveCurrentFile(', '    let splitRefPath =');
  const modalSnippet = section('    function openModal(', '    async function openNewBookModal(');
  vm.runInContext('const prefs = {};\n' + initSnippet + '\n' + selectSnippet + '\n' + openSnippet + '\n' + saveSnippet + '\n' + modalSnippet, context);
  return { context, elements, historyStates, run: s => vm.runInContext(s, context) };
}

test('canceling book switch keeps book, editor, chat, and undo history intact', async () => {
  const { context, elements, run } = navigationRuntime();
  context.readHandleText = async h => h.text;
  context.askUnsavedChoice = async () => 'cancel';
  run(`
    const root = { ws: { id: 'ws1' }, name: 'shelf' };
    const ch1 = { path: '正文/第1章.md', name: '第1章', isChapter: true, words: 4, handle: { text: '旧书原稿' } };
    const ch2 = { path: '正文/第1章.md', name: '新书第1章', isChapter: true, words: 4, handle: { text: '新书内容' } };
    const bookA = { name: '旧书', coreFiles: [], chapters: [ch1], totalWords: 4 };
    const bookB = { name: '新书', coreFiles: [], chapters: [ch2], totalWords: 4 };
    state.rootHandle = root;
    state.books = [bookA, bookB];
    state.currentBook = bookA;
    state.currentFile = ch1;
    state.savedText = '旧书原稿';
    state.dirty = true;
    chatRoot = root; chatBook = '旧书';
    chatHistory = [{ role: 'user', text: '保留对话' }];
    undoSnapshots = { u1: { prevText: '旧书原稿' } };
    $('editorArea').value = '旧书未保存修改';
    $('bookSelect').value = '新书';
  `);
  const switched = await run('selectBook("新书")');
  assert.equal(switched, false);
  assert.equal(run('state.currentBook.name'), '旧书');
  assert.equal(run('state.currentFile.name'), '第1章');
  assert.equal(elements.bookSelect.value, '旧书');
  assert.equal(elements.editorArea.value, '旧书未保存修改');
  assert.equal(run('chatHistory.length'), 1);
  assert.equal(run('Object.keys(undoSnapshots).length'), 1);
});

test('rapid chapter switching ignores slow stale read and preserves doc on read error', async () => {
  const { context, elements, run } = navigationRuntime();
  let resolveSlow;
  context.readHandleText = h => {
    if (h === 'slow') return new Promise(r => { resolveSlow = r; });
    if (h === 'fast') return Promise.resolve('第二章内容');
    if (h === 'broken') return Promise.reject(new Error('磁盘读取失败'));
    return Promise.resolve('初始');
  };
  run(`
    const root = { ws: { id: 'ws1' }, name: 'shelf' };
    const ch1 = { path: '正文/第1章.md', name: '第1章', isChapter: true, handle: 'slow' };
    const ch2 = { path: '正文/第2章.md', name: '第2章', isChapter: true, handle: 'fast' };
    const ch3 = { path: '正文/第3章.md', name: '第3章', isChapter: true, handle: 'broken' };
    state.rootHandle = root;
    state.currentBook = { name: '测试书', coreFiles: [], chapters: [ch1, ch2, ch3] };
    state.books = [state.currentBook];
    globalThis.__chs = { ch1, ch2, ch3 };
  `);
  const p1 = run('openFile(__chs.ch1)');
  const p2 = run('openFile(__chs.ch2)');
  assert.equal(await p2, true);
  assert.equal(run('state.currentFile.name'), '第2章');
  assert.equal(elements.editorArea.value, '第二章内容');
  resolveSlow('第一章迟到内容');
  assert.equal(await p1, false);
  assert.equal(run('state.currentFile.name'), '第2章');
  assert.equal(elements.editorArea.value, '第二章内容');
  assert.equal(await run('openFile(__chs.ch3)'), false);
  assert.equal(run('state.currentFile.name'), '第2章');
  assert.equal(elements.editorArea.value, '第二章内容');
});

test('saving preserves editor content typed mid-save without overwriting', async () => {
  const { context, elements, run } = navigationRuntime();
  let savedPayload = null;
  context.writeHandleText = async (h, text) => {
    savedPayload = text;
    elements.editorArea.value = text + '继续敲的字';
  };
  run(`
    const ch1 = { path: '正文/第1章.md', name: '第1章', isChapter: true, words: 0, handle: 'h1' };
    state.rootHandle = { ws: { id: 'ws1' }, name: 'shelf' };
    state.currentBook = { name: '测试书', coreFiles: [], chapters: [ch1], totalWords: 0 };
    state.currentFile = ch1;
    state.savedText = '旧稿';
    state.dirty = true;
    $('editorArea').value = '保存时的正文';
  `);
  const ok = await run('saveCurrentFile()');
  assert.equal(ok, true);
  assert.equal(savedPayload, '保存时的正文');
  assert.equal(elements.editorArea.value, '保存时的正文继续敲的字');
  assert.equal(run('state.dirty'), true);
  assert.equal(elements.btnSave.disabled, false);
});

test('modal cannot be dismissed while creation is in-flight and AI undo preserves manual edits', () => {
  const { elements, run } = navigationRuntime();
  elements.modalChapter.classList.add('open');
  run('isCreatingChapter = true');
  assert.equal(run('closeModal("modalChapter")'), false);
  assert.equal(elements.modalChapter.classList.contains('open'), true);
  run('isCreatingChapter = false');
  assert.equal(run('closeModal("modalChapter")'), true);
  assert.equal(elements.modalChapter.classList.contains('open'), false);
  assert.equal(html.includes('prevText: diskBefore'), false);
  assert.equal(html.includes('!e.isComposing'), true);
});

test('selective diff review accepts or rejects individual hunks cleanly', () => {
  const ctx = vm.createContext({ escapeHtml: s => s });
  vm.runInContext(section('    function computeDiffSegments(', '    function reviewGenerated('), ctx);
  const oldText = '第一段保持不变\n第二段旧内容\n第三段保持不变\n第四段要删掉\n第五段保持不变';
  const newText = '第一段保持不变\n第二段新改写\n第三段保持不变\n第五段保持不变\n第六段新增';
  const segs = vm.runInContext(`computeDiffSegments(${JSON.stringify(oldText)}, ${JSON.stringify(newText)})`, ctx);
  const hunks = segs.filter(s => s.type === 'change');
  assert.equal(hunks.length, 3);
  assert.equal(hunks.map(h => h.kind).join(','), 'mod,del,add');
  assert.equal(ctx.composeDiffSegments(segs), newText);
  hunks[1].accepted = false;
  assert.equal(ctx.composeDiffSegments(segs), '第一段保持不变\n第二段新改写\n第三段保持不变\n第四段要删掉\n第五段保持不变\n第六段新增');
  hunks.forEach(h => { h.accepted = false; });
  assert.equal(ctx.composeDiffSegments(segs), oldText);
});

test('applyEditPatches applies surgical search-and-replace blocks without rewriting whole doc', () => {
  const ctx = vm.createContext({ escapeHtml: s => s, stripCodeFence: s => String(s || '').trim() });
  vm.runInContext(section('    function computeDiffSegments(', '    function reviewGenerated('), ctx);
  const original = '---\nbook: "旧书名"\n---\n# 总纲\n- **书名**：旧书名\n正文保持四千字不变。';
  const patchOut = [
    '<<<<<<< 原文',
    'book: "旧书名"',
    '=======',
    'book: "雨夜来信"',
    '>>>>>>> 改为',
    '',
    '<<<<<<< 原文',
    '- **书名**：旧书名',
    '=======',
    '- **书名**：雨夜来信',
    '>>>>>>> 改为',
  ].join('\n');
  const res = ctx.applyEditPatches(original, patchOut);
  assert.equal(res.isPatch, true);
  assert.equal(res.applied, 2);
  assert.equal(res.failed, 0);
  assert.equal(res.text, '---\nbook: "雨夜来信"\n---\n# 总纲\n- **书名**：雨夜来信\n正文保持四千字不变。');
  // Fallback when full document is returned
  const full = ctx.applyEditPatches(original, '完整新文档');
  assert.equal(full.isPatch, false);
  assert.equal(full.text, '完整新文档');
});

function updateRuntime(fetchImpl, timeout = setTimeout) {
  const fields = {btnCheckUpdate:{disabled:false,addEventListener:(event,fn)=>{fields.click=fn}},updateStatus:{textContent:''},localVersion:{textContent:''}};
  const context = vm.createContext({document:{querySelector:()=>({content:'1.0.0'}),getElementById:id=>fields[id]},fetch:fetchImpl,AbortController,setTimeout:timeout,clearTimeout});
  vm.runInContext(section('    // Manual public release lookup:', '    // End manual release lookup.'),context);
  return {fields,run:s=>vm.runInContext(s,context)};
}
test('update lookup is opt-in and sends only a fixed anonymous public GET',async()=>{
  const calls=[];
  const {fields}=updateRuntime(async(...args)=>{calls.push(args);return {ok:true,json:async()=>({tag_name:'v1.2.0',body:'<script>untrusted</script>'})}});
  assert.equal(calls.length,0);
  await fields.click();
  assert.equal(calls.length,1);
  assert.equal(calls[0][0],'https://api.github.com/repos/loki2046-mao/loki-novel-workbench/releases/latest');
  const options=calls[0][1];
  assert.deepEqual(Object.keys(options).sort(),['cache','credentials','method','referrerPolicy','signal']);
  assert.equal(options.credentials,'omit');assert.equal(options.referrerPolicy,'no-referrer');assert.equal(options.method,'GET');
  assert.ok(!fields.updateStatus.textContent.includes('git pull'));
  assert.ok(!fields.updateStatus.textContent.includes('--update-app'));
  for(const phrase of ['https://github.com/loki2046-mao/loki-novel-workbench','帮我更新小说工作台','_workbench-backups/','小说正文、作者规则和已有模板不会被覆盖']) assert.ok(fields.updateStatus.textContent.includes(phrase));
  assert.equal(fields.btnCheckUpdate.disabled,false);
});
test('offline, CORS, rate limit, no release, malformed response and timeout leave writing unblocked and allow retry',async()=>{
  for(const fetchImpl of [async()=>{throw Error('offline/CORS')},async()=>({ok:false,status:403}),async()=>({ok:false,status:404}),async()=>({ok:true,json:async()=>({tag_name:'bad'})}),async()=>({ok:true,json:async()=>{throw Error('bad JSON')}}),(_url,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(Error('timeout'))))]) {
    const {fields}=updateRuntime(fetchImpl,fn=>setTimeout(fn,5));
    await fields.click();
    assert.match(fields.updateStatus.textContent,/不影响写作/);
    assert.equal(fields.btnCheckUpdate.disabled,false);
    await fields.click();
    assert.equal(fields.btnCheckUpdate.disabled,false);
  }
});
test('stable version comparison is numeric and never proposes a downgrade',async()=>{
  const {run,fields}=updateRuntime(async()=>({ok:true,json:async()=>({tag_name:'v1.0.0'})}));
  assert.equal(run('isNewerRelease("v1.10.0","1.9.0")'),true);
  assert.equal(run('isNewerRelease("v1.0.0","1.0.1")'),false);
  assert.throws(()=>run('isNewerRelease("v1.1.0-beta.1","1.0.0")'));
  await fields.click();assert.match(fields.updateStatus.textContent,/已是最新/);
});
