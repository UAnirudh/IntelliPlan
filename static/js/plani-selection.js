(() => {
  const panel = document.getElementById('planiSelection');
  const content = document.getElementById('main-content');
  if (!panel || !content) return;

  const excluded = 'input, textarea, select, [contenteditable], [role="textbox"], [data-plani-private]';
  const quoteLimit = 700;
  const prompts = {
    explain: 'Help me understand the selected passage. Explain it in plain language and give me an example.',
    quiz: 'Quiz me on the selected passage. Ask one question at a time and wait for my answer.',
  };
  let quote = '';
  let frame;
  let dismissedSelection = '';
  let lastDraft = '';

  function hide() {
    quote = '';
    panel.hidden = true;
  }

  function update() {
    frame = undefined;
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || selection.rangeCount !== 1) {
      dismissedSelection = '';
      hide();
      return;
    }
    const range = selection.getRangeAt(0);
    const anchor = selection.anchorNode.parentElement;
    const focus = selection.focusNode.parentElement;
    const source = range.commonAncestorContainer;
    if (!content.contains(source) || anchor?.closest(excluded) || focus?.closest(excluded)
        || (source.nodeType === Node.ELEMENT_NODE && Array.from(source.querySelectorAll(excluded)).some(node => range.intersectsNode(node)))) {
      hide();
      return;
    }
    const text = selection.toString().trim();
    if (!text || text === dismissedSelection) {
      hide();
      return;
    }
    const bounds = range.getBoundingClientRect();
    if (!bounds.width || bounds.bottom < 0 || bounds.top > innerHeight) {
      hide();
      return;
    }
    quote = text.length > quoteLimit ? `${text.slice(0, quoteLimit).trimEnd()}…` : text;
    panel.hidden = false;
    const left = (bounds.left + bounds.right - panel.offsetWidth) / 2;
    const x = Math.max(8, Math.min(innerWidth - panel.offsetWidth - 8, left));
    const gap = matchMedia('(pointer: coarse)').matches ? 28 : 9;
    const below = bounds.bottom + gap;
    const y = below + panel.offsetHeight <= innerHeight - 8 ? below : bounds.top - panel.offsetHeight - 9;
    panel.style.left = `${x}px`;
    panel.style.top = `${Math.max(8, y)}px`;
  }

  function queueUpdate() {
    if (frame === undefined) frame = requestAnimationFrame(update);
  }

  function openDraft(action) {
    if (!quote || !prompts[action]) return;
    const input = document.getElementById('planiInput');
    const originalDraft = input.value === lastDraft ? '' : input.value.trim();
    const notes = originalDraft ? `\n\nMy notes:\n${originalDraft}` : '';
    lastDraft = `${prompts[action]}\n\nSelected passage:\n“${quote}”${notes}`;
    input.value = lastDraft;
    dismissedSelection = window.getSelection()?.toString().trim() || '';
    hide();
    document.getElementById('planiBot').classList.add('is-selection-chat');
    openPlani();
    autoResizePlani(input);
  }

  // Keep the range in place when clicking the action with a mouse.
  panel.addEventListener('mousedown', event => event.preventDefault());
  panel.addEventListener('click', event => {
    const button = event.target.closest('[data-plani-action]');
    if (button) openDraft(button.dataset.planiAction);
  });
  document.addEventListener('selectionchange', queueUpdate);
  document.addEventListener('scroll', queueUpdate, {capture: true, passive: true});
  window.addEventListener('resize', queueUpdate);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !panel.hidden) {
      dismissedSelection = window.getSelection()?.toString().trim() || '';
      hide();
    }
    if (event.altKey && event.shiftKey && event.code === 'KeyP' && quote) {
      event.preventDefault();
      openDraft('explain');
    }
  });
})();
