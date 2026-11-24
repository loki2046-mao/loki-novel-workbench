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
