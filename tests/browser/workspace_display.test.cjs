const { test } = require('node:test');
const assert = require('node:assert/strict');
const { normalize } = require('../../static/js/workspace_display.js');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../../static/js/workspace_display.js'), 'utf8');

function page(owner, storage = new Map(), blocked = false) {
  let boot;
  function element(attributes = {}) {
    return { attributes, events: {}, hidden: true, value: '', textContent: '', focused: false,
      getAttribute(key) { return this.attributes[key]; },
      setAttribute(key, value) { this.attributes[key] = value; },
      addEventListener(name, handler) { this.events[name] = handler; },
      focus() { this.focused = true; } };
  }
  const width = element({'data-workspace-setting': 'width'});
  const host = element({'data-chat-owner': owner});
  host.querySelectorAll = () => [width];
  const ids = Object.fromEntries(['workspaceControls','workspaceCustomize','workspaceSettingStatus','workspaceClose','workspaceReset'].map(id => [id, element()]));
  const menu = element();
  ids.workspaceCustomize.closest = () => ({querySelector: () => menu});
  vm.runInNewContext(source, {
    document: {querySelector: () => host, getElementById: id => ids[id], addEventListener: (_, fn) => {boot = fn;}},
    localStorage: { getItem: key => storage.get(key) || null,
      setItem(key, value) { if (blocked) throw new Error('storage unavailable'); storage.set(key, value); } }
  });
  boot();
  return { host, ids, width, menu, change(value) {width.value = value; width.events.change();} };
}

test('stored display values cannot inject styles or unrecognized attributes', () => {
  assert.deepEqual(normalize({ width: 'url(https://example.test)', accent: '#fff', text: 'large', unknown: 'secret' }),
    { width: 'focused', presentation: 'flow', density: 'comfortable', text: 'large', plan: 'board', accent: 'neutral' });
});
test('missing or corrupt preferences fall back to the usable default layout', () => {
  for (const value of [null, false, 'broken', [], {}]) {
    assert.equal(normalize(value).width, 'focused');
    assert.equal(normalize(value).presentation, 'flow');
  }
});
test('all supported display choices survive reload normalization', () => {
  const preferences = { width: 'wide', presentation: 'cards', density: 'compact', text: 'large', plan: 'agenda', accent: 'forest' };
  assert.deepEqual(normalize(JSON.parse(JSON.stringify(preferences))), preferences);
});

test('preferences persist for their account and never bleed into another account', () => {
  const storage = new Map();
  const alice = page('alice', storage);
  alice.change('wide');
  assert.equal(page('alice', storage).host.attributes['data-display-width'], 'wide');
  assert.equal(page('bob', storage).host.attributes['data-display-width'], 'focused');
  assert.equal(storage.size, 1);
  const guest = page('', storage);
  guest.change('wide');
  assert.equal(storage.size, 1);
});

test('customization supports Escape, focus return, reset and unavailable storage', () => {
  const p = page('alice', new Map(), true);
  p.ids.workspaceCustomize.events.click();
  assert.equal(p.ids.workspaceControls.hidden, false);
  assert.equal(p.width.focused, true);
  p.change('wide');
  assert.match(p.ids.workspaceSettingStatus.textContent, /unavailable/);
  p.ids.workspaceReset.events.click();
  assert.equal(p.host.attributes['data-display-width'], 'focused');
  p.ids.workspaceControls.events.keydown({key:'Escape', preventDefault(){}});
  assert.equal(p.ids.workspaceControls.hidden, true);
  assert.equal(p.ids.workspaceCustomize.attributes['aria-expanded'], 'false');
  assert.equal(p.menu.focused, true);
});
