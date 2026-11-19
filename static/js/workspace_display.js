/* Display preferences contain no conversation or school content. */
(function () {
  'use strict';
  var defaults = { width: 'focused', presentation: 'flow', density: 'comfortable', text: 'standard', plan: 'board', accent: 'neutral' };
  var allowed = { width: ['focused', 'wide'], presentation: ['flow', 'cards'], density: ['comfortable', 'compact'], text: ['standard', 'large'], plan: ['board', 'agenda'], accent: ['neutral', 'iris', 'ocean', 'forest', 'ember'] };
  function normalize(raw) {
    var clean = {};
    Object.keys(defaults).forEach(function (key) { clean[key] = raw && allowed[key].indexOf(raw[key]) !== -1 ? raw[key] : defaults[key]; });
    return clean;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { normalize: normalize };
  if (typeof document === 'undefined') return;
  document.addEventListener('DOMContentLoaded', function () {
    var host = document.querySelector('.command-chat');
    if (!host) return;
    var owner = host.getAttribute('data-chat-owner');
    var storageKey = owner ? 'ip_workspace_display_v1:' + owner : null;
    var controls = document.getElementById('workspaceControls');
    var toggle = document.getElementById('workspaceCustomize');
    var status = document.getElementById('workspaceSettingStatus');
    var values = normalize(null);
    try { if (storageKey) values = normalize(JSON.parse(localStorage.getItem(storageKey))); } catch (_) { /* blocked or invalid storage */ }
    var inputs = host.querySelectorAll('[data-workspace-setting]');
    function apply() {
      Object.keys(values).forEach(function (key) { host.setAttribute('data-display-' + key, values[key]); });
      inputs.forEach(function (input) { input.value = values[input.getAttribute('data-workspace-setting')]; });
    }
    function persist() {
      apply();
      try {
        if (storageKey) { localStorage.setItem(storageKey, JSON.stringify(values)); status.textContent = 'Saved on this browser'; }
        else status.textContent = 'Applied for this visit';
      } catch (_) { status.textContent = 'Applied for this visit; browser storage is unavailable'; }
    }
    function show(open) {
      controls.hidden = !open;
      toggle.setAttribute('aria-expanded', String(open));
      if (open) inputs[0].focus();
      else toggle.closest('details').querySelector('summary').focus();
    }
    toggle.addEventListener('click', function () { show(controls.hidden); });
    document.getElementById('workspaceClose').addEventListener('click', function () { show(false); });
    controls.addEventListener('keydown', function (event) { if (event.key === 'Escape') { event.preventDefault(); show(false); } });
    inputs.forEach(function (input) { input.addEventListener('change', function () {
      values[input.getAttribute('data-workspace-setting')] = input.value;
      values = normalize(values);
      persist();
    }); });
    document.getElementById('workspaceReset').addEventListener('click', function () { values = normalize(null); persist(); });
    apply();
  });
})();
