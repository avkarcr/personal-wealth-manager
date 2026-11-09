// Node-only behavioral smoke tests; no browser or external services required.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync('static/app.js','utf8');
const en = JSON.parse(fs.readFileSync('wealth_manager/adapters/web/locales/en.json','utf8'));

function boot(language, fetch) {
  const handlers = {};
  const alerts = [];
  const document = {
    documentElement: {lang:language},
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: (event, fn) => (handlers[event] ||= []).push(fn),
  };
  const window = {UI_MESSAGES:language==='en'?en:{},addEventListener:()=>{}};
  vm.runInNewContext(code, {document,window,fetch,alert:message=>alerts.push(message),console});
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
