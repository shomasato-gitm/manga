/* 開発用。PlaywrightとローカルのStreamlitサーバーが必要。実データでは実行しない。 */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
(async()=>{
 const [baseURL,dataDir,outputDir='/tmp/manga-page-canvas-result']=process.argv.slice(2);
 assert(baseURL && dataDir,'URLと専用のテストデータ保存先を指定してください');
 const projectId=fs.readFileSync(path.join(dataDir,'.page-editor-test-fixture'),'utf8').trim();
 assert.equal(fs.readdirSync(dataDir).filter(n=>n.endsWith('.json')).length,1,'他のプロジェクトがある場所では実行しません');
 const configFile=path.join(dataDir,projectId,'composition.json');
 const before=JSON.parse(fs.readFileSync(configFile,'utf8'));
 assert.equal(Object.keys(before.pages).length,0,'未編集のテスト用プロジェクトだけを使ってください');
 fs.mkdirSync(outputDir,{recursive:true});
 const browser=await chromium.launch({executablePath:process.env.MANGA_CHROMIUM||undefined,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
 const page=await browser.newPage({viewport:{width:1440,height:1080}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/*',route=>{const u=new URL(route.request().url());return ['127.0.0.1','localhost'].includes(u.hostname)?route.continue():route.abort();});
 await page.goto(baseURL);
 await page.getByRole('button',{name:'選択したプロジェクトを開く',exact:true}).click();
 await page.getByText('漫画ページ編集',{exact:true}).first().click();
 const selector='iframe[title="manga.page_canvas_ui.manga_page_canvas"]';
 const canvas=page.frameLocator(selector).locator('canvas');
 await canvas.waitFor({timeout:20000});
 async function state(){const frame=page.frames().find(f=>f.url().includes('/component/manga.page_canvas_ui.'));return frame.evaluate(()=>({token:args.token,box:JSON.parse(JSON.stringify(boxes[0])),width:canvas.width,height:canvas.height,ready:background.complete&&background.naturalWidth>0}));}
 async function changed(token){for(let n=0;n<100;n++){const s=await state();if(s.ready&&s.token!==token)return s;await page.waitForTimeout(100);}throw Error('編集操作が反映されませんでした');}
 let initial=await state();
 for(let n=0;!initial.ready&&n<100;n++){await page.waitForTimeout(100);initial=await state();}
 assert(initial.ready);
 await canvas.scrollIntoViewIfNeeded();
 let bounds=await canvas.boundingBox();
 const absolute=s=>({x:s.box.frame.x+s.box.box.x*s.box.frame.width,y:s.box.frame.y+s.box.box.y*s.box.frame.height,w:s.box.box.width*s.box.frame.width,h:s.box.box.height*s.box.frame.height});
 let rect=absolute(initial);
 const point=(x,y,s)=>({x:bounds.x+x*bounds.width/s.width,y:bounds.y+y*bounds.height/s.height});
 let from=point(rect.x+rect.w/2,rect.y+rect.h/2,initial);
 await page.mouse.move(from.x,from.y);await page.mouse.down();await page.mouse.move(from.x-35,from.y+12,{steps:10});await page.mouse.up();
 let moved=await changed(initial.token);
 assert(moved.box.box.x<initial.box.box.x,'ドラッグで横位置が変わらない');
 assert.deepEqual(JSON.parse(fs.readFileSync(configFile,'utf8')),before,'ドラッグだけで保存データが変わった');
 await canvas.scrollIntoViewIfNeeded();bounds=await canvas.boundingBox();rect=absolute(moved);
 from=point(rect.x+rect.w,rect.y+rect.h,moved);
 await page.mouse.move(from.x,from.y);await page.mouse.down();await page.mouse.move(from.x+20,from.y+15,{steps:10});await page.mouse.up();
 const resized=await changed(moved.token);
 assert(resized.box.box.width>moved.box.box.width && resized.box.box.height>moved.box.box.height,'端のドラッグでサイズが変わらない');
 await page.getByRole('button',{name:'ページの編集を保存',exact:true}).click();
 await page.getByText('ページ編集を保存しました。完成プレビュー・出力に同じ配置を反映します。',{exact:true}).waitFor();
 const saved=JSON.parse(fs.readFileSync(configFile,'utf8'));
 const bubble=saved.pages['1'].bubbles.find(b=>b.id===resized.box.id);
 assert.deepEqual(bubble.box,resized.box.box);
 assert.equal(saved.pages['1'].bubbles.length,4);
 await canvas.screenshot({path:path.join(outputDir,'editor-canvas.png')});
 assert.deepEqual(errors,[]);
 fs.writeFileSync(path.join(outputDir,'browser-result.json'),JSON.stringify({passed:true,initial:initial.box.box,moved:moved.box.box,resized:resized.box.box,saved:bubble.box,browser:await browser.version(),externalRequests:'blocked',javascriptErrors:errors},null,2));
 console.log('PASS: 実ブラウザで選択・ドラッグ・リサイズ・明示保存・原稿の4枠を検証');
 } finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
