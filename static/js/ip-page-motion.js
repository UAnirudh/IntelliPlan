(() => {
  'use strict';

  const root = document.documentElement;
  const curtain = document.querySelector('.ip-page-curtain');
  if (!curtain) return;
  const key = 'intelliplan.page-motion';
  const motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)');
  let target = null;
  let timer;
  let recovery;

  function clear() {
    clearTimeout(timer);
    clearTimeout(recovery);
    target = null;
    delete root.dataset.pageMotion;
  }

  function motionAllowed() {
    return !motionPreference.matches && !root.classList.contains('a11y-reduce-motion')
      && root.dataset.a11yMotion !== 'reduce' && root.dataset.uiMotion !== 'reduce';
  }

  function navigate() {
    if (!target) return;
    clearTimeout(timer);
    const url = target;
    target = null;
    try {
      sessionStorage.setItem(key, JSON.stringify({
        url: url.pathname + url.search, from: location.href.split('#')[0], at: Date.now(),
      }));
    } catch (_) {}
    location.assign(url.href);
    recovery = setTimeout(() => {
      try { sessionStorage.removeItem(key); } catch (_) {}
      clear();
    }, 12000);
  }

  curtain.addEventListener('animationend', event => {
    if (event.animationName === 'ip-page-cover') navigate();
    if (event.animationName === 'ip-page-reveal') clear();
  });
  if (root.dataset.pageMotion === 'arrive') recovery = setTimeout(clear, 600);

  window.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey
        || event.shiftKey || event.altKey || !motionAllowed()) return;
    const link = event.target.closest?.('a[href]');
    if (!(link instanceof HTMLAnchorElement) || (link.target && link.target !== '_self') || link.hasAttribute('download')
        || link.hasAttribute('ping') || link.hasAttribute('referrerpolicy') || link.relList.contains('noreferrer')
        || link.closest('[data-no-page-motion], [data-no-progress], [aria-disabled="true"]')) return;
    const url = new URL(link.href, location.href);
    if (url.origin !== location.origin || !/^https?:$/.test(url.protocol)) return;
    if (url.pathname === location.pathname && url.search === location.search) return;
    if (/^\/(oauth2callback|oauth|auth)(\/|$)/.test(url.pathname)) return;

    event.preventDefault();
    if (root.dataset.pageMotion === 'leave') return;
    clear();
    target = url;
    root.dataset.pageMotion = 'leave';
    timer = setTimeout(navigate, 240);
  });

  window.addEventListener('pageshow', event => { if (event.persisted) clear(); });
  window.addEventListener('pagehide', clear);
})();
