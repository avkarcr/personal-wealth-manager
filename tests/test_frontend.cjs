// Node-only behavioral smoke tests; no browser or external services required.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync('static/app.js','utf8');
const en = JSON.parse(fs.readFileSync('wealth_manager/adapters/web/locales/en.json','utf8'));

function boot(language, fetch, overrides={}) {
  const handlers = {};
  const alerts = [];
  const document = {
    documentElement: {lang:language},
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: (event, fn) => (handlers[event] ||= []).push(fn),
    ...overrides,
  };
  const window = {UI_MESSAGES:language==='en'?en:{},addEventListener:()=>{}};
  vm.runInNewContext(code, {document,window,fetch,alert:message=>alerts.push(message),console,getComputedStyle:()=>({getPropertyValue:()=>'#123456'})});
  return {handlers,alerts,window};
}

for (const language of ['ru','en']) {
  test(`CBR error and button recovery (${language})`, async () => {
    const {handlers,alerts,window}=boot(language,async()=>{throw Error('offline')});
    assert.equal(typeof window.Charts.history,'function');
    const form={received_date:{value:'2026-01-01'},currency:{value:'USD'}};
    const button={textContent:'CBR',disabled:false,closest:()=>form};
    const event={target:{closest:selector=>selector==='[data-cbr]'?button:null}};
    await handlers.click[0](event);
    assert.equal(alerts[0],language==='en'?'Could not connect to cbr.ru':'Не удалось связаться с cbr.ru');
    assert.equal(button.disabled,false);
    assert.equal(button.textContent,'CBR');
  });
}
test('Missing rate uses translated fallback without shadowing translator',async()=>{
  const {handlers,alerts}=boot('en',async()=>({json:async()=>({})}));
  const form={received_date:{value:'2026-01-01'},currency:{value:'USD'}};
  const button={textContent:'CBR',closest:()=>form};
  await handlers.click[0]({target:{closest:()=>button}});
  assert.deepEqual(alerts,['No exchange rate']);
  assert.equal(button.disabled,false);
});

test('Entity forms route by type and unlock type choices when creating again',()=>{
  const {handlers}=boot('en',async()=>({}));
  const radios=['broker','crypto','realty','freelance','other'].map(value=>({value,disabled:false,checked:false}));
  const id={value:'20'};const title={textContent:''};
  const form={dataset:{accountAction:'/accounts/save',assetAction:'/assets/save',incomeAction:'/income-sources/save'},
    querySelector:s=>s==='[name=kind]:checked'?radios.find(r=>r.checked):s==='[name=id]'?id:null,
    querySelectorAll:s=>s==='[name=kind]'?radios:[]};
  const dlg={dataset:{},querySelector:s=>s==='form'?form:s==='h3'?title:null};
  const event={target:{closest:()=>dlg}};
  radios[2].checked=true;handlers.change[0](event);
  assert.equal(form.action,'/assets/save');assert.equal(title.textContent,'Edit asset');
  assert.equal(radios[0].disabled,true);assert.equal(radios[2].disabled,false);
  id.value='';radios[2].checked=false;radios[3].checked=true;handlers.change[0](event);
  assert.equal(form.action,'/income-sources/save');assert.equal(title.textContent,'New income source');
  assert.ok(radios.every(r=>!r.disabled));
  id.value='10';radios[3].checked=false;radios[0].checked=true;handlers.change[0](event);
  assert.equal(form.action,'/accounts/save');assert.equal(radios[1].disabled,false);assert.equal(radios[3].disabled,true);
});


test('Stable cash code controls quantity label and custom field without translating values',()=>{
  const {handlers}=boot('en',async()=>({}));
  const code={value:'cash'}, custom={value:'Деньги',disabled:false},label={textContent:''};
  const form={querySelector:s=>s==='[name=asset_class_code]'?code:s==='[data-custom-asset]'?custom:null};
  const flags={};
  const dlg={querySelector:s=>s==='form'?form:s==='[data-qty-label]'?label:null,classList:{toggle:(key,value)=>flags[key]=value}};
  const event={target:{closest:()=>dlg}};
  handlers.change[0](event);
  assert.equal(flags.cash,true);assert.equal(label.textContent,'Amount');assert.equal(custom.disabled,true);
  code.value='custom';handlers.change[0](event);
  assert.equal(flags.cash,false);assert.equal(label.textContent,'Quantity');assert.equal(custom.disabled,false);
  assert.equal(custom.value,'Деньги');
});

test('Donut displays custom labels as escaped text, including on hover',()=>{
  const nodes=[];
  function node(){const n={innerHTML:'',children:[],handlers:{},classList:{add:()=>{},remove:()=>{}},
    appendChild(child){this.children.push(child)},setAttribute:()=>{},addEventListener(event,fn){this.handlers[event]=fn}};nodes.push(n);return n;}
  const box=node(),legend=node();
  const {window}=boot('en',async()=>({}),{querySelector:s=>s==='#test-chart'?box:s==='#test-legend'?legend:null,createElement:node,createElementNS:node});
  window.Charts.donut('#test-chart','#test-legend',[[`<img src=x onerror=alert(1)> & "test"`,10]],'RUB');
  legend.children[0].handlers.mouseenter();
  for(const n of nodes)assert.ok(!n.innerHTML.includes('<img'),n.innerHTML);
  assert.ok(legend.children[0].innerHTML.includes('&lt;img'));
});

for (const language of ['ru','en']) {
  test(`Historical chart never renders missing FX as zero (${language})`,()=>{
    const children=[];
    const box={innerHTML:'',appendChild:child=>children.push(child)};
    const {window}=boot(language,async()=>({}),{
      querySelector:s=>s==='#history'?box:null,
      createElement:()=>({className:'',textContent:''}),
    });
    window.Charts.history('#history',[
      {date:'2026-01-01',value:100,invested:90},
      {date:'2026-02-01',value:null,invested:null},
      {date:'2026-03-01',value:120,invested:90},
    ],'USD');
    assert.equal(children.length,1);
    assert.match(children[0].textContent,language==='en'?/Historical rates are missing/:/не хватает курсов/);
  });
}
