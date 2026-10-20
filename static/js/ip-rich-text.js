/* Shared, sanitized AI prose. Executable model output is never supported. */
(function () {
  'use strict';
  var parser, mathSources = [], mathCount = 0;
  function json(value) { try { return JSON.stringify(value, null, 2); } catch (_) { return 'Response could not be displayed.'; } }
  function normalize(value) {
    for (var depth = 0; depth < 8; depth++) {
      if (typeof value === 'string') {
        var trimmed = value.trim().replace(/^\uFEFF/, '');
        var fenced = /^```(?:json)?\s*\n([\s\S]*?)\n```\s*$/i.exec(trimmed);
        if (fenced) {
          try {
            var envelope = JSON.parse(fenced[1]);
            if (envelope && typeof envelope === 'object' && ['reply','response','content','text','answer','explanation','message','choices','output'].some(function (key) { return key in envelope; })) value = envelope;
            else return trimmed;
          } catch (_) { return trimmed; }
          continue;
        }
        if (!/^[{\["]/.test(trimmed)) return trimmed;
        try { value = JSON.parse(trimmed); } catch (_) { return trimmed; }
      } else if (Array.isArray(value)) {
        var blocks = value.filter(function (block) { return block && ['text','output_text'].includes(block.type) && (typeof block.text === 'string' || typeof block.text?.value === 'string'); });
        if (!blocks.length) return '```json\n' + json(value) + '\n```';
        value = blocks.map(function (block) { return typeof block.text === 'string' ? block.text : block.text.value; }).join('\n\n');
      } else if (value && typeof value === 'object' && !Array.isArray(value)) {
        if (Array.isArray(value.choices) && value.choices[0]?.message) { value = value.choices[0].message; continue; }
        var key = ['reply', 'response', 'content', 'text', 'answer', 'explanation', 'message'].find(function (k) {
          return typeof value[k] === 'string' || Array.isArray(value[k]) || (value[k] && typeof value[k] === 'object'
            && ['content','text','message','reply','response'].some(function (field) { return field in value[k]; }));
        });
        if (!key && Array.isArray(value.output)) {
          value = value.output.flatMap(function (block) { return Array.isArray(block?.content) ? block.content : []; });
          continue;
        }
        if (!key) return '```json\n' + json(value) + '\n```';
        value = value[key];
      } else return '```json\n' + json(value) + '\n```';
    }
    return typeof value === 'string' ? value : json(value);
  }
  function escape(text) {
    return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function render(value) {
    var text = normalize(value).slice(0, 120000);
    if (!window.marked || !window.DOMPurify) return '<p>' + escape(text).replace(/\n/g, '<br>') + '</p>';
    if (!parser) {
      parser = new window.marked.Marked({gfm: true, breaks: false});
      parser.use({extensions: [{name: 'ipMath', level: 'inline',
        start: function (source) { return source.search(/\$|\\[([]/); },
        tokenizer: function (source) {
          if (!window.katex || mathCount >= 32) return;
          var opener = ['$$','\\[','\\(','$'].find(function (delimiter) { return source.startsWith(delimiter); });
          if (!opener) return;
          var closer = {'$$':'$$','\\[':'\\]','\\(':'\\)','$':'$'}[opener];
          var end = source.indexOf(closer, opener.length);
          while (end !== -1 && source[end - 1] === '\\') end = source.indexOf(closer, end + closer.length);
          if (end === -1 || end === opener.length || end - opener.length > 2000) return;
          var expression = source.slice(opener.length, end);
          if (opener === '$' && (/^\s|\s$|\n/.test(expression) || /\d/.test(source[end + 1] || ''))) return;
          mathCount += 1;
          return {type: 'ipMath', raw: source.slice(0, end + closer.length), expression: expression,
            display: opener === '$$' || opener === '\\['};
        },
        renderer: function (token) {
          var index = mathSources.push(token) - 1;
          return '<span data-ip-math="' + index + '"></span>';
        },
      }]});
    }
    mathSources = []; mathCount = 0;
    var html = parser.parse(text);
    var safe = window.DOMPurify.sanitize(html, {
      ALLOWED_TAGS: ['span','p','br','strong','em','del','s','h1','h2','h3','h4','h5','h6',
        'ul','ol','li','blockquote','pre','code','hr','a','table','thead','tbody','tr','th','td','sup','sub'],
      ALLOWED_ATTR: ['href','title','start','colspan','rowspan','data-ip-math'],
      ALLOW_DATA_ATTR: false,
      ALLOW_ARIA_ATTR: false,
      FORBID_TAGS: ['script','style','iframe','svg','math','img','video','audio','form','input'],
    });
    var holder = document.createElement('div'); holder.innerHTML = safe;
    holder.querySelectorAll('[data-ip-math]').forEach(function (node) {
      var token = mathSources[Number(node.getAttribute('data-ip-math'))];
      if (!token) { node.removeAttribute('data-ip-math'); return; }
      node.className = token.display ? 'ip-math ip-math-display' : 'ip-math';
      node.setAttribute('data-ip-math', token.expression);
      try {
        // Only vetted local KaTeX output is inserted after sanitization. Raw
        // response HTML never receives style or MathML permissions.
        node.innerHTML = window.katex.renderToString(token.expression, {displayMode: token.display,
          trust: false, strict: 'error', throwOnError: true, maxSize: 10, maxExpand: 1000, macros: {}});
      } catch (_) { node.textContent = token.raw; }
    });
    holder.querySelectorAll('pre,table,.ip-math-display').forEach(function (region) {
      region.tabIndex = 0; region.setAttribute('role', 'region');
      region.setAttribute('aria-label', region.tagName === 'PRE' ? 'Code example' : region.tagName === 'TABLE' ? 'Response table' : 'Mathematical expression');
    });
    return holder.innerHTML;
  }
  function plainText(value, forSpeech) {
    var holder = document.createElement('div');
    holder.innerHTML = render(value);
    if (forSpeech) holder.querySelectorAll('pre, table').forEach(function (node) { node.remove(); });
    holder.querySelectorAll('[data-ip-math]').forEach(function (node) { node.textContent = node.getAttribute('data-ip-math'); });
    holder.querySelectorAll('th,td').forEach(function (node) { node.after(document.createTextNode('\t')); });
    holder.querySelectorAll('p,li,h1,h2,h3,h4,h5,h6,pre,tr,blockquote,br').forEach(function (node) { node.after(document.createTextNode('\n')); });
    return (holder.textContent || '').replace(/\n{3,}/g, '\n\n').trim();
  }
  function speech(value) { return plainText(value, true).replace(/\s+/g, ' ').trim(); }
  window.IPRichText = {normalize: normalize, render: render, speech: speech, plainText: plainText};
})();
