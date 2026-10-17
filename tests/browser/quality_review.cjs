/* 開発用。専用fixtureのローカルStreamlitだけを操作し、ChatGPTへは接続しない。 */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
(async()=>{
 const [baseURL,dataDir,outputDir='/tmp/manga-quality-browser-result']=process.argv.slice(2);
 assert(baseURL && dataDir);
 const projectId=fs.readFileSync(path.join(dataDir,'.quality-review-test-fixture'),'utf8').trim();
 assert.equal(fs.readdirSync(dataDir).filter(n=>n.endsWith('.json')).length,1);
 const root=path.join(dataDir,projectId),reviewFile=path.join(root,'review.json');
 assert(!fs.existsSync(reviewFile),'未確認の専用テスト作品だけを使用してください');
 const protectedFiles=['story_history.json','artwork.json','composition.json'];
 const before=Object.fromEntries(protectedFiles.map(n=>[n,fs.readFileSync(path.join(root,n),'utf8')]));
 fs.mkdirSync(outputDir,{recursive:true});
 let page;
 const browser=await chromium.launch({executablePath:process.env.MANGA_CHROMIUM||undefined,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try {
  page=await browser.newPage({viewport:{width:1440,height:1080}});const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',route=>{const u=new URL(route.request().url());return ['127.0.0.1','localhost'].includes(u.hostname)?route.continue():route.abort();});
  async function settle(){
   // Streamlitの入力確定（blur）とWebSocket再実行の完了を待つ。
   await page.waitForTimeout(150);
   await page.locator('[data-testid="stApp"][data-test-script-state="notRunning"]').waitFor({state:'attached',timeout:20000});
  }
  const btn=name=>({click:async()=>{await page.getByRole('button',{name,exact:true}).click();await settle();}});
  const data=()=>JSON.parse(fs.readFileSync(reviewFile,'utf8'));
  async function waitFor(check){for(let i=0;i<150;i++){if(check())return;await page.waitForTimeout(100);}throw Error('保存状態の待機がタイムアウト');}
  async function select(label,value){const field=page.getByTestId('stSelectbox').filter({has:page.getByText(label,{exact:true})});await field.getByRole('combobox').click();await page.getByRole('option',{name:value,exact:true}).click();await settle();}
  await page.goto(baseURL);
  await btn('選択したプロジェクトを開く').click();
  await page.getByText('完成・書き出し',{exact:true}).first().click();
  await btn('自動チェックを実行').click();
  await waitFor(()=>fs.existsSync(reviewFile)&&data().scans.length===1);
  await btn('ページサムネイルを作成・更新').click();
  await waitFor(()=>fs.existsSync(path.join(root,'review_previews')) && fs.readdirSync(path.join(root,'review_previews')).filter(n=>n.endsWith('.png')).length===8);
  await page.getByText('ページ一覧・手動レビュー',{exact:true}).click();await settle();
  await btn('1ページを確認').click();await btn('選択ページをプレビュー').click();
  await btn('このプレビューを目視確認した').click();await waitFor(()=>!!data().previews['1']);
  await btn('目視した7項目をすべて「問題なし」に設定').click();
  await btn('手動レビューを保存').click();await waitFor(()=>!!data().manual['1']);
  await page.getByText('目視で見つけた問題を修正タスクにする',{exact:true}).click();
  await select('修正対象のコマ（ページ全体も選択可）','2コマ目');
  await page.getByLabel('問題の内容（必須）',{exact:true}).fill('美咲の顔を基準画像と見比べる（ブラウザテスト）');
  await page.getByLabel('問題の内容（必須）',{exact:true}).press('Tab');await settle();
  await btn('目視指摘をタスクに登録').click();await waitFor(()=>data().tasks.length===1);
  await page.getByText('修正タスク',{exact:true}).click();await settle();
  await btn('このタスクの対象画面へ移動').click();
  await page.getByRole('heading',{name:'コマ制作ワークスペース',exact:true}).waitFor();
  assert((await page.getByTestId('stSelectbox').filter({has:page.getByText('画像を管理するコマ',{exact:true})}).innerText()).includes('2コマ'));
  await btn('品質チェックへ戻る').click();
  await select('修正状態','再確認待ち');await btn('タスクの状態を保存').click();
  await waitFor(()=>data().tasks[0].status==='再確認待ち');
  await select('修正状態','確認完了');
  await page.getByText('現在の保存済み画像・設定を人間が再確認した',{exact:true}).click();await settle();
  await btn('タスクの状態を保存').click();await waitFor(()=>data().tasks[0].status==='確認完了');
  await page.getByText('自動チェック・最終確認',{exact:true}).click();await settle();
  await page.getByText('未確認項目の警告を確認して書き出しへ進む',{exact:true}).click();await settle();
  await btn('PNG・PDF・縦読み書き出しへ').click();
  await page.getByRole('heading',{name:'PNG・PDF書き出し',exact:true}).waitFor();
  await btn('出力ファイルを生成').click();
  await page.getByRole('button',{name:'完成ファイルをダウンロード',exact:true}).waitFor();
  await page.screenshot({path:path.join(outputDir,'export-warning.png'),fullPage:true});
  for(const n of protectedFiles)assert.equal(fs.readFileSync(path.join(root,n),'utf8'),before[n]);
  assert.equal(data().finals['1'],undefined,'ブラウザ操作だけで自動最終確認にしていない');
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(outputDir,'browser-result.json'),JSON.stringify({passed:true,browser:await browser.version(),checks:['8ページ検査','8サムネイル','目視回答保存','1ページ2コマへの直接移動','品質画面へ戻る','修正後の再確認・完了','未確認を警告してPNG生成','制作JSONの保護'],externalRequests:'blocked',javascriptErrors:errors},null,2));
  console.log('PASS: 実ブラウザで品質確認・対象コマとの往復・再確認・警告付きPNG生成を検証');
 }catch(e){if(page){await page.screenshot({path:path.join(outputDir,'failure.png'),fullPage:true});fs.writeFileSync(path.join(outputDir,'failure.txt'),await page.locator('body').innerText());}throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
