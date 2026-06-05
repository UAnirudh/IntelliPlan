/** Shared chat panel for live sessions and study groups. */
function initSessionChat(opts) {
  const host = document.getElementById(opts.hostId);
  if (!host) return;
  const ctxType = opts.contextType;
  const ctxId = opts.contextId;
  const canSave = !!opts.canSave;

  host.innerHTML = `
    <div class="sess-chat-log" id="sessChatLog" role="log" aria-live="polite"></div>
    <div class="sess-chat-compose">
      <textarea id="sessChatInput" rows="2" placeholder="Share a link, formula, or note…" maxlength="8000"></textarea>
      <div class="sess-chat-actions">
        <button type="button" class="btn-primary" id="sessChatSend" style="padding:8px 14px;border:none;cursor:pointer;font-size:0.84rem;">Send</button>
        <span id="sessChatStatus" style="font-size:0.76rem;color:var(--text-muted);"></span>
      </div>
    </div>`;

  const log = document.getElementById('sessChatLog');
  const input = document.getElementById('sessChatInput');
  const status = document.getElementById('sessChatStatus');

  function esc(s) {
    return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  function render(messages) {
    if (!messages.length) {
      log.innerHTML = '<div class="sess-chat-empty">No messages yet — share materials with the room.</div>';
      return;
    }
    log.innerHTML = messages.map(m => `
      <div class="sess-chat-msg" data-id="${m.id}">
        <div class="sess-chat-meta"><strong>${esc(m.author_name)}</strong> · ${m.created_at ? new Date(m.created_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : ''}</div>
        <div class="sess-chat-body">${esc(m.body).replace(/\n/g,'<br>')}</div>
        ${canSave ? `<button type="button" class="sess-chat-save" data-save="${m.id}" ${m.saved_to_library ? 'disabled' : ''}>${m.saved_to_library ? 'Saved' : 'Save to notes'}</button>` : ''}
      </div>`).join('');
    log.scrollTop = log.scrollHeight;
    log.querySelectorAll('[data-save]').forEach(btn => {
      btn.addEventListener('click', () => saveMsg(parseInt(btn.dataset.save, 10), btn));
    });
  }

  async function load() {
    try {
      const r = await fetch(`/api/sessions/${ctxType}/${ctxId}/messages`);
      const d = await r.json();
      if (d.status === 'ok') render(d.messages || []);
    } catch (e) { /* ignore */ }
  }

  async function send() {
    const body = input.value.trim();
    if (!body) return;
    status.textContent = 'Sending…';
    try {
      const r = await fetch(`/api/sessions/${ctxType}/${ctxId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ body }),
      });
      const d = await r.json();
      if (d.status === 'ok') {
        input.value = '';
        status.textContent = '';
        await load();
      } else {
        status.textContent = d.message || 'Send failed';
      }
    } catch (e) {
      status.textContent = 'Network error';
    }
  }

  async function saveMsg(id, btn) {
    btn.disabled = true;
    try {
      const r = await fetch(`/api/sessions/messages/${id}/save`, { method: 'POST' });
      const d = await r.json();
      if (d.status === 'ok') {
        btn.textContent = 'Saved ✓';
      } else {
        btn.disabled = false;
        alert(d.message || 'Could not save');
      }
    } catch (e) {
      btn.disabled = false;
    }
  }

  document.getElementById('sessChatSend').addEventListener('click', send);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });
  load();
  setInterval(load, 8000);
}
