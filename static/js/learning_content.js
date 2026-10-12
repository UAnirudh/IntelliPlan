'use strict';
(() => {
  const MAX_SOURCE = 30000;
  const MAX_MATH = 2000;
  const MAX_EXPRESSIONS = 32;
  let parser;

  function createParser() {
    const md = window.markdownit({
      html: false, linkify: false, typographer: false,
      highlight(code, language) {
        if (!window.hljs || !window.hljs.getLanguage(language)) return '';
        try { return window.hljs.highlight(code, {language, ignoreIllegals: true}).value; }
        catch { return ''; } // The parser escapes unknown/unsupported code as text.
      },
    });
    // Generated URLs remain inert. Reviewed catalog links are rendered separately.
    md.disable(['link', 'image', 'autolink']);
    md.renderer.rules.heading_open = () => '<h6 class="learning-content-heading">';
    md.renderer.rules.heading_close = () => '</h6>\n';
    const delimiters = [
      {open: '$$', close: '$$', display: true},
      {open: '\\[', close: '\\]', display: true},
      {open: '\\(', close: '\\)', display: false},
      {open: '$', close: '$', display: false},
    ];
    md.inline.ruler.before('escape', 'learning_math', (state, silent) => {
      if ((state.env.mathCount || 0) >= MAX_EXPRESSIONS) return false;
      const delimiter = delimiters.find(d => state.src.startsWith(d.open, state.pos));
      if (!delimiter) return false;
      const begin = state.pos + delimiter.open.length;
      if (delimiter.open === '$' && /\s/.test(state.src[begin] || ' ')) return false;
      let end = state.src.indexOf(delimiter.close, begin);
      while (end !== -1 && delimiter.close.startsWith('$') && state.src[end - 1] === '\\') {
        end = state.src.indexOf(delimiter.close, end + delimiter.close.length);
      }
      if (end === -1 || end === begin || end - begin > MAX_MATH) return false;
      if (delimiter.open === '$' && (/\s/.test(state.src[end - 1]) || /\d/.test(state.src[end + 1] || ''))) return false;
      if (!silent) {
        const token = state.push('learning_math', '', 0);
        token.content = state.src.slice(begin, end);
        token.meta = {display: delimiter.display};
        state.env.mathCount = (state.env.mathCount || 0) + 1;
      }
      state.pos = end + delimiter.close.length;
      return true;
    });
    md.block.ruler.before('fence', 'learning_math_block', (state, start, last, silent) => {
      if ((state.env.mathCount || 0) >= MAX_EXPRESSIONS || state.sCount[start] > 3) return false;
      const first = state.src.slice(state.bMarks[start] + state.tShift[start], state.eMarks[start]).trim();
      const delimiter = delimiters.find(d => d.display && first === d.open);
      if (!delimiter) return false;
      let end = start + 1;
      while (end < last && state.src.slice(state.bMarks[end] + state.tShift[end], state.eMarks[end]).trim() !== delimiter.close) end++;
      if (end === last) return false;
      const content = state.src.slice(state.bMarks[start + 1], state.bMarks[end]).trim();
      if (!content || content.length > MAX_MATH) return false;
      if (silent) return true;
      const token = state.push('learning_math', '', 0);
      token.block = true; token.content = content; token.meta = {display: true};
      state.env.mathCount = (state.env.mathCount || 0) + 1;
      state.line = end + 1;
      return true;
    });
    md.renderer.rules.learning_math = (tokens, index) => {
      const token = tokens[index];
      try {
        return window.katex.renderToString(token.content, {
          displayMode: token.meta.display, output: 'htmlAndMathml',
          trust: false, strict: 'error', throwOnError: true,
          maxSize: 10, maxExpand: 1000, macros: {}, globalGroup: false,
        });
      } catch {
        return `<span class="learning-math-source">Math source: <code>${md.utils.escapeHtml(token.content)}</code></span>`;
      }
    };
    return md;
  }

  function render(container, source) {
    const text = typeof source === 'string' ? source : String(source ?? '');
    container.classList.add('learning-content');
    if (text.length > MAX_SOURCE || !window.markdownit || !window.katex || !window.DOMPurify?.isSupported) {
      container.textContent = text;
      container.dataset.rendering = 'plain-text';
      return false;
    }
    try {
      parser ||= createParser();
      const markup = parser.render(text, {});
      const content = window.DOMPurify.sanitize(markup, {
        RETURN_DOM_FRAGMENT: true,
        USE_PROFILES: {html: true, svg: true, mathMl: true},
        FORBID_TAGS: ['a', 'img', 'iframe', 'video', 'audio', 'script', 'style', 'form', 'input', 'button', 'object', 'embed', 'foreignObject'],
        FORBID_ATTR: ['href', 'src', 'srcset', 'xlink:href', 'id', 'name'],
        ALLOW_DATA_ATTR: false,
      });
      container.replaceChildren(content);
      // These regions can overflow after a viewport change. Keep their scroll
      // controls reachable by keyboard even when rendered before attachment.
      for (const region of container.querySelectorAll('pre, .katex-display')) {
        region.tabIndex = 0;
        region.setAttribute('role', 'region');
        region.setAttribute('aria-label', region.tagName === 'PRE' ? 'Code example' : 'Mathematical expression');
      }
      container.dataset.rendering = 'formatted';
      return true;
    } catch {
      container.textContent = text;
      container.dataset.rendering = 'plain-text';
      return false;
    }
  }
  window.LearningContent = Object.freeze({render});
})();
