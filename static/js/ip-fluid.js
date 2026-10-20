/* Local layout preferences only. Access decisions always come from the server. */
(function () {
  'use strict';
  function read(key) {
    try { return localStorage.getItem(key) === 'collapsed'; } catch (_) { return false; }
  }
  function write(key, collapsed) {
    try { localStorage.setItem(key, collapsed ? 'collapsed' : 'expanded'); } catch (_) { /* Session remains usable. */ }
  }
  function animatePanel(target) {
    if (!target || !target.animate || matchMedia('(prefers-reduced-motion: reduce)').matches ||
        document.documentElement.dataset.a11yMotion === 'reduce' || document.documentElement.dataset.uiMotion === 'reduce') return;
    target.animate([{opacity: .6, transform: 'translateX(6px)'}, {opacity: 1, transform: 'translateX(0)'}],
      {duration: 180, easing: 'cubic-bezier(.22,1,.36,1)'});
  }
  function panel(options) {
    var parent = document.querySelector(options.parent);
    if (!parent) return;
    var target = parent.querySelector(options.target);
    if (!target) return;
    var key = 'ip_panel_' + options.id;
    target.id = target.id || options.id;
    target.dataset.fluidPanel = '';
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'fluid-panel-toggle no-glass ' + (options.floating ? 'fluid-history-toggle' : '');
    button.setAttribute('aria-controls', target.id);
    var icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    icon.setAttribute('viewBox', '0 0 24 24');
    icon.setAttribute('aria-hidden', 'true');
    var path = document.createElementNS(icon.namespaceURI, 'path');
    path.setAttribute('d', 'M4 4h16v16H4z M9 4v16 M13 9l3 3-3 3');
    icon.appendChild(path);
    var label = document.createElement('span');
    button.append(icon, label);
    var collapsed = read(key);
    var desktop = window.matchMedia('(min-width: 769px)');
    function apply() {
      // The tutor's existing mobile drawer owns its visibility on phones.
      var hide = collapsed && (!options.floating || desktop.matches);
      target.hidden = hide;
      if (hide) target.setAttribute('inert', ''); else target.removeAttribute('inert');
      parent.dataset.panelCollapsed = hide ? 'true' : 'false';
      button.setAttribute('aria-expanded', hide ? 'false' : 'true');
      label.textContent = (hide ? 'Show ' : 'Hide ') + options.label;
    }
    button.addEventListener('click', function () {
      collapsed = !collapsed; write(key, collapsed); apply();
      animatePanel(parent.querySelector('.tutor-main') || parent);
    });
    if (options.floating) {
      var header = parent.querySelector('.tutor-workspace-actions');
      if (header) header.prepend(button);
      else parent.querySelector('.tutor-main').prepend(button);
    }
    else {
      var toolbar = document.createElement('div');
      toolbar.className = 'fluid-panel-toolbar';
      toolbar.appendChild(button);
      parent.before(toolbar);
    }
    desktop.addEventListener('change', apply);
    apply();
  }
  function extraTools() {
    var nav = document.getElementById('sideNavList');
    if (!nav) return;
    var extras = ['memories', 'flashcards', 'courses', 'streak', 'pet', 'balance', 'features', 'my_stats'];
    var links = Array.from(nav.querySelectorAll('[data-nav-item]')).filter(function (link) {
      return extras.indexOf(link.dataset.navItem) >= 0;
    });
    if (!links.length) return;
    links.forEach(function (link) { link.classList.add('side-extra-link'); });
    var button = document.createElement('button');
    button.className = 'side-link side-extra-toggle no-glass';
    button.type = 'button';
    var icon = document.createElement('span');
    icon.className = 'side-icon';
    icon.textContent = '⋯';
    icon.setAttribute('aria-hidden', 'true');
    var label = document.createTextNode('');
    button.append(icon, label);
    button.setAttribute('aria-controls', links.map(function (link, index) {
      link.id = link.id || 'side-extra-' + index; return link.id;
    }).join(' '));
    var expanded = !read('ip_panel_extra-tools');
    // First visit starts with a compact, useful set. A visible active tool
    // always stays in the list, including when the extras are minimized.
    try { if (localStorage.getItem('ip_panel_extra-tools') === null) expanded = false; } catch (_) { expanded = false; }
    function apply() {
      nav.dataset.extraCollapsed = expanded ? 'false' : 'true';
      label.nodeValue = expanded ? 'Fewer tools' : 'More tools';
      button.setAttribute('aria-expanded', String(expanded));
      button.setAttribute('aria-label', expanded ? 'Show fewer tools' : 'Show more tools');
    }
    button.addEventListener('click', function () { expanded = !expanded; write('ip_panel_extra-tools', !expanded); apply(); });
    nav.appendChild(button);
    // Custom ordering moves the actual links. Keep this disclosure after
    // them without changing their saved relative order or visibility.
    new MutationObserver(function () { if (nav.lastElementChild !== button) nav.appendChild(button); })
      .observe(nav, {childList: true});
    apply();
  }
  function policyLinks() {
    var source = document.getElementById('ipWorkspacePolicy');
    if (!source) return;
    var paths;
    try { paths = JSON.parse(source.textContent); } catch (_) { return; }
    if (!Array.isArray(paths) || !paths.length) return;
    document.querySelectorAll('a[href]').forEach(function (link) {
      var url = new URL(link.href, location.href);
      if (url.origin !== location.origin) return;
      if (!paths.some(function (path) { return url.pathname === path || url.pathname.indexOf(path + '/') === 0; })) return;
      if (link.closest('.app-side, #mainNav, .ip-tabbar, #ipMenuPages')) {
        link.hidden = true;
        link.dataset.policyDisabled = '';
      } else {
        link.dataset.policyDisabled = '';
        link.setAttribute('aria-disabled', 'true');
        link.setAttribute('title', 'Unavailable under your district or school policy');
        link.removeAttribute('href');
      }
    });
  }
  function appearance() {
    var dialog = document.getElementById('workspaceAppearance');
    if (!dialog) return;
    var allowed = {density: ['comfortable','compact'], width: ['focused','wide'], text: ['standard','large','larger'], surface: ['glass','solid'],
      accent: ['default','emerald','violet'], motion: ['full','reduce'], mascot: ['shown','hidden']};
    var previousFocus = null;
    function apply(values) {
      Object.keys(allowed).forEach(function (key) {
        var value = allowed[key].indexOf(values[key]) >= 0 ? values[key] : allowed[key][0];
        document.documentElement.setAttribute('data-ui-' + key, value);
        dialog.querySelector('[data-appearance="' + key + '"]').value = value;
      });
      document.dispatchEvent(new CustomEvent('ip:appearance'));
    }
    function values() {
      var result = {};
      dialog.querySelectorAll('[data-appearance]').forEach(function (field) { result[field.dataset.appearance] = field.value; });
      return result;
    }
    var saved = {};
    try { saved = JSON.parse(localStorage.getItem('ip_workspace_style_v1') || '{}'); } catch (_) {}
    apply(saved || {});
    document.querySelectorAll('[data-appearance-open]').forEach(function (button) {
      button.addEventListener('click', function () {
        var sheet = button.closest('#ipMenuSheet');
        previousFocus = sheet ? document.getElementById('ipMenuOpen') : button;
        if (sheet) document.getElementById('ipMenuClose').click();
        dialog.showModal();
      });
    });
    dialog.querySelector('[data-appearance-close]').addEventListener('click', function () { dialog.close(); });
    dialog.addEventListener('close', function () { if (previousFocus) previousFocus.focus(); });
    dialog.addEventListener('change', function () {
      var prefs = values(); apply(prefs);
      try { localStorage.setItem('ip_workspace_style_v1', JSON.stringify(prefs)); } catch (_) {}
    });
    document.getElementById('appearanceReset').addEventListener('click', function () {
      apply({}); try { localStorage.removeItem('ip_workspace_style_v1'); } catch (_) {}
    });
  }
  function init() {
    appearance();
    extraTools();
    panel({parent: '.tutor-shell', target: '.tutor-side', id: 'tutor-history', label: 'chat history', floating: true});
    panel({parent: '.ipd-columns', target: ':scope > :last-child', id: 'dashboard-overview', label: 'overview'});
    panel({parent: '.learning-workspace', target: '.learning-guide', id: 'study-guide', label: 'study guide'});
    policyLinks();
    // The phone menu is derived from the sidebar when opened.
    document.getElementById('ipMenuOpen')?.addEventListener('click', policyLinks);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
