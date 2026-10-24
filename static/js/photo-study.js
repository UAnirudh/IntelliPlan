/* Photo notes stay in this tab; only an explicitly saved deck is persisted. */
(function () {
  'use strict';
  var root = document.getElementById('photoStudy');
  if (!root) return;
  function el(id) { return document.getElementById(id); }
  var source = el('psSource'), file, previewUrl, practice, receipt, busy = false, pendingRequest;
  var current = 0, answers = [], complete = false;
  function node(tag, text, className) {
    var item = document.createElement(tag);
    if (text != null) item.textContent = text;
    if (className) item.className = className;
    return item;
  }
  function rich(value, className) {
    var item = node('div', null, 'ps-rich ' + (className || ''));
    if (window.IPRichText) item.innerHTML = window.IPRichText.render(value);
    else item.textContent = value;
    return item;
  }
  function status(message, error) {
    var item = el('psStatus');
    item.textContent = message; item.hidden = !message;
    item.dataset.error = error ? 'true' : 'false';
    item.setAttribute('role', error ? 'alert' : 'status');
    if (error) item.scrollIntoView({block: 'center', behavior: 'auto'});
  }
  function controls() {
    ['psChoose', 'psCamera', 'psFile', 'psCameraFile', 'psMode', 'psCount', 'psEdit', 'psRestart'].forEach(function (id) { el(id).disabled = busy; });
    source.readOnly = busy;
    el('psRead').disabled = busy || !file;
    el('psGenerate').disabled = busy || source.value.trim().length < 20;
    el('psSave').disabled = busy || !receipt || !!el('psSave').dataset.saved;
    root.setAttribute('aria-busy', busy ? 'true' : 'false');
    el('psCharacters').textContent = source.value.length.toLocaleString() + ' / 12,000';
  }
  async function api(path, body) {
    var controller = new AbortController(), timedOut = false;
    pendingRequest = controller;
    var timeout = window.setTimeout(function () { timedOut = true; controller.abort(); }, 60000);
    var options = {method: 'POST', credentials: 'same-origin', cache: 'no-store', signal: controller.signal};
    if (body instanceof FormData) options.body = body;
    else { options.body = JSON.stringify(body); options.headers = {'Content-Type': 'application/json'}; }
    try {
      var response = await fetch('/api/flashcards/photo/' + path, options);
      var result;
      try { result = await response.json(); } catch (error) {
        if (error.name === 'AbortError') throw error;
        throw new Error('The server could not complete that request. Try again shortly.');
      }
      if (!response.ok || result.error || result.refusal) {
        throw new Error(result.message || result.reply || (typeof result.error === 'string' ? result.error : 'Please try again shortly.'));
      }
      return result;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error(timedOut ? 'That took too long. Your preview is still here; please try again.' : 'The request was cancelled. Please try again.');
      throw error;
    } finally {
      window.clearTimeout(timeout);
      if (pendingRequest === controller) pendingRequest = null;
    }
  }
  function invalidate() {
    practice = null; receipt = null;
    el('psResults').hidden = true;
    el('psWorkspace').hidden = false;
    delete el('psSave').dataset.saved; el('psSaved').hidden = true;
    controls();
  }
  function selected(event) {
    var chosen = event.target.files[0];
    if (!chosen) return;
    if (chosen.size > 6 * 1024 * 1024 || !/^image\/(jpeg|png|webp)$/.test(chosen.type)) {
      event.target.value = ''; status('Choose a JPEG, PNG or WebP photo smaller than 6 MB.', true); return;
    }
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    file = chosen; previewUrl = URL.createObjectURL(file);
    el('psImage').src = previewUrl; el('psFileName').textContent = file.name;
    el('psPreview').hidden = false; el('psWarnings').hidden = true;
    source.value = ''; invalidate(); status('Photo ready. Read it, then check the text before creating practice.');
    event.target.value = '';
  }
  el('psChoose').addEventListener('click', function () { el('psFile').click(); });
  el('psCamera').addEventListener('click', function () { el('psCameraFile').click(); });
  el('psFile').addEventListener('change', selected); el('psCameraFile').addEventListener('change', selected);
  source.addEventListener('input', invalidate);
  el('psMode').addEventListener('change', invalidate); el('psCount').addEventListener('change', invalidate);
  el('psRead').addEventListener('click', async function () {
    if (busy || !file) return;
    busy = true; controls(); status('Reading your photo and checking the material…');
    try {
      var data = new FormData(); data.append('image', file);
      var result = await api('extract', data);
      source.value = result.text;
      el('psWarnings').textContent = (result.warnings || []).join(' ');
      el('psWarnings').hidden = !(result.warnings || []).length;
      invalidate(); status('Reading ready. Check the text, especially formulas and unclear sections.');
      source.focus();
    } catch (error) { status(error.message || 'The photo could not be read. Try again.', true); }
    finally { busy = false; controls(); }
  });
  function evidence(quote) {
    var details = node('details', null, 'ps-evidence');
    details.append(node('summary', 'See supporting text from your notes'), rich(quote));
    return details;
  }
  function button(text, handler, className) {
    var item = node('button', text, 'ps-btn ' + (className || ''));
    item.type = 'button'; item.addEventListener('click', handler); return item;
  }
  function focusQuestion() {
    var target = el('psPractice').querySelector('[tabindex="-1"]');
    if (target) target.focus({preventScroll: true});
  }
  function choiceText(value) {
    var item = rich(value);
    // The answer button is the single keyboard target. Preserve sanitized
    // formulas and emphasis without nesting links or math focus stops.
    item.querySelectorAll('a').forEach(function (link) { link.replaceWith.apply(link, Array.from(link.childNodes)); });
    item.querySelectorAll('[tabindex]').forEach(function (child) { child.removeAttribute('tabindex'); });
    return item;
  }
  function quiz() {
    var host = el('psPractice'); host.replaceChildren();
    var correct = answers.filter(function (value, index) { return value === practice.items[index].answer; }).length;
    if (complete) {
      var summary = node('div', null, 'ps-complete');
      var heading = node('h3', correct + ' of ' + practice.items.length + ' correct'); heading.tabIndex = -1;
      summary.append(heading, node('p', 'Revisit the supporting text for anything you missed, then give it another try.'));
      summary.append(button('Review answers', function () { complete = false; current = 0; quiz(); focusQuestion(); }));
      host.append(summary); return;
    }
    var item = practice.items[current], answered = answers[current] !== undefined;
    var progress = node('div', null, 'ps-progress');
    progress.append(node('span', 'Question ' + (current + 1) + ' of ' + practice.items.length), node('span', correct + ' correct so far'));
    var question = rich(item.question, 'ps-question'); question.tabIndex = -1;
    host.append(progress, question);
    var choices = node('div', null, 'ps-choices');
    item.options.forEach(function (choice, index) {
      var option = button('', function () { answers[current] = index; quiz(); var feedback = host.querySelector('.ps-feedback'); if (feedback) feedback.focus({preventScroll: true}); }, 'ps-choice');
      option.append(node('span', String.fromCharCode(65 + index)), choiceText(choice));
      option.disabled = answered;
      if (answered) {
        if (index === item.answer) option.dataset.correct = 'true';
        if (index === answers[current] && index !== item.answer) option.dataset.wrong = 'true';
        if (index === item.answer) option.append(node('span', '✓ Correct answer'));
        else if (index === answers[current]) option.append(node('span', 'Your choice'));
      }
      choices.append(option);
    });
    host.append(choices);
    if (answered) {
      var feedback = node('div', null, 'ps-feedback'); feedback.tabIndex = -1; feedback.setAttribute('role', 'status');
      feedback.append(node('strong', answers[current] === item.answer ? 'That’s right.' : 'Not quite. Here’s why.'), rich(item.explanation), evidence(item.source_quote));
      host.append(feedback);
    }
    var nav = node('div', null, 'ps-nav');
    var previous = button('Previous', function () { current--; quiz(); focusQuestion(); }); previous.disabled = current === 0;
    var next = button(current === practice.items.length - 1 ? 'See results' : 'Next question', function () {
      if (current === practice.items.length - 1) complete = true; else current++;
      quiz(); focusQuestion();
    }, 'ps-primary'); next.disabled = !answered;
    nav.append(previous, next); host.append(nav);
  }
  function flashcards() {
    var host = el('psPractice'); host.replaceChildren();
    var list = node('div', null, 'ps-card-list');
    practice.items.forEach(function (item, index) {
      var card = node('article', null, 'ps-card');
      card.append(node('h3', 'Card ' + (index + 1)), rich(item.front, 'ps-question'));
      var answer = node('div', null, 'ps-card-answer'); answer.hidden = true;
      answer.append(rich(item.back), evidence(item.source_quote));
      var reveal = button('Reveal answer', function () { answer.hidden = !answer.hidden; reveal.textContent = answer.hidden ? 'Reveal answer' : 'Hide answer'; reveal.setAttribute('aria-expanded', String(!answer.hidden)); });
      reveal.setAttribute('aria-expanded', 'false');
      card.append(reveal, answer); list.append(card);
    });
    host.append(list);
  }
  el('psGenerate').addEventListener('click', async function () {
    if (busy || source.value.trim().length < 20) return;
    busy = true; controls(); status('Creating practice from your reviewed notes and checking each answer…');
    try {
      var result = await api('generate', {source: source.value, mode: el('psMode').value, count: Number(el('psCount').value)});
      practice = result; receipt = result.receipt; current = 0; answers = []; complete = false;
      delete el('psSave').dataset.saved; el('psSaved').hidden = true;
      el('psResultTitle').textContent = result.title;
      el('psResultHint').textContent = result.mode === 'quiz' ? 'Choose an answer to get feedback. Each explanation links back to your notes.' : 'Try answering before you reveal. Check the supporting text whenever you need it.';
      el('psSave').textContent = result.mode === 'quiz' ? 'Save questions as flashcards' : 'Save flashcard deck';
      if (result.mode === 'quiz') quiz(); else flashcards();
      el('psWorkspace').hidden = true;
      el('psResults').hidden = false; status('Your practice is ready. Check anything uncertain against your original notes.');
      el('psResultTitle').focus({preventScroll: true}); el('psResults').scrollIntoView({block: 'start', behavior: 'auto'});
    } catch (error) { status(error.message || 'Practice could not be created. Please try again.', true); }
    finally { busy = false; controls(); }
  });
  el('psRestart').addEventListener('click', function () { current = 0; answers = []; complete = false; if (practice.mode === 'quiz') quiz(); else flashcards(); focusQuestion(); });
  el('psEdit').addEventListener('click', function () { invalidate(); source.focus(); source.scrollIntoView({block: 'center'}); status('Update the source, then create fresh practice.'); });
  el('psSave').addEventListener('click', async function () {
    if (busy || !receipt || el('psSave').dataset.saved) return;
    busy = true; controls(); status('Checking your school rules and saving your flashcards…');
    try {
      var result = await api('save', {receipt: receipt});
      el('psSave').dataset.saved = 'true'; el('psSave').textContent = 'Saved to your decks';
      el('psSaved').hidden = false; el('psSaved').href = result.url;
      status(result.added + ' flashcards saved. Your next reviews are ready in Flashcards.');
    } catch (error) { status(error.message || 'The deck could not be saved. Please try again.', true); }
    finally { busy = false; controls(); }
  });
  window.addEventListener('pagehide', function () {
    if (pendingRequest) pendingRequest.abort();
    if (previewUrl) URL.revokeObjectURL(previewUrl);
  });
  controls();
})();
