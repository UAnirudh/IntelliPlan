/* A private, video-only peer connection. No public STUN/TURN or media server. */
(function (window) {
  'use strict';
  var BASE = '/api/active/camera-pairs';
  function error(message, name) { var e = new Error(message); e.name = name || 'CameraPairError'; return e; }
  function stopTracks(stream) {
    if (stream) stream.getTracks().forEach(function (t) { t.onended = null; t.stop(); });
  }
  function Peer(options) {
    this.options = options || {};
    this.role = this.options.role || 'receiver';
    this.id = this.options.id;
    this.key = this.options.key;
    this.alive = false;
    this.timer = null;
    this.controllers = new Set();
  }
  Peer.prototype._request = function (suffix, data, key, keepalive) {
    var controller = new AbortController();
    var self = this;
    this.controllers.add(controller);
    var timeout = window.setTimeout(function () { controller.abort(); }, 8000);
    return fetch(BASE + (this.id ? '/' + this.id : '') + suffix, {
      method: 'POST', credentials: 'same-origin', cache: 'no-store', keepalive: !!keepalive,
      headers: { 'Content-Type': 'application/json', 'X-Camera-Key': key || this.key || '' },
      body: JSON.stringify(data), signal: controller.signal
    }).then(function (response) {
      return response.json().then(function (body) {
        if (!response.ok) throw error(body.error || 'Camera connection unavailable.');
        return body;
      });
    }).finally(function () {
      window.clearTimeout(timeout);
      self.controllers.delete(controller);
    });
  };
  Peer.prototype._current = function () {
    if (!this.alive) throw error('Camera sharing stopped.', 'AbortError');
  };
  Peer.prototype._gather = function () {
    var self = this;
    return new Promise(function (resolve, reject) {
      var pc = self.pc;
      var timeout = window.setTimeout(function () { finish(error('Could not connect. Keep both devices on the same Wi-Fi.')); }, 10000);
      function finish(failure) {
        pc.removeEventListener('icegatheringstatechange', changed);
        window.clearTimeout(timeout);
        if (failure) reject(failure); else resolve();
      }
      function changed() {
        if (!self.alive) finish(error('Camera sharing stopped.', 'AbortError'));
        else if (pc.iceGatheringState === 'complete') finish();
      }
      pc.addEventListener('icegatheringstatechange', changed);
      changed();
    });
  };
  Peer.prototype.start = function () {
    var self = this;
    if (this.alive) return Promise.reject(error('This camera is already connecting.'));
    if (typeof window.RTCPeerConnection !== 'function') return Promise.reject(error('This browser cannot pair a camera.'));
    this.alive = true;
    this.started = Date.now();
    this.ready = new Promise(function (resolve, reject) { self.resolve = resolve; self.reject = reject; });
    this._setup().then(function () { self._poll(); }).catch(function (e) { self._fail(e); });
    return this.ready;
  };
  Peer.prototype._setup = async function () {
    var self = this;
    if (this.role === 'receiver') {
      var created = await this._request('', { share_video: true });
      this.id = created.id;
      this.key = created.receiver_key;
      if (!this.alive) { this._revoke(); this._current(); }
      if (this.options.onInvite) this.options.onInvite(
        window.location.origin + '/study-camera#' + created.id + '.' + created.invite_key);
    } else {
      var claimed = await this._request('/claim', { share_video: true });
      this.key = claimed.sender_key;
      if (!this.alive) { this._revoke(); this._current(); }
    }
    this._current();
    this.pc = new window.RTCPeerConnection({ iceServers: [] });
    this.pc.ontrack = function (evt) {
      if (!self.alive) { evt.track.stop(); return; }
      if (evt.track.kind !== 'video') { evt.track.stop(); self._fail(error('Only video cameras are supported.')); return; }
      self.stream = evt.streams[0] || new MediaStream([evt.track]);
      evt.track.onended = function () { self._fail(error('The phone camera disconnected.')); };
      self._ready();
    };
    this.pc.onconnectionstatechange = function () {
      if (!self.alive) return;
      if (self.pc.connectionState === 'connected') { self.connected = true; self._ready(); }
      else if (['failed', 'closed', 'disconnected'].indexOf(self.pc.connectionState) !== -1) {
        self._fail(error('Camera disconnected. Start a new connection to try again.'));
      }
    };
    if (this.role === 'receiver') {
      this.pc.addTransceiver('video', { direction: 'recvonly' });
      await this.pc.setLocalDescription(await this.pc.createOffer());
      await this._gather(); this._current();
      this.description = { type: 'offer', sdp: this.pc.localDescription.sdp };
    } else {
      var stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 },
          facingMode: { ideal: this.options.facingMode || 'environment' } }, audio: false
      });
      if (!this.alive) { stopTracks(stream); this._current(); }
      this.stream = stream;
      stream.getVideoTracks().forEach(function (track) {
        track.onended = function () { self._fail(error('Camera sharing stopped.')); };
        self.pc.addTrack(track, stream);
      });
      if (this.options.onPreview) this.options.onPreview(stream);
    }
  };
  Peer.prototype._ready = function () {
    if (this.alive && this.connected && this.stream && this.resolve) {
      this.resolve(this.stream); this.resolve = this.reject = null;
    }
  };
  Peer.prototype._poll = async function () {
    var self = this;
    try {
      this._current();
      if (!this.connected && Date.now() - this.started >= 120000) throw error('The camera invitation expired. Start a new connection.');
      var payload = { role: this.role, connected: !!this.connected };
      if (this.description) payload.description = this.description;
      var result = await this._request('/sync', payload);
      this.description = null;
      this._current();
      if (result.description && !this.pc.remoteDescription) {
        await this.pc.setRemoteDescription(result.description); this._current();
        if (this.role === 'sender') {
          await this.pc.setLocalDescription(await this.pc.createAnswer());
          await this._gather(); this._current();
          this.description = { type: 'answer', sdp: this.pc.localDescription.sdp };
        }
      }
      this.timer = window.setTimeout(function () { self._poll(); }, this.description ? 0 : 2000);
    } catch (e) { this._fail(e); }
  };
  Peer.prototype._revoke = function () {
    if (!this.id || !this.key) return;
    this._request('/stop', { role: this.role }, this.key, true).catch(function () {});
  };
  Peer.prototype._fail = function (failure) {
    if (!this.alive) return;
    var onEnded = this.options.onEnded;
    var reject = this.reject;
    this.reject = null;
    this.stop();
    if (reject) reject(failure);
    if (onEnded) onEnded(failure);
  };
  Peer.prototype.stop = function () {
    this.alive = false;
    window.clearTimeout(this.timer);
    this.controllers.forEach(function (controller) { controller.abort(); });
    this.controllers.clear();
    if (this.pc) {
      this.pc.ontrack = this.pc.onconnectionstatechange = null;
      this.pc.close(); this.pc = null;
    }
    stopTracks(this.stream); this.stream = null;
    if (this.options.onInvite) this.options.onInvite(null);
    this._revoke();
    if (this.reject) this.reject(error('Camera sharing stopped.', 'AbortError'));
    this.reject = this.resolve = null;
  };
  window.IPStudyCamera = { Peer: Peer };
})(window);
