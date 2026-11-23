const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const code=fs.readFileSync('static/macro_charts.js','utf8');
const en=JSON.parse(fs.readFileSync('wealth_manager/adapters/web/locales/en.json','utf8'));
function boot(lang='en'){
  const nodes=[];
  function node(){const n={children:[],attrs:{},handlers:{},clientWidth:700,textContent:'',
    setAttribute(k,v){this.attrs[k]=String(v)},getAttribute(k){return this.attrs[k]||'Chart'},
    appendChild(c){this.children.push(c)},replaceChildren(){this.children=[]},
    addEventListener(k,f){this.handlers[k]=f},getBoundingClientRect(){return {left:0}},focus(){this.focused=true}};nodes.push(n);return n;}
  const document={documentElement:{lang},getElementById:()=>null,createElementNS:node};
  const window={UI_MESSAGES:lang==='en'?en:{}};
  vm.runInNewContext(code,{window,document,Intl,Date,console});
  return {api:window.MacroCharts,node,nodes};
}
test('Macro numeric conversion preserves zero and rejects missing or nonfinite values',()=>{
  const {api}=boot();for(const x of [null,undefined,'','  ',true,'NaN','Infinity'])assert.equal(api.number(x),null);
  assert.equal(api.number('0'),0);assert.equal(api.number('-0.25'),-.25);
  assert.equal(api.bounds([null,'']),null);assert.ok(api.bounds([0])[0]<0);
  const range=api.bounds(['10','20'],true);assert.ok(range[0]<0 && range[1]>20);
  assert.ok(api.bounds(Array(200000).fill('10'))[1]>10);
});
test('Macro line segments never bridge missing observations or explicit gaps',()=>{
  const {api}=boot(),points=[{value:'1'},{value:null},{value:'0'},{value:'2',break_before:true},{value:'3'}];
  const parts=api.segments(points);assert.equal(parts.length,3);assert.equal(parts[1][0].value,'0');assert.equal(parts[2].length,2);
  assert.equal(api.segments([{value:null}]).length,0);
});
test('Keyboard and pointer selection clamp correctly at history boundaries',()=>{
  const {api}=boot();assert.equal(api.nearest([],1),-1);assert.equal(api.nearest([1,5,10],7),1);assert.equal(api.nearest([1,5,10],100),2);
  assert.equal(api.step(0,'ArrowLeft',3),0);assert.equal(api.step(2,'ArrowRight',3),2);assert.equal(api.step(2,'Home',3),0);
  assert.equal(api.step(0,'End',3),2);assert.equal(api.step(1,'Enter',3),1);assert.equal(api.step(0,'Home',0),-1);
});
for(const lang of ['ru','en'])test(`Rendered macro chart supports keyboard, pointer, exact readout and theme tokens (${lang})`,()=>{
  const {api,node,nodes}=boot(lang),box=node(),output=node();
  const data={kind:'gdp',axis:'date',start:'2026-01-01',end:'2026-03-31',unit_label:'%',points:[{date:'2026-01-31',value:'0',revisions:[1]},{date:'2026-02-28',value:null,revisions:[]},{date:'2026-03-31',value:'2.12345678901234567890',revisions:[3],break_before:true}]};
  api.mount(box,output,data);assert.match(output.textContent,/2.12345678901234567890/);
  let prevented=false;box.handlers.keydown({key:'Home',preventDefault(){prevented=true}});assert.ok(prevented);assert.match(output.textContent,/2026-01-31/);
  box.handlers.keydown({key:'ArrowRight',preventDefault(){}});assert.match(output.textContent,lang==='en'?/No data/:/Нет данных/);
  box.handlers.pointerdown({clientX:690});assert.ok(box.focused);assert.match(output.textContent,/2026-03-31/);
  assert.ok(nodes.some(n=>n.attrs.stroke==='var(--accent)'));
  assert.ok(nodes.filter(n=>n.attrs.d).every(n=>!n.attrs.d.includes('NaN')));
  assert.ok(nodes.every(n=>!('innerHTML' in n)));
});
test('Phase chart is categorical, unknown phases remain labelled, and untrusted labels are plain text',()=>{
  const {api,node,nodes}=boot(),box=node(),output=node();
  api.mount(box,output,{axis:'phase',kind:'phases',start:'2026-03-31',end:'2026-06-30',unit_label:'',phase_labels:{INSUFFICIENT_DATA:'<img src=x onerror=alert(1)>'},points:[{date:'2026-03-31',value:6,phase:'INSUFFICIENT_DATA',confidence:'0',coverage:'0',revisions:[]}]});
  assert.match(output.textContent,/<img src=x onerror=alert\(1\)>/);assert.ok(nodes.every(n=>!('innerHTML' in n)));
  assert.ok(nodes.some(n=>n.attrs.fill==='var(--muted)'));
});
test('Empty charts and missing tenors do not create fabricated zero observations',()=>{
  const {api,node,nodes}=boot(),box=node(),output=node();api.mount(box,output,{axis:'date',points:[]});
  assert.ok(nodes.some(n=>n.textContent==='No data'));
  const box2=node();api.mount(box2,output,{kind:'curve',axis:'tenor',start:'2026-01-01',end:'2026-01-01',unit_label:'%',points:[{x:1,date:'2026-01-01',value:10,revisions:[1]},{x:2,date:'2026-01-01',value:null,revisions:[]},{x:10,date:'2026-01-01',value:9,revisions:[2]}]});
  box2.handlers.keydown({key:'Home',preventDefault(){}});assert.match(output.textContent,/1 years/);
});
