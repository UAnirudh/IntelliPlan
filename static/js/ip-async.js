/* ==========================================================================
   ip-async.js — async UI state machine for IntelliPlan
   --------------------------------------------------------------------------
   Every async region on the site goes through IP.block(), which guarantees
   the four states a fetch can land in are all designed rather than left to
   chance:

     loading   skeleton shaped like the content that is coming
     ready     the rendered content
     empty     a composed "nothing here yet" view with a next action
     error     a plain message plus a Retry button

   Also here:
     IP.optimistic()  apply now, roll back if the server disagrees
     IP.paginate()    Load more / infinite scroll over a paged endpoint
     IP.boundary()    contain a render crash to one section of the page

   Depends on ip-core.js. No build step, no framework.
   ========================================================================== */
(function (window, document) {
  'use strict';

  var IP = window.IP || (window.IP = {});
  if (!IP.request) {
    console.error('[ip-async] ip-core.js must load first');
    return;
  }

  /* ── Skeletons ────────────────────────────────────────────────────── */

  /**
   * Skeletons mirror the shape of the real content so the layout does not
   * jump when data lands (CLS). A generic spinner tells the user nothing
   * about what is arriving; these do.
   */
  var SKELETONS = {
    text: function (n) {
      var out = '';
      for (var i = 0; i < (n || 3); i++) {
        var w = [100, 92, 68][i % 3];
        out += '<div class="ip-sk ip-sk--line" style="width:' + w + '%"></div>';
      }
      return '<div class="ip-sk-group">' + out + '</div>';
    },

    list: function (n) {
      var out = '';
      for (var i = 0; i < (n || 4); i++) {
        out += '<div class="ip-sk-row">' +
                 '<div class="ip-sk ip-sk--avatar"></div>' +
                 '<div class="ip-sk-row__body">' +
                   '<div class="ip-sk ip-sk--line" style="width:' + (58 + (i % 3) * 12) + '%"></div>' +
                   '<div class="ip-sk ip-sk--line ip-sk--sm" style="width:' + (34 + (i % 2) * 16) + '%"></div>' +
                 '</div>' +
               '</div>';
      }
      return '<div class="ip-sk-group">' + out + '</div>';
    },

    cards: function (n) {
      var out = '';
      for (var i = 0; i < (n || 3); i++) {
        out += '<div class="ip-sk-card">' +
                 '<div class="ip-sk ip-sk--line" style="width:44%"></div>' +
                 '<div class="ip-sk ip-sk--block"></div>' +
                 '<div class="ip-sk ip-sk--line ip-sk--sm" style="width:70%"></div>' +
               '</div>';
      }
      return '<div class="ip-sk-cards">' + out + '</div>';
    },

    stats: function (n) {
      var out = '';
      for (var i = 0; i < (n || 4); i++) {
        out += '<div class="ip-sk-stat">' +
                 '<div class="ip-sk ip-sk--num"></div>' +
                 '<div class="ip-sk ip-sk--line ip-sk--sm" style="width:64%"></div>' +
               '</div>';
      }
      return '<div class="ip-sk-stats">' + out + '</div>';
    },

    table: function (n) {
      var out = '<div class="ip-sk-trow ip-sk-trow--head">' +
                  '<div class="ip-sk ip-sk--line ip-sk--sm"></div>'.repeat(4) +
                '</div>';
      for (var i = 0; i < (n || 5); i++) {
        out += '<div class="ip-sk-trow">' +
                 '<div class="ip-sk ip-sk--line ip-sk--sm"></div>'.repeat(4) +
               '</div>';
      }
      return '<div class="ip-sk-table">' + out + '</div>';
    },

    hero: function () {
      return '<div class="ip-sk-hero">' +
               '<div class="ip-sk ip-sk--orb"></div>' +
               '<div class="ip-sk-hero__body">' +
                 '<div class="ip-sk ip-sk--line ip-sk--sm" style="width:26%"></div>' +
                 '<div class="ip-sk ip-sk--title" style="width:52%"></div>' +
                 '<div class="ip-sk ip-sk--line" style="width:72%"></div>' +
                 SKELETONS.stats(4) +
               '</div>' +
             '</div>';
    }
  };

  IP.skeleton = function (kind, count) {
    var fn = SKELETONS[kind] || SKELETONS.text;
    return '<div class="ip-skeleton" aria-hidden="true">' + fn(count) + '</div>';
  };

  /* ── Small DOM helpers ────────────────────────────────────────────── */

  function resolve(target) {
    if (!target) return null;
    if (typeof target === 'string') return document.querySelector(target);
    return target;
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function isEmptyValue(data) {
    if (data == null) return true;
    if (Array.isArray(data)) return data.length === 0;
    if (typeof data === 'object') {
      if (Array.isArray(data.items)) return data.items.length === 0;
      if (Array.isArray(data.results)) return data.results.length === 0;
      return Object.keys(data).length === 0;
    }
    return false;
  }

  /* ── State views ──────────────────────────────────────────────────── */

  /**
   * Error view. One sentence of plain copy, the technical detail folded away,
   * and a Retry button that is always present — a dead end with no way
   * forward is the worst version of a failed request.
   */
  function buildErrorView(error, onRetry, opts) {
    opts = opts || {};
    var wrap = el('div', 'ip-state ip-state--error');
    wrap.setAttribute('role', 'alert');

    var icon = el('div', 'ip-state__icon ip-state__icon--error');
    icon.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>';
    wrap.appendChild(icon);

    wrap.appendChild(el('p', 'ip-state__title', opts.title || 'This did not load'));
    wrap.appendChild(el('p', 'ip-state__body',
      error && error.friendly ? error.friendly() : (error && error.message) || 'Something went wrong.'));

    var actions = el('div', 'ip-state__actions');
    var retry = el('button', 'ip-btn ip-btn--primary', 'Retry');
    retry.type = 'button';
    retry.addEventListener('click', function () {
      retry.disabled = true;
      retry.textContent = 'Retrying…';
      onRetry();
    });
    actions.appendChild(retry);

    if (opts.secondary && opts.secondary.href) {
      var link = el('a', 'ip-btn ip-btn--ghost', opts.secondary.label);
      link.href = opts.secondary.href;
      actions.appendChild(link);
    }

    var report = el('button', 'ip-btn ip-btn--text', 'Report this');
    report.type = 'button';
    report.addEventListener('click', function () {
      IP.openBugReport({
        prefill: 'This section failed to load: ' + (opts.title || document.title) +
                 '\n\nError: ' + ((error && error.message) || 'unknown'),
        auto: error
      });
    });
    actions.appendChild(report);
    wrap.appendChild(actions);

    if (error && (error.status || error.code)) {
      var det = el('details', 'ip-state__details');
      det.appendChild(el('summary', null, 'Technical detail'));
      det.appendChild(el('pre', null,
        [error.code && ('code: ' + error.code),
         error.status && ('status: ' + error.status),
         error.url && ('url: ' + error.url)].filter(Boolean).join('\n')));
      wrap.appendChild(det);
    }
    return wrap;
  }

  /**
   * Empty view. An empty region is a chance to tell the user what to do next,
   * so `empty.action` is strongly encouraged at every call site.
   */
  function buildEmptyView(cfg) {
    cfg = cfg || {};
    var wrap = el('div', 'ip-state ip-state--empty');

    var icon = el('div', 'ip-state__icon');
    icon.innerHTML = cfg.icon ||
      '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
    wrap.appendChild(icon);

    wrap.appendChild(el('p', 'ip-state__title', cfg.title || 'Nothing here yet'));
    if (cfg.body) wrap.appendChild(el('p', 'ip-state__body', cfg.body));

    if (cfg.action) {
      var actions = el('div', 'ip-state__actions');
      var node;
      if (cfg.action.href) {
        node = el('a', 'ip-btn ip-btn--primary', cfg.action.label);
        node.href = cfg.action.href;
      } else {
        node = el('button', 'ip-btn ip-btn--primary', cfg.action.label);
        node.type = 'button';
        node.addEventListener('click', cfg.action.onClick);
      }
      actions.appendChild(node);
      wrap.appendChild(actions);
    }
    return wrap;
  }

  /* ── AsyncBlock ───────────────────────────────────────────────────── */

  /**
   * Owns one async region of the page.
   *
   *   IP.block('#petBody', {
   *     load:     () => IP.get('/api/pet/state'),
   *     render:   (data, host) => { ... },
   *     skeleton: 'hero',
   *     cacheKey: 'pet:state',   // paints instantly from cache, then refreshes
   *     empty:    { title: 'No pet yet', action: {label:'Hatch one', onClick} }
   *   });
   *
   * When `cacheKey` is set the block paints cached data immediately and
   * revalidates in the background, so a returning user sees content before
   * the network answers.
   */
  function AsyncBlock(target, cfg) {
    this.host = resolve(target);
    this.cfg  = cfg || {};
    this.state = 'idle';
    this.data = null;
    this.error = null;
    this.controller = null;
    this.generation = 0;

    if (!this.host) {
      console.warn('[ip-async] block target not found:', target);
      return;
    }
    this.host.classList.add('ip-block');
    if (this.cfg.auto !== false) this.load();
  }

  AsyncBlock.prototype._setState = function (state) {
    this.state = state;
    if (!this.host) return;
    this.host.dataset.ipState = state;
    this.host.setAttribute('aria-busy', state === 'loading' ? 'true' : 'false');
    if (this.cfg.onState) {
      try { this.cfg.onState(state, this); } catch (e) { IP.report(e, { kind: 'block.onState' }); }
    }
  };

  AsyncBlock.prototype._paintSkeleton = function () {
    var sk = this.cfg.skeleton;
    if (sk === false) return;
    this.host.innerHTML = typeof sk === 'function'
      ? sk()
      : IP.skeleton(sk || 'text', this.cfg.skeletonCount);
  };

  /**
   * Fetch and render. `opts.silent` keeps the current content on screen and
   * only swaps it once fresh data arrives — used for background refresh so
   * the page does not flash a skeleton over content the user is reading.
   */
  AsyncBlock.prototype.load = function (opts) {
    opts = opts || {};
    var self = this;
    var gen = ++this.generation;

    if (this.controller) this.controller.abort();
    this.controller = new AbortController();

    // Serve cache first so the region is never blank on a return visit.
    var servedCache = false;
    if (this.cfg.cacheKey && !opts.skipCache && this.state === 'idle') {
      var hit = IP.store.get(this.cfg.cacheKey, { allowStale: true });
      if (hit && !isEmptyValue(hit.value)) {
        servedCache = true;
        this._renderData(hit.value, { fromCache: true });
        this._setState('refreshing');
      }
    }

    if (!servedCache && !opts.silent) {
      this._setState('loading');
      this._paintSkeleton();
    }

    return Promise.resolve()
      .then(function () { return self.cfg.load({ signal: self.controller.signal, block: self }); })
      .then(function (data) {
        if (gen !== self.generation) return;          // a newer load superseded us
        if (self.cfg.cacheKey) {
          IP.store.set(self.cfg.cacheKey, data, self.cfg.cacheTtl || 5 * 60 * 1000);
        }
        self._renderData(data, {});
      })
      .catch(function (err) {
        if (gen !== self.generation) return;
        if (err && err.code === 'abort') return;
        self.error = err;

        // A background refresh that fails should not destroy readable content.
        if (servedCache || (opts.silent && self.data)) {
          self._setState('ready');
          IP.toast('Could not refresh. Showing your last saved copy.', 'error');
          return;
        }
        self._setState('error');
        self.host.innerHTML = '';
        self.host.appendChild(buildErrorView(err, function () { self.load({ skipCache: true }); }, {
          title: self.cfg.errorTitle,
          secondary: self.cfg.errorSecondary
        }));
        IP.report(err, { kind: 'block.load', extra: { target: self.host.id || '' } });
      });
  };

  AsyncBlock.prototype._renderData = function (data, meta) {
    this.data = data;
    this.error = null;

    var empty = this.cfg.isEmpty ? this.cfg.isEmpty(data) : isEmptyValue(data);
    if (empty) {
      this._setState('empty');
      this.host.innerHTML = '';
      this.host.appendChild(buildEmptyView(this.cfg.empty));
      return;
    }

    this._setState('ready');
    try {
      this.host.innerHTML = '';
      this.cfg.render(data, this.host, { fromCache: !!meta.fromCache, block: this });
    } catch (e) {
      this._setState('error');
      this.host.innerHTML = '';
      var self = this;
      this.host.appendChild(buildErrorView(
        new IP.ApiError('This section could not be displayed.'),
        function () { self.load({ skipCache: true }); },
        { title: 'Display problem' }
      ));
      IP.report(e, { kind: 'block.render', extra: { target: this.host.id || '' } });
    }
  };

  /** Swap in data the caller already has (e.g. after a successful write). */
  AsyncBlock.prototype.setData = function (data) {
    this.generation++;                                 // cancel any in-flight load
    if (this.cfg.cacheKey) IP.store.set(this.cfg.cacheKey, data, this.cfg.cacheTtl || 5 * 60 * 1000);
    this._renderData(data, {});
  };

  AsyncBlock.prototype.reload = function () { return this.load({ skipCache: true }); };
  AsyncBlock.prototype.refresh = function () { return this.load({ silent: true, skipCache: true }); };
  AsyncBlock.prototype.destroy = function () {
    this.generation++;
    if (this.controller) this.controller.abort();
  };

  IP.block = function (target, cfg) { return new AsyncBlock(target, cfg); };
  IP.AsyncBlock = AsyncBlock;

  /* ── Optimistic mutation ──────────────────────────────────────────── */

  /**
   * Apply a change to the DOM immediately, send it, and put the old value
   * back if the server rejects it. The UI answers in ~0ms instead of waiting
   * out a round trip.
   *
   *   IP.optimistic({
   *     apply:    () => nameEl.textContent = next,
   *     rollback: () => nameEl.textContent = prev,
   *     commit:   () => IP.post('/api/pet/rename', {name: next}),
   *     label:    'name',
   *     queueOnOffline: { url:'/api/pet/rename', body:{name: next}, dedupeKey:'pet:name' }
   *   });
   *
   * When offline and `queueOnOffline` is given, the change stays applied and
   * the write is parked for replay rather than being rolled back — losing a
   * user's edit because their train went into a tunnel is worse than
   * showing it as pending.
   */
  IP.optimistic = function (cfg) {
    var label = cfg.label || 'change';

    try { cfg.apply(); }
    catch (e) { IP.report(e, { kind: 'optimistic.apply' }); return Promise.reject(e); }

    if (navigator.onLine === false && cfg.queueOnOffline) {
      IP.queue.push(cfg.queueOnOffline);
      IP.toast('Saved offline. This will sync when you reconnect.', 'info');
      return Promise.resolve({ queued: true });
    }

    return cfg.commit().then(function (res) {
      if (cfg.onSuccess) cfg.onSuccess(res);
      return res;
    }, function (err) {
      if (err && err.retriable && cfg.queueOnOffline) {
        IP.queue.push(cfg.queueOnOffline);
        IP.toast('Could not reach the server. Your ' + label + ' will sync automatically.', 'info');
        return { queued: true };
      }
      try { cfg.rollback(); }
      catch (e) { IP.report(e, { kind: 'optimistic.rollback' }); }

      var msg = err && err.friendly ? err.friendly() : 'Could not save your ' + label + '.';
      if (cfg.onError) cfg.onError(err);
      else IP.toast(msg, 'error', { action: 'Retry', onAction: function () { IP.optimistic(cfg); } });
      throw err;
    });
  };

  /* ── Pagination ───────────────────────────────────────────────────── */

  /**
   * Drives a paged list. Handles the first page (skeleton), subsequent pages
   * (inline spinner on the button, existing rows stay put), the empty case,
   * and a page-level failure that keeps already-loaded rows on screen.
   *
   *   IP.paginate({
   *     list: '#feedbackList',
   *     fetchPage: (page) => IP.get('/api/feedback/mine?page=' + page),
   *     renderItem: (item) => nodeFor(item),
   *     mode: 'infinite'
   *   });
   *
   * `fetchPage` must resolve to {items, has_more, next_cursor?}.
   */
  function Paginator(cfg) {
    this.cfg      = cfg;
    this.list     = resolve(cfg.list);
    this.page     = 0;
    this.cursor   = null;
    this.hasMore  = true;
    this.loading  = false;
    this.count    = 0;

    if (!this.list) {
      console.warn('[ip-async] paginate list not found:', cfg.list);
      return;
    }
    this.list.classList.add('ip-paged');
    this._buildFooter();
    if (cfg.auto !== false) this.next();
  }

  Paginator.prototype._buildFooter = function () {
    var self = this;
    this.footer = el('div', 'ip-page-footer');

    this.moreBtn = el('button', 'ip-btn ip-btn--ghost ip-page-more', 'Load more');
    this.moreBtn.type = 'button';
    this.moreBtn.addEventListener('click', function () { self.next(); });
    this.footer.appendChild(this.moreBtn);

    this.status = el('p', 'ip-page-status');
    this.footer.appendChild(this.status);

    (this.cfg.footerHost ? resolve(this.cfg.footerHost) : this.list.parentNode)
      .appendChild(this.footer);

    if (this.cfg.mode === 'infinite' && 'IntersectionObserver' in window) {
      this.moreBtn.classList.add('ip-page-more--auto');
      this.observer = new IntersectionObserver(function (entries) {
        if (entries[0].isIntersecting && !self.loading && self.hasMore) self.next();
      }, { rootMargin: '300px' });
      this.observer.observe(this.footer);
    }
  };

  Paginator.prototype.next = function () {
    if (this.loading || !this.hasMore) return Promise.resolve();
    var self = this;
    var first = this.page === 0;
    this.loading = true;

    if (first && this.cfg.skeleton !== false) {
      this.list.innerHTML = IP.skeleton(this.cfg.skeleton || 'list', this.cfg.pageSize || 5);
    }
    this.moreBtn.disabled = true;
    /* Shimmer the label while the request is in flight (.ipm-shimmer,
       ip-motion.css). The button is disabled and otherwise unchanged, so
       without this there is no signal that anything is happening — and a
       spinner is heavier than this wait deserves.
       The class goes on an inner <span>, not on the button: the sitewide
       glass rule sets `background: … !important` on <button>, which would
       beat the shimmer's gradient and leave transparent text on a glass
       fill — an invisible label. Cleared in every settle path below. */
    this.moreBtn.textContent = '';
    var label = document.createElement('span');
    label.className = 'ipm-shimmer';
    label.textContent = first ? 'Loading…' : 'Loading more…';
    this.moreBtn.appendChild(label);
    this.footer.hidden = false;

    return Promise.resolve(this.cfg.fetchPage(this.page + 1, this.cursor))
      .then(function (res) {
        var items = (res && (res.items || res.results)) || [];
        if (first) self.list.innerHTML = '';

        items.forEach(function (item, i) {
          var node = self.cfg.renderItem(item, self.count + i);
          if (node) self.list.appendChild(node);
        });

        self.count  += items.length;
        self.page   += 1;
        self.cursor  = res && res.next_cursor != null ? res.next_cursor : null;
        self.hasMore = !!(res && res.has_more);
        self.loading = false;

        if (self.count === 0) {
          self.list.appendChild(buildEmptyView(self.cfg.empty));
          self.footer.hidden = true;
          /* Footer is hidden either way, but do not leave a shimmering
             label behind in case something later unhides it. */
          self.moreBtn.textContent = 'Load more';
          if (self.observer) self.observer.disconnect();
          return;
        }

        self.moreBtn.disabled = false;
        // Replaces the shimmering <span> wholesale.
        self.moreBtn.textContent = 'Load more';
        self.moreBtn.hidden = !self.hasMore;

        // `unit` lets a list say what it is counting — "day", "lesson" —
        // instead of every list reporting generic "items".
        var one   = self.cfg.unit || 'item';
        var many  = self.cfg.unitPlural || (one + 's');
        var total = res && res.total;
        // Only trust `total` if it is at least what we have already shown;
        // a smaller number means the endpoint is reporting something other
        // than an overall count, and "showing 4 of 2" helps nobody.
        var trustTotal = typeof total === 'number' && total >= self.count;
        self.status.textContent = self.hasMore
          ? (trustTotal ? 'Showing ' + self.count + ' of ' + total
                        : 'Showing ' + self.count)
          : (self.count === 1 ? '1 ' + one : 'All ' + self.count + ' ' + many);

        if (!self.hasMore && self.observer) self.observer.disconnect();
        if (self.cfg.onPage) self.cfg.onPage(res, self);
      })
      .catch(function (err) {
        self.loading = false;
        self.moreBtn.disabled = false;
        // Replaces the shimmering <span> wholesale.
        self.moreBtn.textContent = 'Load more';

        if (first) {
          self.list.innerHTML = '';
          self.list.appendChild(buildErrorView(err, function () { self.next(); }, {
            title: self.cfg.errorTitle || 'This list did not load'
          }));
          self.footer.hidden = true;
        } else {
          // Keep the rows already on screen; only the new page failed.
          self.status.textContent = (err.friendly ? err.friendly() : 'Could not load more.');
          self.status.classList.add('ip-page-status--error');
        }
        IP.report(err, { kind: 'paginate', extra: { list: self.cfg.list } });
      });
  };

  Paginator.prototype.reset = function () {
    this.page = 0; this.cursor = null; this.hasMore = true; this.count = 0;
    this.status.classList.remove('ip-page-status--error');
    return this.next();
  };

  IP.paginate = function (cfg) { return new Paginator(cfg); };

  /* ── Error boundary ───────────────────────────────────────────────── */

  /**
   * Runs `fn` and, if it throws, replaces `target` with a contained error
   * card instead of letting one broken widget take down the whole page.
   * The nearest thing vanilla JS has to a React error boundary.
   */
  IP.boundary = function (target, fn, opts) {
    opts = opts || {};
    var host = resolve(target);
    try {
      return fn(host);
    } catch (e) {
      IP.report(e, { kind: 'boundary', extra: { target: opts.name || '' } });
      if (host) {
        host.innerHTML = '';
        host.appendChild(buildErrorView(
          new IP.ApiError(opts.message || 'This part of the page stopped working.'),
          function () { location.reload(); },
          { title: opts.title || 'Something broke here' }
        ));
      }
      return null;
    }
  };

  /** Wrap an event handler so a throw inside it is reported, not swallowed. */
  IP.guard = function (fn, name) {
    return function () {
      try { return fn.apply(this, arguments); }
      catch (e) {
        IP.report(e, { kind: 'handler', extra: { name: name || fn.name || '' } });
        IP.toast('That action did not work. It has been reported.', 'error');
      }
    };
  };

  /**
   * Marks every element carrying `data-ip-boundary` so a crash inside one
   * does not leave a blank hole with no explanation. Called on DOM ready.
   */
  IP.sealBoundaries = function (root) {
    var nodes = (root || document).querySelectorAll('[data-ip-boundary]');
    Array.prototype.forEach.call(nodes, function (node) {
      node.classList.add('ip-boundary');
    });
  };

  if (document.readyState !== 'loading') IP.sealBoundaries();
  else document.addEventListener('DOMContentLoaded', function () { IP.sealBoundaries(); });

})(window, document);
