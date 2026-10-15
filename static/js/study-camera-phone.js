(function () {
  'use strict';
  var invite = [];
  var peer = null;
  var button = document.getElementById('cameraConnect');
  var stop = document.getElementById('cameraStop');
  var select = document.getElementById('cameraFacing');
  var status = document.getElementById('cameraStatus');
  var preview = document.getElementById('cameraPreview');
  function reset(message) {
    if (peer) peer.stop();
    peer = null;
    preview.srcObject = null; preview.hidden = true;
    button.disabled = false; stop.hidden = true; select.disabled = false;
    status.textContent = message;
  }
  function loadInvite() {
    var fragment = window.location.hash.slice(1);
    if (!fragment) return;
    reset('Camera is off. Tap Connect to share video with your study browser.');
    invite = fragment.split('.');
    window.history.replaceState(null, '', window.location.pathname);
  }
  window.addEventListener('hashchange', loadInvite);
  loadInvite();
  button.addEventListener('click', function () {
    if (!/^[a-f0-9]{32}$/.test(invite[0] || '') || !/^[A-Za-z0-9_-]{43}$/.test(invite[1] || '')) {
      status.textContent = 'Open a new private link from Camera source on your study page.'; return;
    }
    button.disabled = true; stop.hidden = false; select.disabled = true;
    status.textContent = 'Connecting. Allow camera access when your browser asks.';
    var current = new window.IPStudyCamera.Peer({ role: 'sender', id: invite[0], key: invite[1],
      facingMode: select.value,
      onPreview: function (stream) {
        if (peer !== current) return;
        preview.srcObject = stream; preview.hidden = false;
        preview.play().catch(function () {});
      },
      onEnded: function (e) { if (peer === current) reset(e.message); }
    });
    peer = current;
    current.start().then(function () {
      if (peer === current) status.textContent = 'Sharing video with your study browser. Audio is off.';
    }).catch(function (e) { if (peer === current) reset(e.message); });
  });
  stop.addEventListener('click', function () { reset('Camera sharing is off. Start a new connection from your study page to reconnect.'); });
  window.addEventListener('pagehide', function () { reset('Camera sharing stopped when you left this page.'); });
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) reset('Camera sharing paused when this page was hidden. Start a new connection to resume.');
  });
})();
