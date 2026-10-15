/* Receive video directly from an HTTPS WHEP camera gateway, without cookies. */
(function (window) {
  'use strict';
  function fail(message) { var e = new Error(message); e.name = 'NetworkCameraError'; return e; }
  function endpoint(value) {
    var url;
    try { url = new URL(value); } catch (_) { throw fail('Enter a complete HTTPS camera gateway URL.'); }
    if (url.protocol !== 'https:' || url.username || url.password || url.hash || url.origin === window.location.origin) {
      throw fail('Use an HTTPS camera gateway URL, without embedded credentials.');
    }
    return url;
  }
  function Network(options) {
    this.options = options || {};
    this.alive = false;
    this.sessionUrl = null;
    this.timer = null;
  }
  Network.prototype._request = function (url, options) {
    var controller = new AbortController();
    var timeout = window.setTimeout(function () { controller.abort(); }, 10000);
    return fetch(url, Object.assign({ credentials: 'omit', cache: 'no-store',
      redirect: 'error', referrerPolicy: 'no-referrer', signal: controller.signal }, options))
      .finally(function () { window.clearTimeout(timeout); });
  };
  Network.prototype._delete = function () {
    if (!this.sessionUrl) return;
    var url = this.sessionUrl;
    this.sessionUrl = null;
    this._request(url, { method: 'DELETE', keepalive: true, headers: this.headers }).catch(function () {});
  };
  Network.prototype._current = function () {
    if (!this.alive) throw fail('Network camera stopped.');
  };
  Network.prototype._policy = async function () {
    var response = await this._request('/api/active/focus-permission', { credentials: 'same-origin' });
    var timer;
    var body;
    try {
      body = response.ok ? await Promise.race([response.json(), new Promise(function (_, reject) {
        timer = window.setTimeout(function () { reject(fail('Camera permissions could not be checked.')); }, 8000);
      })]) : null;
    } finally { window.clearTimeout(timer); }
    if (!body || body.allowed !== true) throw fail('Camera access is unavailable under your current settings.');
    this._current();
  };
  Network.prototype._watchPolicy = async function () {
    var self = this;
    try {
      await this._policy();
      this.timer = window.setTimeout(function () { self._watchPolicy(); }, 5000);
    } catch (e) { this._fail(e); }
  };
  Network.prototype._gather = function () {
    var self = this;
    return new Promise(function (resolve, reject) {
      var pc = self.pc;
      var timeout = window.setTimeout(function () { finish(fail('The camera could not establish a connection.')); }, 10000);
      function finish(error) {
        pc.removeEventListener('icegatheringstatechange', check);
        window.clearTimeout(timeout);
        if (error) reject(error); else resolve();
      }
      function check() {
        if (!self.alive) finish(fail('Network camera stopped.'));
        else if (pc.iceGatheringState === 'complete') finish();
      }
      pc.addEventListener('icegatheringstatechange', check);
      check();
    });
  };
  Network.prototype._readSdp = async function (response) {
    if (!response.body) throw fail('The gateway returned an empty connection response.');
    var reader = response.body.getReader();
    this.reader = reader;
    var decoder = new TextDecoder();
    var text = '';
    var bytes = 0;
    var timeout;
    var expired = new Promise(function (_, reject) {
      timeout = window.setTimeout(function () {
        reader.cancel().catch(function () {});
        reject(fail('The camera gateway response timed out.'));
      }, 10000);
    });
    try {
      while (true) {
        this._current();
        var part = await Promise.race([reader.read(), expired]);
        if (part.done) break;
        bytes += part.value.byteLength;
        if (bytes > 65536) throw fail('The camera gateway returned an oversized response.');
        text += decoder.decode(part.value, { stream: true });
      }
      return text + decoder.decode();
    } finally {
      window.clearTimeout(timeout);
      reader.cancel().catch(function () {});
      this.reader = null;
    }
  };
  Network.prototype.start = function () {
    var self = this;
    if (this.alive) return Promise.reject(fail('This camera is already connecting.'));
    this.alive = true;
    this.ready = new Promise(function (resolve, reject) { self.resolve = resolve; self.reject = reject; });
    this._setup().catch(function (e) { self._fail(e); });
    return this.ready;
  };
  Network.prototype._setup = async function () {
    var self = this;
    var url = endpoint(this.options.url || '');
    var username = this.options.username || '';
    var password = this.options.password || '';
    if (username.length > 256 || password.length > 2048 || /[\r\n]/.test(username + password)) {
      throw fail('The camera credentials are not valid.');
    }
    this.headers = {};
    if (username) {
      var bytes = new TextEncoder().encode(username + ':' + password);
      this.headers.Authorization = 'Basic ' + btoa(String.fromCharCode.apply(null, bytes));
    } else if (password) this.headers.Authorization = 'Bearer ' + password;
    await this._policy();
    if (typeof window.RTCPeerConnection !== 'function') throw fail('This browser cannot connect to a network camera.');
    this.pc = new window.RTCPeerConnection({ iceServers: [] });
    this.pc.ontrack = function (evt) {
      if (!self.alive) { evt.track.stop(); return; }
      if (evt.track.kind !== 'video') { evt.track.stop(); self._fail(fail('Only video cameras are supported.')); return; }
      self.stream = evt.streams[0] || new MediaStream([evt.track]);
      evt.track.onended = function () { self._fail(fail('The network camera disconnected.')); };
      self._ready();
    };
    this.pc.onconnectionstatechange = function () {
      if (!self.alive) return;
      if (self.pc.connectionState === 'connected') { self.connected = true; self._ready(); }
      else if (['failed', 'closed', 'disconnected'].indexOf(self.pc.connectionState) !== -1) {
        self._fail(fail('Network camera disconnected. Check the gateway and try again.'));
      }
    };
    this.pc.addTransceiver('video', { direction: 'recvonly' });
    await this.pc.setLocalDescription(await this.pc.createOffer());
    await this._gather(); this._current();
    await this._policy();
    var response = await this._request(url.href, { method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/sdp', Accept: 'application/sdp' }, this.headers),
      body: this.pc.localDescription.sdp });
    // Even a late response is inspected for its resource URL so cancellation
    // can delete the gateway session instead of reviving capture.
    if (response.status !== 201) throw fail('The camera gateway refused the connection. Check the URL and access details.');
    var location = response.headers.get('Location');
    if (!location) throw fail('The gateway must expose its WHEP Location header to this website.');
    var resource = endpoint(new URL(location, url.href).href);
    if (resource.origin !== url.origin) throw fail('The gateway returned an unexpected connection address.');
    this.sessionUrl = resource.href;
    if (!this.alive) { this._delete(); return; }
    if (!(response.headers.get('Content-Type') || '').toLowerCase().startsWith('application/sdp')) {
      throw fail('This address is not a WHEP camera endpoint.');
    }
    var size = response.headers.get('Content-Length');
    if (size && Number(size) > 65536) throw fail('The camera gateway returned an oversized response.');
    var sdp = await this._readSdp(response);
    if (sdp.length > 65536 || !sdp.startsWith('v=0') || !sdp.includes('m=video ')
        || sdp.includes('m=audio ') || sdp.includes('m=application ')) {
      throw fail('The gateway must provide a video-only WHEP stream.');
    }
    this._current();
    await this._policy();
    await this.pc.setRemoteDescription({ type: 'answer', sdp: sdp });
    this._current();
    this.deadline = window.setTimeout(function () {
      if (!self.connected) self._fail(fail('Could not receive video. Check the gateway network and camera codec.'));
    }, 15000);
    this._watchPolicy();
  };
  Network.prototype._ready = function () {
    if (this.alive && this.connected && this.stream && this.resolve) {
      this.resolve(this.stream); this.resolve = this.reject = null;
    }
  };
  Network.prototype._fail = function (e) {
    if (!this.alive) return;
    var ended = this.options.onEnded;
    var safe = fail(e.name === 'NetworkCameraError' ? e.message
      : 'Could not reach the camera. Check HTTPS, network permission, CORS and the gateway.');
    var reject = this.reject;
    this.reject = null;
    this.stop();
    if (reject) reject(safe);
    if (ended) ended(safe);
  };
  Network.prototype.stop = function () {
    this.alive = false;
    window.clearTimeout(this.timer);
    window.clearTimeout(this.deadline);
    if (this.reader) this.reader.cancel().catch(function () {});
    if (this.pc) { this.pc.ontrack = this.pc.onconnectionstatechange = null; this.pc.close(); this.pc = null; }
    if (this.stream) this.stream.getTracks().forEach(function (track) { track.onended = null; track.stop(); });
    this.stream = null;
    this._delete();
    if (this.reject) this.reject(fail('Network camera stopped.'));
    this.reject = this.resolve = null;
  };
  window.IPNetworkCamera = { Network: Network, endpoint: endpoint };
})(window);
