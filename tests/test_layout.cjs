// Run against an initialized fictional library, with its server already running:
// node tests/test_layout.cjs http://127.0.0.1:8789 output/playwright
// Requires Playwright and Chrome; PLAYWRIGHT_MODULE can point to an existing installation.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const url = process.argv[2] || 'http://127.0.0.1:8789';
const output = process.argv[3] || 'output/playwright';
(async () => {
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({channel:'chrome',headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:900}});
    const errors = [], results = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => { if(m.type()==='error') errors.push(m.text()); });
    await page.route('**/api/engines*', r=>r.fulfill({json:{ok:true,engines:[{engine:'codex',available:true,supportsModel:true,models:[{id:'fixture',name:'Fixture'}],defaultModel:'fixture'}]}}));
    await page.route('**/api/generate', r=>r.abort());
    await page.goto(url);
    await page.getByRole('button',{name:/第01章-停电/}).click();
    await page.waitForFunction(()=>!!state.currentFile && !!document.querySelector('.prose').textContent.trim());
    for (const theme of ['loki-ease','loki-ease-dark','loki-moss','sky-cinema']) {
      await page.evaluate(theme=>applyTheme(theme),theme);
      await page.evaluate(()=>new Promise(requestAnimationFrame));
      const result = await page.evaluate(()=>{
        const rect = s => { const r=document.querySelector(s).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}; };
        return {theme:state.themeId,main:rect('.main'),children:['.doc-toolbar','.meta-bar','.panes'].map(rect),pane:rect('.pane-preview'),prose:rect('.prose'),input:rect('#cliChatInput'),send:rect('#cliSendBtn'),chat:rect('#cliChatLog'),assetStatus:rect('#cliAssetStatus'),assetsOpen:document.querySelector('#cliAssetsBox').open,scroll:document.querySelector('.rightbar').scrollTop,editorWidth:parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--editor-width')),overflow:document.documentElement.scrollWidth>innerWidth};
      });
      assert.equal(result.theme,theme);
      assert.equal(result.assetsOpen,false);
      assert.equal(result.scroll,0);
      assert.equal(result.overflow,false);
      for (const r of result.children) {assert(Math.abs(r.x-result.main.x)<2,theme+' left edge');assert(Math.abs(r.right-result.main.right)<2,theme+' right edge');}
      for (const key of ['input','send','assetStatus']) {const r=result[key];assert(r.width>0 && r.height>0 && r.y>=0 && r.bottom<=900,theme+' '+key+' first viewport');}
      assert(Math.abs(result.prose.x+result.prose.width/2-result.pane.x-result.pane.width/2)<2,theme+' centered prose');
      assert(result.prose.width<=result.editorWidth+1);
      assert(result.chat.height>=76 && result.chat.height<=180);
      results.push(result);
      await page.screenshot({path:path.join(output,theme+'.png')});
    }
    // Short documents must keep the available reading-card width instead of shrinking to text.
    const short=await page.evaluate(()=>{const p=document.querySelector('.prose');const before=p.getBoundingClientRect().width;p.innerHTML='<p>雨。</p>';return {before,after:p.getBoundingClientRect().width};});
    assert.equal(short.before,short.after);
    // Long conversations remain scrollable within the capped log; expanding assets is still usable.
    const long=await page.evaluate(()=>{chatHistory=Array.from({length:20},()=>({role:'ai',text:'虚构对话测试。'.repeat(30)}));renderChatLog();const box=document.querySelector('#cliChatLog');return {height:box.getBoundingClientRect().height,scrollHeight:box.scrollHeight,clientHeight:box.clientHeight};});
    assert(long.height<=180 && long.scrollHeight>long.clientHeight);
    await page.locator('#cliAssetsBox summary').click();
    assert.equal(await page.locator('#cliAssetsBox').evaluate(e=>e.open),true);
    assert.deepEqual(errors,[]);
    const report={viewport:{width:1440,height:900},results,short,long,errors};
    fs.writeFileSync(path.join(output,'layout-results.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report,null,2));
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
