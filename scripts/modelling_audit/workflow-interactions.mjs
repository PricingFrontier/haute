import { chromium } from '../../frontend/node_modules/playwright/index.mjs';
import { expect } from '../../frontend/node_modules/@playwright/test/index.mjs';
import fs from 'node:fs';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { parseArgs } from 'node:util';
const { values } = parseArgs({ options: {
  project: { type: 'string' }, output: { type: 'string' },
  'source-file': { type: 'string', default: 'rating/main.py' },
  resume: { type: 'boolean', default: false },
  'full-only': { type: 'boolean', default: false },
  url: { type: 'string', default: 'http://127.0.0.1:8012' },
  node: { type: 'string', default: 'freq' },
  'table-budget': { type: 'string', default: '42' },
  tolerance: { type: 'string', default: '0.75' },
  strength: { type: 'string', default: '4' },
  chromium: { type: 'string' },
} });
if (!values.project || !values.output) throw Error('--project and --output are required; use a disposable project');
const root=path.resolve(values.output);
fs.mkdirSync(root,{recursive:true});
const browser=await chromium.launch({headless:true,...(values.chromium ? {executablePath:values.chromium} : {})});
let page,latest,args;
const report={started:Date.now(),options:values,requests:[],errors:[],warnings:[],milestones:[]};
const starts=new Map();const pending=[];
const stateCache=new Map();
// Read the store's pointer and revision manifests so audit polling adds no HTTP traffic.
// A manifest's result envelope keeps its small fields inline (paths, selected stage,
// metrics, features); its diagnostic references are not hydrated and are not read here.
function stateFromDisk(){
 if(!args)return null;
 const key=createHash('sha256').update(JSON.stringify([args.graph.source_file??values['source-file'],args.node_id,args.model])).digest('hex');
 const store=path.join(values.project,'.haute','staged-training',key);
 if(!fs.existsSync(path.join(store,'manifest.json')))return null;
 const state=JSON.parse(fs.readFileSync(path.join(store,'manifest.json'),'utf8'));
 for(const name of ['current','preview','final']){
  const record=state[name];
  if(record){
   if(!stateCache.has(record.revision)){
    stateCache.set(record.revision,JSON.parse(fs.readFileSync(path.join(store,'revisions',`${record.revision}.json`),'utf8')).result);
   }
   record.result=stateCache.get(record.revision);
  }
 }
 return state;
}

function mark(name,extra={}){const data={name,at:Date.now(),seconds:(Date.now()-report.started)/1000,...extra};report.milestones.push(data);console.log(JSON.stringify(data));}
try {
 page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(30000);
 page.on('pageerror',e=>report.errors.push({kind:'pageerror',message:e.message}));
 page.on('console',e=>{if(['error','warning'].includes(e.type()))report.warnings.push({kind:e.type(),message:e.text()});});
 await page.addInitScript(()=>{window.auditLongTasks=[];new PerformanceObserver(list=>{for(const e of list.getEntries())window.auditLongTasks.push({start:e.startTime,ms:e.duration});}).observe({entryTypes:['longtask']});});
 page.on('request',r=>{starts.set(r,Date.now());if(r.url().endsWith('/api/modelling/stages/read'))args=r.postDataJSON();});
 page.on('requestfinished',r=>{const task=(async()=>{if(!r.url().includes('/api/'))return;const response=await r.response();let body=null;const sizes=await r.sizes();let bytes=sizes.responseBodySize;if(!response.ok()||r.url().endsWith('/train')){try{body=await response.json();}catch{}}
 const row={path:new URL(r.url()).pathname,status:response.status(),ms:Date.now()-starts.get(r),bytes,diagnostics:r.method()==='POST'?r.postDataJSON()?.diagnostics:undefined,at:Date.now()};report.requests.push(row);
 if(!response.ok()){row.error=body;report.errors.push(row);console.log('HTTP ERROR',JSON.stringify(row));}
 
 if(r.url().endsWith('/train'))mark('train submitted',{job_id:body?.job_id});
 })();pending.push(task);});
 await page.goto(values.url);await page.waitForTimeout(2000);
 if(await page.getByRole('button',{name:'Cancel',exact:true}).isVisible())await page.getByRole('button',{name:'Cancel',exact:true}).click();
 await page.getByTestId('pipeline-view').getByTestId('view-switcher-modelling').click();
 const view=page.getByTestId('modelling-view');await view.getByRole('button',{name:values.node,exact:true}).click();
 await view.getByRole('tab',{name:'Training',exact:true}).click();
 const developmentAcknowledgement=view.getByRole('checkbox',{name:'Use development rows for exploration'});
 if(await developmentAcknowledgement.isVisible())await developmentAcknowledgement.check();
 await expect(view.getByRole('button',{name:'Train GBM',exact:true})).toBeEnabled();
 await expect.poll(()=>Boolean(args)).toBeTruthy();
 latest=stateFromDisk();if(latest?.current&&!values.resume)throw Error('Use a fresh disposable project; an applied model already exists');
 if(values['full-only']) await page.evaluate(()=>{window.auditProgress=[];window.auditProgressTimer=setInterval(()=>{const bar=document.querySelector('[data-testid=modelling-view] [role=progressbar]');window.auditProgress.push({at:Date.now(),value:bar?Number(bar.getAttribute('aria-valuenow')):null});},100);});
 if(!latest?.current){mark('start full');await view.getByRole('button',{name:'Train GBM',exact:true}).click();}else{mark('resume applied model');}
 if(values['full-only']) {
  await view.getByRole('tab',{name:'Learning curve',exact:true}).click();
  await expect(view.getByRole('checkbox',{name:'Bag 8',exact:true})).toBeVisible({timeout:120000});
  await page.screenshot({path:root+'/learning-live.png'});
 }
 for(const [stage,label] of [['pruned','Pruning'],['banded','Banding'],['smoothed','Smoothing']]){
  await expect.poll(()=>{latest=stateFromDisk();return Boolean((latest?.preview?.result?.response?.staged?.paths?.[stage]||latest?.current?.result?.response?.staged?.paths?.[stage])&&latest?.job?.status!=='running');},{timeout:900000,intervals:[1000]}).toBeTruthy();
  const state=latest;const path=(state.preview?.result.response.staged.paths[stage]??state.current.result.response.staged.paths[stage]);mark(stage+' path ready',{points:path.points.length,job:state.job});
  if(values['full-only']) break;
  await view.getByRole('tab',{name:'Paths',exact:true}).click();await view.getByRole('tab',{name:label,exact:true}).click();
  const candidates=path.points.filter(p=>!p.identity&&p.status!=='rejected');
  let selected=stage==='pruned'? candidates.find(p=>p.diagnostics.table_budget===Number(values['table-budget'])):stage==='smoothed'?candidates.find(p=>p.settings.strength===Number(values.strength)):candidates.find(p=>p.settings.tolerance===Number(values.tolerance));
  if(!selected)throw Error('Requested point unavailable for '+stage+': '+JSON.stringify(candidates.map(p=>({settings:p.settings,diagnostics:p.diagnostics}))));
  report.milestones.at(-1).chosen={id:selected.id,settings:selected.settings,diagnostics:selected.diagnostics};
  const paths=view.getByRole('tabpanel',{name:'Paths',exact:true});
  mark(stage+' select');await paths.locator('[aria-label="Path points"] > button').nth(path.points.findIndex(p=>p.id===selected.id)).click();
  await expect(paths.getByRole('button',{name:'Apply selected',exact:true})).toBeEnabled({timeout:60000});mark(stage+' selection rendered');
  await page.screenshot({path:`${root}/${stage}.png`});
  const applied=page.waitForResponse(r=>r.url().endsWith('/stages/path/apply')&&r.request().method()==='POST',{timeout:90000});
  mark(stage+' apply start');await paths.getByRole('button',{name:'Apply selected',exact:true}).click();const response=await applied;if(!response.ok())throw Error(await response.text());mark(stage+' apply response');
  if(stage==='smoothed'){await expect(paths.getByRole('button',{name:'Applied',exact:true})).toBeDisabled();break;}
 }
 if(!values['full-only']) {
 await view.getByRole('tab',{name:'Factor',exact:true}).click();
 await view.getByRole('tab',{name:'Features',exact:true}).click();
 const firstFeature=latest.current.result.response.features[0];
 await view.getByRole('button',{name:firstFeature,exact:true}).click();
 await page.waitForTimeout(1000);
 await page.screenshot({path:root+'/factor.png'});
 await view.getByRole('tab',{name:'Training',exact:true}).click();
 await expect(view.getByText('Candidate ready',{exact:true})).toHaveCount(0);
 await view.getByRole('tab',{name:'Tables',exact:true}).click();
 await expect(view.locator('[role=alert]')).toHaveCount(0);
 await expect(view.getByRole('combobox',{name:'Table stage'})).toBeVisible();
 await expect.poll(()=>report.requests.some(r=>r.path==='/api/modelling/stages/read'&&r.diagnostics==='full')||report.requests.some(r=>r.path==='/api/modelling/stages/path/read'&&r.diagnostics==='full'),{timeout:60000}).toBeTruthy();
 await expect(view.getByText('Loading tables…',{exact:true})).toHaveCount(0,{timeout:60000});
 } else {
  await expect(view.getByRole('progressbar')).toHaveCount(0,{timeout:30000});
  await expect(view.getByRole('checkbox',{name:'Mean of bags',exact:true})).toBeVisible();
  await page.screenshot({path:root+'/learning-validation.png'});
  await view.getByRole('button',{name:'Training',exact:true}).click();
  await page.screenshot({path:root+'/learning-training.png'});
  await view.getByRole('checkbox',{name:'Bag 1',exact:true}).uncheck();
  await expect(view.locator('path[data-series="Bag 1"]')).toHaveCount(0);
  report.progress=await page.evaluate(()=>{clearInterval(window.auditProgressTimer);return window.auditProgress;});
  const samples=report.progress.filter(p=>p.value!==null);
  if(samples.length<10)throw Error('Insufficient progress samples');
  for(let i=1;i<samples.length;i++)if(samples[i].value<samples[i-1].value)throw Error('Progress regressed: '+JSON.stringify([samples[i-1],samples[i]]));
  if(!samples.some(p=>p.value>=50))throw Error('Automatic preparation progress missing');
  const first=report.progress.findIndex(p=>p.value!==null);
  const last=report.progress.findLastIndex(p=>p.value!==null);
  if(report.progress.slice(first,last).some(p=>p.value===null))throw Error('Progress disappeared during the run');
 }
 mark('workflow complete');report.longTasks=await page.evaluate(()=>window.auditLongTasks);report.finalText=await view.innerText();
 latest=stateFromDisk();report.final={stage:latest?.current?.result?.response?.staged?.selected_stage,metrics:latest?.current?.result?.response?.diagnostic_metrics};
 await page.screenshot({path:root+'/complete.png'});
} catch(e){console.log('FAILED',e.stack);report.failure=e.stack;process.exitCode=1;if(page){console.log('PAGE',await page.locator('body').innerText());await page.screenshot({path:root+'/failure.png'});}}
finally{await Promise.allSettled(pending);fs.writeFileSync(root+'/workflow-interactions.json',JSON.stringify(report,null,2));if(args)fs.writeFileSync(root+'/request.json',JSON.stringify(args));await browser.close();if(report.errors.length)process.exitCode=1;}
