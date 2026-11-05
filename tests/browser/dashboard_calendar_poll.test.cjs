const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

const templatePath = path.join(__dirname, '../../Main_Project/templates/dashboard.html');
const template = fs.readFileSync(templatePath, 'utf8');

test('minute dashboard refresh does not fetch calendar event contents for unused status', () => {
  const start = template.indexOf('async function loadDashboard()');
  const end = template.indexOf('setInterval(loadDashboard, 60000)', start);
  assert.ok(start >= 0 && end > start, 'dashboard loader and refresh interval should exist');

  const refreshSource = template.slice(start, end);
  assert.doesNotMatch(refreshSource, /\/calendar\/events/);
  assert.doesNotMatch(refreshSource, /gcalConnected/);
});
