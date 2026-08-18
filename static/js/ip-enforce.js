/* ═══════════════════════════════════════════════════════════════════
   ip-enforce.js — what Active study does when you stop studying.

   The camera check-in (ip-focus.js) already works out when a student has
   genuinely drifted. Until now the only response was a dismissible line
   of text, which is easy to ignore — so for the student who most needed
   it, it did nothing at all.

   Three escalations, chosen by the student during onboarding and
   changeable in Settings. Never imposed: the default is still the gentle
   nudge, because an app that starts blaring at someone who never asked
   it to is an app they uninstall.

     alarm     a sound at full volume — theirs, if they upload one
     takeover  the screen fills with something you cannot read past
     stakes    the session forfeits the sparks it has earned so far

   Two rules hold across all three:

     1. Nothing fires without a grace period. "Looked away" and "left" are
        different things, and an alarm that goes off when someone reaches
        for a textbook gets the whole feature switched off within a day.
     2. Everything stops the instant they come back. Enforcement that
        outlives the distraction is punishment, and punishment is not what
        makes anybody work.
   ══════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var DEFAULTS = { mode: 'off', grace_seconds: 25, alarm_url: '' };

  /* Escalation steps, in seconds past the grace period. Coming back at any
     point resets to zero — this is a ladder, not a sentence. */
  var ESCALATE_AT = [0, 20, 45];

  function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }

  /* ── Audio ────────────────────────────────────────────────────────
     Deliberately WebAudio rather than a bare <audio> element.

     An <audio> tag's volume is capped at 1.0 = "the volume the page was
     already at", and browsers duck or mute media in a backgrounded tab —
     which is exactly the tab an alarm needs to be heard from. Routing
     through a GainNode lets the signal be driven above unity, and an
     oscillator fallback means the alarm still works when no file has
     been uploaded and nothing has to be fetched at the moment it is
     needed most. */
  function Alarm(url) {
    this.url = url || '';
    this.ctx = null;
    this.gain = null;
    this.source = null;
    this.buffer = null;
    this.playing = false;
    this._loading = null;
  }

  Alarm.prototype._context = function () {
    if (this.ctx) return this.ctx;
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    this.ctx = new Ctx();
    this.gain = this.ctx.createGain();
    this.gain.gain.value = 1;
    this.gain.connect(this.ctx.destination);
    return this.ctx;
  };

  /* Warm the context and decode the sound while the student is still
     present. Doing this at alarm time would mean a fetch, a decode and an
     autoplay-policy prompt at the one moment latency is unacceptable. */
  Alarm.prototype.prime = function () {
    var self = this;
    var ctx = this._context();
    if (!ctx) return Promise.resolve(false);
    if (ctx.state === 'suspended') { try { ctx.resume(); } catch (e) {} }
    if (!this.url || this._loading) return this._loading || Promise.resolve(true);

    this._loading = fetch(this.url, { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.arrayBuffer() : Promise.reject(r.status); })
      .then(function (buf) {
        return new Promise(function (resolve, reject) {
          // Callback form: Safari still does not return a promise here.
          ctx.decodeAudioData(buf, resolve, reject);
        });
      })
      .then(function (decoded) { self.buffer = decoded; return true; })
      .catch(function () {
        // A missing or undecodable upload must not mean silence — fall
        // back to the built-in tone rather than failing to enforce.
        self.buffer = null;
        return false;
      });
    return this._loading;
  };

  Alarm.prototype.start = function (intensity) {
    var ctx = this._context();
    if (!ctx || this.playing) return;
    if (ctx.state === 'suspended') { try { ctx.resume(); } catch (e) {} }

    this.playing = true;
    // Above 1.0 on purpose: this is the one sound in the app allowed to be
    // louder than everything else the student is doing.
    this.gain.gain.value = clamp(1 + intensity * 1.5, 1, 4);

    if (this.buffer) {
      var src = ctx.createBufferSource();
      src.buffer = this.buffer;
      src.loop = true;
      src.connect(this.gain);
      src.start(0);
      this.source = src;
      return;
    }

    // Built-in tone: two detuned square waves warbling against each other.
    // Chosen because it is genuinely unpleasant to sit through, which is
    // the entire specification for an alarm.
    var osc = ctx.createOscillator();
    var lfo = ctx.createOscillator();
    var lfoGain = ctx.createGain();
    osc.type = 'square';
    osc.frequency.value = 880;
    lfo.frequency.value = 6;
    lfoGain.gain.value = 260;
    lfo.connect(lfoGain);
    lfoGain.connect(osc.frequency);
    osc.connect(this.gain);
    osc.start(0);
    lfo.start(0);
    this.source = { stop: function () { try { osc.stop(); lfo.stop(); } catch (e) {} } };
  };

  Alarm.prototype.stop = function () {
    if (!this.playing) return;
    this.playing = false;
    try { if (this.source) this.source.stop(); } catch (e) {}
    this.source = null;
  };

  /* ── System volume ────────────────────────────────────────────────
     A web page cannot change the operating system's volume, and no amount
     of wanting it to will change that — the gain above is as loud as a
     browser tab is permitted to be. The desktop build can do it properly,
     so ask it when it is there and carry on when it is not. */
  function raiseSystemVolume() {
    try {
      var d = window.IPDesktop;
      if (d && typeof d.setSystemVolume === 'function') {
        d.setSystemVolume(1);
        return true;
      }
    } catch (e) {}
    return false;
  }

  /* ── Takeover ─────────────────────────────────────────────────────
     Fills the viewport with motion and covers the page. Not a modal: it
     has no dismiss control, because a dismiss button is just a thing to
     click without reading. It leaves when the student does the one thing
     that is supposed to make it leave. */
  function Takeover() {
    this.el = null;
  }

  Takeover.prototype.show = function (title, body) {
    if (this.el) return;
    var el = document.createElement('div');
    el.className = 'ipe-takeover';
    el.setAttribute('role', 'alertdialog');
    el.setAttribute('aria-live', 'assertive');
    el.innerHTML =
      '<div class="ipe-takeover__noise" aria-hidden="true"></div>' +
      '<div class="ipe-takeover__inner">' +
        '<div class="ipe-takeover__eyebrow">Active session</div>' +
        '<h2 class="ipe-takeover__title"></h2>' +
        '<p class="ipe-takeover__body"></p>' +
        '<div class="ipe-takeover__hint">This clears the moment you are back at your desk.</div>' +
      '</div>';
    el.querySelector('.ipe-takeover__title').textContent = title;
    el.querySelector('.ipe-takeover__body').textContent = body;
    document.body.appendChild(el);
    // Next frame so the entry transition actually runs.
    requestAnimationFrame(function () { el.classList.add('is-on'); });
    this.el = el;
  };

  Takeover.prototype.intensify = function (level) {
    if (this.el) this.el.setAttribute('data-level', String(level));
  };

  Takeover.prototype.hide = function () {
    var el = this.el;
    if (!el) return;
    this.el = null;
    el.classList.remove('is-on');
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 260);
  };

  /* ── Stakes ───────────────────────────────────────────────────────
     The third option, and deliberately not another sensory one. An alarm
     and a takeover both work by being unpleasant, and unpleasant is easy
     to route around — headphones off, tab in another window.

     This one costs something instead. The sparks the session has earned
     start draining while the student is away, and the running total is on
     screen while it happens, so the choice to stay away is a visible one.
     It only ever forfeits what *this session* earned: it cannot reach into
     a balance built up over weeks, because a planner that can take real
     progress away for standing up is one nobody will risk opening. */
  function Stakes(host) {
    this.host = host;
    this.el = null;
    this.forfeited = 0;
  }

  Stakes.prototype.show = function () {
    if (this.el) return;
    var el = document.createElement('div');
    el.className = 'ipe-stakes';
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.innerHTML =
      '<div class="ipe-stakes__title">This session is losing its sparks</div>' +
      '<div class="ipe-stakes__count"><span class="ipe-stakes__num">0</span> forfeited</div>' +
      '<div class="ipe-stakes__hint">Only sparks earned in this session — your balance is safe.</div>';
    (this.host || document.body).appendChild(el);
    this.el = el;
  };

  Stakes.prototype.tick = function (amount) {
    this.forfeited += amount;
    if (this.el) {
      this.el.querySelector('.ipe-stakes__num').textContent = String(this.forfeited);
    }
  };

  Stakes.prototype.hide = function () {
    if (this.el && this.el.parentNode) this.el.parentNode.removeChild(this.el);
    this.el = null;
  };

  Stakes.prototype.reset = function () { this.forfeited = 0; };

  /* ── Controller ───────────────────────────────────────────────────── */
  function Enforcer(options) {
    options = options || {};
    this.settings = Object.assign({}, DEFAULTS, options.settings || {});
    this.onForfeit = options.onForfeit || function () {};
    this.onEscalate = options.onEscalate || function () {};

    this.alarm = new Alarm(this.settings.alarm_url);
    this.takeover = new Takeover();
    this.stakes = new Stakes(options.stakesHost);

    this.awaySince = 0;
    this.level = -1;
    this.timer = null;
    this.active = false;
  }

  Enforcer.prototype.mode = function () { return this.settings.mode || 'off'; };

  /** Called once when a session starts, while the student is still here. */
  Enforcer.prototype.prime = function () {
    if (this.mode() === 'alarm') this.alarm.prime();
  };

  /** The tracker says they are away. Idempotent — safe to call per sample. */
  Enforcer.prototype.onAway = function () {
    if (this.mode() === 'off' || this.active) return;
    this.active = true;
    this.awaySince = Date.now();
    this.level = -1;
    var self = this;
    this.timer = window.setInterval(function () { self._evaluate(); }, 1000);
    this._evaluate();
  };

  /** They are back. Everything stops, immediately and completely. */
  Enforcer.prototype.onReturn = function () {
    if (!this.active) return;
    this.active = false;
    window.clearInterval(this.timer);
    this.timer = null;
    this.alarm.stop();
    this.takeover.hide();
    this.stakes.hide();
    this.stakes.reset();
    this.level = -1;
  };

  Enforcer.prototype.stop = function () { this.onReturn(); };

  Enforcer.prototype._evaluate = function () {
    // `|| 25` would be wrong here: 0 is a legitimate value, and it is the
    // one preview() uses to fire immediately. Only an absent or unparseable
    // setting should fall back to the default. The floor is 0 rather than 5
    // for the same reason — the server already clamps real preferences to a
    // sane minimum, so the only caller that reaches 0 is the preview.
    var configured = Number(this.settings.grace_seconds);
    var grace = clamp(Number.isFinite(configured) ? configured : 25, 0, 300);
    var away = (Date.now() - this.awaySince) / 1000;
    if (away < grace) return;

    var past = away - grace;
    var level = 0;
    for (var i = ESCALATE_AT.length - 1; i >= 0; i--) {
      if (past >= ESCALATE_AT[i]) { level = i; break; }
    }

    if (level !== this.level) {
      this.level = level;
      this._apply(level);
      this.onEscalate(level);
    } else if (this.mode() === 'stakes') {
      // Stakes is the one mode with something to do every second rather
      // than only on escalation.
      this._drain(level);
    }
  };

  Enforcer.prototype._apply = function (level) {
    switch (this.mode()) {
      case 'alarm':
        if (level === 0) raiseSystemVolume();
        this.alarm.stop();
        this.alarm.start(level);
        break;
      case 'takeover':
        this.takeover.show(
          'Come back to it.',
          'Your session is still running and this block is not going to finish itself.'
        );
        this.takeover.intensify(level);
        break;
      case 'stakes':
        this.stakes.show();
        this._drain(level);
        break;
      default:
        break;
    }
  };

  Enforcer.prototype._drain = function (level) {
    var amount = level + 1;      // costs more the longer they stay away
    this.stakes.tick(amount);
    this.onForfeit(amount, this.stakes.forfeited);
  };

  /* ── Settings ─────────────────────────────────────────────────────── */
  function load() {
    return fetch('/api/focus/enforcement', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        return (d && d.status === 'ok') ? d : Object.assign({}, DEFAULTS);
      })
      .catch(function () { return Object.assign({}, DEFAULTS); });
  }

  /** Fire one escalation on demand, so Settings can offer a real preview. */
  function preview(settings, seconds) {
    var e = new Enforcer({ settings: settings });
    e.settings.grace_seconds = 0;
    e.prime();
    e.onAway();
    window.setTimeout(function () { e.stop(); }, (seconds || 3) * 1000);
    return e;
  }

  window.IPEnforce = {
    Enforcer: Enforcer,
    load: load,
    preview: preview,
    MODES: ['off', 'alarm', 'takeover', 'stakes']
  };
})();
