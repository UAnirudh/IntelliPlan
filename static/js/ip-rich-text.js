/* Shared, sanitized AI prose. Executable model output is never supported. */
(function () {
  'use strict';
  var parser, mathSources = [], mathCount = 0;
  function json(value) { try { return JSON.stringify(value, null, 2); } catch (_) { return 'Response could not be displayed.'; } }
  function parseEnvelope(source) {
    // Some providers return a JSON envelope with single TeX backslashes. JSON
    // treats \frac/\text as control escapes and rejects \ce/\[ entirely. Repair
    // only recognizable TeX commands, preserving proper JSON escapes and code.
    if (/[\$]|\\(?:ce|pu)\{|\\[([]/.test(source)) {
      var command = /^\\(?:ce|pu|frac|dfrac|tfrac|sqrt|text|mathrm|mathbf|displaystyle|begin|end|alpha|beta|gamma|delta|theta|lambda|mu|pi|sigma|omega|Delta|rightarrow|leftarrow|leftrightarrow|rightleftharpoons|to|times|cdot|sum|int|lim|infty|left|right)\b|^\\[()[\]]/;
      source = source.replace(/\\\\|\\(?:ce|pu|frac|dfrac|tfrac|sqrt|text|mathrm|mathbf|displaystyle|begin|end|alpha|beta|gamma|delta|theta|lambda|mu|pi|sigma|omega|Delta|rightarrow|leftarrow|leftrightarrow|rightleftharpoons|to|times|cdot|sum|int|lim|infty|left|right)\b|\\[()[\]]|\\./g, function (match) {
        return command.test(match) ? '\\' + match : match;
      });
    }
    return JSON.parse(source);
  }
  function normalize(value) {
    for (var depth = 0; depth < 8; depth++) {
      if (typeof value === 'string') {
        var trimmed = value.trim().replace(/^\uFEFF/, '');
        var fenced = /^```(?:json)?\s*\n([\s\S]*?)\n```\s*$/i.exec(trimmed);
        if (fenced) {
          try {
            var envelope = parseEnvelope(fenced[1]);
            if (envelope && typeof envelope === 'object' && ['reply','response','content','text','answer','explanation','message','choices','output'].some(function (key) { return key in envelope; })) value = envelope;
            else return trimmed;
          } catch (_) { return trimmed; }
          continue;
        }
        if (!/^[{\["]/.test(trimmed)) return trimmed;
        try { value = parseEnvelope(trimmed); } catch (_) { return trimmed; }
      } else if (Array.isArray(value)) {
        var blocks = value.filter(function (block) { return block && (!block.type || ['text','output_text'].includes(block.type)) && (typeof block.text === 'string' || typeof block.text?.value === 'string'); });
        if (!blocks.length || blocks.length !== value.length) return '```json\n' + json(value) + '\n```';
        value = blocks.map(function (block) { return typeof block.text === 'string' ? block.text : block.text.value; }).join('\n\n');
      } else if (value && typeof value === 'object' && !Array.isArray(value)) {
        if (Array.isArray(value.choices) && value.choices.length === 1 && value.choices[0]?.message) { value = value.choices[0].message; continue; }
        if (Array.isArray(value.candidates) && value.candidates.length === 1 && value.candidates[0]?.content?.parts) { value = value.candidates[0].content.parts; continue; }
        var key = ['reply', 'response', 'content', 'text', 'answer', 'explanation', 'message'].find(function (k) {
          return typeof value[k] === 'string' || Array.isArray(value[k]) || (value[k] && typeof value[k] === 'object'
            && ['content','text','message','reply','response'].some(function (field) { return field in value[k]; }));
        });
        if (!key && Array.isArray(value.output) && value.output.length && value.output.every(function (block) {
          return Array.isArray(block?.content) && (!block.type || block.type === 'message');
        })) {
          value = value.output.flatMap(function (block) { return block.content; });
          continue;
        }
        if (!key) return '```json\n' + json(value) + '\n```';
        // Only recognized transport metadata may accompany a prose field.
        // Domain objects such as {answer, explanation, steps} stay intact.
        var metadata = ['role','type','id','object','model','created','usage','status',
          'conversation_id','assessment_protection','finish_reason'];
        if (Object.keys(value).some(function (field) { return field !== key && !metadata.includes(field); })) {
          return '```json\n' + json(value) + '\n```';
        }
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
      function placeholder(token) {
        var index = mathSources.push(token) - 1;
        return '<span data-ip-math="' + index + '"></span>';
      }
      parser.use({renderer: {code: function (token) {
        var language = (token.lang || '').split(/\s/)[0].replace(/[^a-zA-Z0-9_+#.-]/g, '').slice(0, 32);
        return '<pre data-ip-language="' + escape(language || 'text') + '"><code>' + escape(token.text) + '</code></pre>';
      }}, extensions: [{name: 'ipDisplayMath', level: 'block',
        start: function (source) { return source.search(/(?:^|\n)[ \t]*(?:\$\$|\\\[)/); },
        tokenizer: function (source) {
          if (!window.katex || mathCount >= 32) return;
          var opener = /^(?: {0,3})(\$\$|\\\[)/.exec(source);
          if (!opener) return;
          var closer = opener[1] === '$$' ? '$$' : '\\]';
          var end = source.indexOf(closer, opener[0].length);
          if (end === -1 || end - opener[0].length > 2000) return;
          var expression = source.slice(opener[0].length, end).trim();
          if (!expression) return;
          mathCount++;
          return {type: 'ipDisplayMath', raw: source.slice(0, end + closer.length), expression: expression, display: true};
        }, renderer: placeholder,
      }, {name: 'ipMath', level: 'inline',
        start: function (source) { return source.search(/\$|\\[([]|\\(?:ce|pu)\{/); },
        tokenizer: function (source) {
          if (!window.katex || mathCount >= 32) return;
          if (/^\\(?:ce|pu)\{/.test(source)) {
            var depth = 0;
            for (var position = source.indexOf('{'); position < Math.min(source.length, 2000); position++) {
              if (source[position] === '{') depth++;
              else if (source[position] === '}' && --depth === 0) {
                mathCount++;
                var raw = source.slice(0, position + 1);
                return {type: 'ipMath', raw: raw, expression: raw, display: false};
              }
            }
            return;
          }
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
        renderer: placeholder,
      }]});
    }
    mathSources = []; mathCount = 0;
    var html = parser.parse(text);
    var safe = window.DOMPurify.sanitize(html, {
      ALLOWED_TAGS: ['span','p','br','strong','em','del','s','h1','h2','h3','h4','h5','h6',
        'ul','ol','li','blockquote','pre','code','hr','a','table','thead','tbody','tr','th','td','sup','sub'],
      ALLOWED_ATTR: ['href','title','start','colspan','rowspan','data-ip-math','data-ip-language'],
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
    holder.querySelectorAll('table').forEach(function (table) {
      var scroll = document.createElement('div'); scroll.className = 'ip-table-scroll';
      scroll.tabIndex = 0; scroll.setAttribute('role', 'region'); scroll.setAttribute('aria-label', 'Response table');
      table.before(scroll); scroll.append(table);
    });
    holder.querySelectorAll('pre,.ip-math').forEach(function (region) {
      region.tabIndex = 0; region.setAttribute('role', 'region');
      region.setAttribute('aria-label', region.tagName === 'PRE' ? 'Code example' : 'Mathematical expression');
    });
    holder.querySelectorAll('a[href]').forEach(function (link) {
      link.setAttribute('rel', 'noopener noreferrer');
    });
    holder.querySelectorAll('pre').forEach(function (pre) {
      var language = (pre.getAttribute('data-ip-language') || 'text').replace(/[^a-zA-Z0-9_+#.-]/g, '').slice(0, 32);
      pre.removeAttribute('data-ip-language');
      var block = document.createElement('div'); block.className = 'ip-code-block';
      var toolbar = document.createElement('div'); toolbar.className = 'ip-code-toolbar';
      var label = document.createElement('span'); label.textContent = language;
      var copy = document.createElement('button'); copy.type = 'button'; copy.textContent = 'Copy code';
      copy.setAttribute('data-ip-copy-code', ''); copy.setAttribute('aria-label', 'Copy ' + language + ' code');
      var feedback = document.createElement('span'); feedback.setAttribute('role', 'status');
      feedback.className = 'sr-only';
      toolbar.append(label, copy, feedback); pre.before(block); block.append(toolbar, pre);
    });
    return holder.innerHTML;
  }
  function plainText(value, forSpeech) {
    var holder = document.createElement('div');
    holder.innerHTML = render(value);
    holder.querySelectorAll('.ip-code-toolbar').forEach(function (node) { node.remove(); });
    if (forSpeech) holder.querySelectorAll('pre, table').forEach(function (node) { node.remove(); });
    holder.querySelectorAll('[data-ip-math]').forEach(function (node) { node.textContent = node.getAttribute('data-ip-math'); });
    holder.querySelectorAll('th,td').forEach(function (node) { node.after(document.createTextNode('\t')); });
    holder.querySelectorAll('p,li,h1,h2,h3,h4,h5,h6,pre,tr,blockquote,br').forEach(function (node) { node.after(document.createTextNode('\n')); });
    return (holder.textContent || '').replace(/\n{3,}/g, '\n\n').trim();
  }
  function speech(value) { return plainText(value, true).replace(/\s+/g, ' ').trim(); }
  document.addEventListener('click', async function (event) {
    var button = event.target.closest?.('[data-ip-copy-code]');
    if (!button || button.disabled) return;
    var code = button.closest('.ip-code-block')?.querySelector('pre code');
    if (!code) return;
    button.disabled = true;
    var feedback = button.parentElement.querySelector('[role="status"]');
    try {
      await navigator.clipboard.writeText(code.textContent);
      button.textContent = 'Copied'; feedback.textContent = 'Code copied to clipboard.';
    } catch (_) {
      button.textContent = 'Select code'; feedback.textContent = 'Clipboard unavailable. Code selected; use your copy shortcut.';
      var selection = window.getSelection(), range = document.createRange();
      range.selectNodeContents(code); selection.removeAllRanges(); selection.addRange(range);
    } finally {
      button.disabled = false;
      setTimeout(function () { if (button.isConnected) button.textContent = 'Copy code'; }, 2000);
    }
  });
  window.IPRichText = {normalize: normalize, render: render, speech: speech, plainText: plainText};
})();
