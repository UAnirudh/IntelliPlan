const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../../static/js/ip-sidebar-rail.js'), 'utf8');

function harness(blocked = false) {
  const attributes = {}, sideEvents = {}, timers = [], classes = new Set();
  const root = {setAttribute:(k,v)=>attributes[k]=v, removeAttribute:k=>delete attributes[k], hasAttribute:k=>k in attributes};
  const button = {setAttribute(){}, addEventListener(){}};
  const side = {classList:{add:k=>classes.add(k),remove:k=>classes.delete(k),contains:k=>classes.has(k)},
    addEventListener:(k,v)=>sideEvents[k]=v, contains:()=>true, matches:()=>true};
  const tools = {open:false, addEventListener(){}, querySelector:()=>button};
  const document = {documentElement:root, readyState:'complete', activeElement:button,
    getElementById:()=>tools, querySelector:()=>side, dispatchEvent(){}, addEventListener(){}};
  const window = {matchMedia:()=>({matches:false,addEventListener(){}}),addEventListener(){}};
  vm.runInNewContext(source,{window,document,CustomEvent:function(){},
    localStorage:{getItem:()=>null,setItem(){if(blocked)throw new Error('blocked');}},
    setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){}});
  return {api:window.IPSidebar,sideEvents,classes,attributes,timers,tools};
}

test('collapse closes a focused hover peek and does not immediately reopen', () => {
  const h=harness();
  h.api.setMode('collapsed');
  h.sideEvents.mouseenter();h.timers.shift()();
  assert.equal(h.classes.has('is-peek'),true);
  h.api.toggle();h.api.toggle();
  assert.equal(h.api.getMode(),'collapsed');
  assert.equal(h.classes.has('is-peek'),false);
  h.sideEvents.focusin({target:{matches:()=>true}});
  assert.equal(h.classes.has('is-peek'),false);
  h.sideEvents.mouseleave();h.sideEvents.mouseenter();h.timers.shift()();
  assert.equal(h.classes.has('is-peek'),true);
});

test('sidebar still toggles when browser storage is blocked', () => {
  const h=harness(true);
  assert.equal(h.api.getMode(),'auto');
  assert.equal('data-side-rail' in h.attributes,true);
  h.api.toggle();
  assert.equal(h.api.getMode(),'expanded');
  assert.equal('data-side-rail' in h.attributes,false);
  h.api.toggle();
  assert.equal(h.api.getMode(),'collapsed');
  assert.equal('data-side-rail' in h.attributes,true);
});

test('pointer exit retracts the rail and closes extra tools after clicking', () => {
  const h = harness();
  h.sideEvents.mouseenter();
  h.timers.shift()();
  h.tools.open = true;
  h.sideEvents.pointerdown();
  h.sideEvents.mouseleave();
  assert.equal(h.classes.has('is-peek'), false);
  assert.equal(h.tools.open, false);
});

test('keyboard focus keeps sidebar labels visible after pointer exit', () => {
  const h = harness();
  h.sideEvents.focusin({target:{matches:()=>true}});
  h.sideEvents.mouseleave();
  assert.equal(h.classes.has('is-peek'), true);
});
