'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../static/js/school_admin.js'), 'utf8');
const start = source.indexOf('function renderMembers(data) {');
const end = source.indexOf('\n  function renderAudit', start);
assert.ok(start >= 0 && end > start, 'member renderer should remain a standalone function');
const renderer = source.slice(start, end);

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.listeners = {};
    this.dataset = {};
    this.attributes = {};
    this.disabled = false;
    this.value = '';
    this.textContent = '';
  }
  append(...children) {
    for (const child of children) {
      child.parent = this;
      this.children.push(child);
    }
  }
  insertBefore(child, before) {
    child.parent = this;
    const index = this.children.indexOf(before);
    this.children.splice(index < 0 ? this.children.length : index, 0, child);
  }
  replaceChildren(...children) {
    this.children = [];
    this.append(...children);
  }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  setAttribute(name, value) { this.attributes[name] = value; }
  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this);
  }
}

function fixture(nextPage) {
  const container = new Element('div');
  const requests = [];
  const scope = {id: 19, name: 'North School'};
  const context = {
    document: {createElement: tag => new Element(tag)},
    selected: scope,
    saving: false,
    status() {},
    render() {},
    $: id => new Element(id),
    api: async url => { requests.push(url); return nextPage(); },
  };
  vm.createContext(context);
  vm.runInContext(renderer, context);
  return {context, container, requests, scope};
}

test('school roster appends bounded pages and preserves per-member access controls', async () => {
  const f = fixture(async () => ({
    members: [{user_id: 2, role: 'teacher', email: 'teacher@example.test', name: 'Taylor'}],
    total: 2, role_counts: {teacher: 1, student: 1}, offset: 1, limit: 1, has_more: false,
  }));
  f.context.$ = id => id === 'admin-members' ? f.container : new Element(id);
  vm.runInContext(`renderMembers({members:[{user_id:1,role:'student',email:'student@example.test',name:'Sam'}], total:2, role_counts:{student:1,teacher:1}, offset:0, limit:1, has_more:true})`, f.context);

  assert.match(f.container.children.at(-1).textContent, /1 of 2/);
  const button = f.container.children.at(-1);
  await button.listeners.click();

  assert.deepEqual(f.requests, ['/api/school-admin/organizations/19/members?offset=1&limit=1']);
  assert.equal(f.container.children.length, 2);
  assert.match(f.container.children[0].children[0].children[1].textContent, /student@example.test/);
  assert.match(f.container.children[1].children[0].children[1].textContent, /teacher@example.test/);
  assert.equal(f.container.children[1].children[1].children[0].tagName, 'select');
  assert.equal(f.container.children[1].children[1].children[1].textContent, 'Remove');
});

test('a roster page fetched for an old school is discarded after switching organizations', async () => {
  let resolvePage;
  const f = fixture(() => new Promise(resolve => { resolvePage = resolve; }));
  f.context.$ = id => id === 'admin-members' ? f.container : new Element(id);
  vm.runInContext(`renderMembers({members:[{user_id:1,role:'student',email:'student@example.test',name:'Sam'}], total:2, role_counts:{student:2}, offset:0, limit:1, has_more:true})`, f.context);
  const load = f.container.children.at(-1).listeners.click();
  f.context.selected = {id: 20, name: 'South School'};
  resolvePage({members:[{user_id:2,role:'student',email:'other@example.test',name:'Pat'}], total:2, role_counts:{student:2}, offset:1, limit:1, has_more:false});
  await load;
  assert.equal(f.container.children.length, 2);
  assert.equal(f.container.children[1].dataset.memberLoadMore, '');
});

test('filtered roster pagination uses the match count and preserves the encoded search', async () => {
  const f = fixture(async () => ({
    members: [{user_id: 9, role: 'teacher', email: 'jordan@example.test', name: 'Jordan'}],
    total: 240, filtered_total: 2, search: 'Jordan + teacher@example.test',
    role_counts: {student: 200, teacher: 40}, offset: 1, limit: 1, has_more: false,
  }));
  f.context.$ = id => id === 'admin-members' ? f.container : new Element(id);
  vm.runInContext(`renderMembers({members:[{user_id:8,role:'teacher',email:'teacher@example.test',name:'Jordan'}], total:240, filtered_total:2, search:'Jordan + teacher@example.test', role_counts:{student:200,teacher:40}, offset:0, limit:1, has_more:true})`, f.context);

  const button = f.container.children.at(-1);
  assert.match(button.textContent, /1 of 2/);
  await button.listeners.click();
  assert.deepEqual(f.requests, ['/api/school-admin/organizations/19/members?offset=1&limit=1&q=Jordan%20%2B%20teacher%40example.test']);
});

test('school admin roster search is labelled and limited to the selected workspace', () => {
  const template = fs.readFileSync(path.join(__dirname, '../../Main_Project/templates/school_admin.html'), 'utf8');
  assert.match(template, /id="admin-member-filter" type="search" maxlength="128"/);
  assert.match(template, /id="admin-members" role="region" aria-label="Workspace members"/);
  assert.match(source, /value\.trim\(\)\.slice\(0, 128\)/);
  assert.match(source, /URLSearchParams\(\{limit:'50'\}\)/);
});

test('teacher and student access guidance names its workspace and admin boundary', () => {
  const template = fs.readFileSync(path.join(__dirname, '../../Main_Project/templates/school_admin.html'), 'utf8');
  assert.match(template, /id="admin-role" aria-describedby="admin-role-help"/);
  const start = source.indexOf('function updateMemberRoleHelp() {');
  const end = source.indexOf('\n  }', start) + 4;
  assert.ok(start >= 0 && end > start, 'role guidance should remain a standalone function');
  const role = new Element('select');
  const help = new Element('p');
  const context = {
    selected: {name: 'North School'},
    $: id => id === 'admin-role' ? role : help,
  };
  vm.createContext(context);
  vm.runInContext(source.slice(start, end), context);

  role.value = 'teacher';
  vm.runInContext('updateMemberRoleHelp()', context);
  assert.match(help.textContent, /North School only/);
  assert.match(help.textContent, /Assign the teacher to a classroom separately/);
  assert.match(help.textContent, /does not grant school administrator access/);

  role.value = 'student';
  vm.runInContext('updateMemberRoleHelp()', context);
  assert.match(help.textContent, /does not grant teacher or school administrator controls/);
});
