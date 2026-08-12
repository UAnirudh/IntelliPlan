/*
 * ip-focus.js — optional, on-device attention estimation for Active study.
 *
 * WHAT THIS IS
 * ------------
 * A best-effort estimate of whether the student is present and engaged,
 * assembled from two independent sources:
 *
 *   1. Presence signals the browser gives us for free — tab visibility,
 *      window focus, and input idleness. Always available, cheap, exact
 *      about what they measure, and worth more than people expect.
 *   2. An optional camera check-in using the browser's built-in, on-device
 *      FaceDetector. Frames never leave the machine. Nothing is recorded,
 *      nothing is uploaded, and no identity information is derived — we ask
 *      only "is there a face, and is it roughly pointed this way".
 *
 * WHAT THIS IS NOT
 * ----------------
 * It is not accurate attention measurement, and the UI must never present it
 * as such. A student reading a textbook beside the laptop is "absent". A
 * student staring blankly at the screen is "focused". Those errors are
 * inherent to the approach, not bugs to be tuned away.
 *
 * Because of that, every decision here is deliberately sluggish:
 *
 *   - Samples are taken every ~2s, not every frame.
 *   - A raw sample never changes anything on its own. State changes require
 *     a run of agreeing samples (hysteresis), so one glance at your notes
 *     cannot trigger anything.
 *   - Low-confidence detections are discarded rather than guessed at.
 *   - Nudges are rate-limited on top of all of that.
 *
 * The cost of a false "you look distracted" is that the student stops
 * trusting the feature and turns it off, so the whole design is biased
 * toward silence.
 *
 * PRIVACY
 * -------
 * The only thing that ever crosses the network is a per-minute integer
 * summary: how many samples looked present, away, or absent, plus a mean
 * confidence. No frames, no landmarks, no descriptors. The <video> element
 * is never attached to the document, and the capture canvas is 160px wide.
 */
(function (window, document) {
  'use strict';

  // ── Tuning ─────────────────────────────────────────────────────────
  var SAMPLE_INTERVAL_MS = 2000;     // how often we look
  var CAPTURE_WIDTH = 160;           // downscaled frame width for detection
  var BUCKET_MS = 60000;             // aggregation window sent to the server

  // A state must win this many consecutive samples before we believe it.
  // ~5 samples ≈ 10 seconds of agreement.
  var HYSTERESIS_SAMPLES = 5;

  // Below this detector confidence we record "unknown" rather than guess.
  var MIN_CONFIDENCE = 0.45;

  // Idle input for this long, with the tab visible, reads as "away".
  var IDLE_MS = 120000;

  // Never nudge more often than this, whatever the detector says.
  var MIN_NUDGE_GAP_MS = 300000;     // 5 minutes

  // A face this far off-centre or this small is probably not looking here.
  var MAX_OFFSET_RATIO = 0.34;
  var MIN_FACE_AREA_RATIO = 0.012;

  var STATE = { FOCUSED: 'present', AWAY: 'away', ABSENT: 'absent', UNKNOWN: 'unknown' };

  function now() { return Date.now(); }

  function clamp(value, lo, hi) { return Math.max(lo, Math.min(hi, value)); }

  /**
   * Presence from browser signals alone. Always runs, camera or not.
   * Measures exactly what it claims: is this tab in front, and has the
   * student touched anything recently.
   */
  function PresenceMonitor() {
    this.lastInput = now();
    this.visible = !document.hidden;
    this.focused = document.hasFocus();
    this._bound = [];
    var self = this;

    function mark() { self.lastInput = now(); }
    ['keydown', 'pointerdown', 'pointermove', 'wheel', 'touchstart'].forEach(function (evt) {
      document.addEventListener(evt, mark, { passive: true });
      self._bound.push([document, evt, mark]);
    });

    function onVisibility() { self.visible = !document.hidden; }
    document.addEventListener('visibilitychange', onVisibility);
    this._bound.push([document, 'visibilitychange', onVisibility]);

    function onFocus() { self.focused = true; }
    function onBlur() { self.focused = false; }
    window.addEventListener('focus', onFocus);
    window.addEventListener('blur', onBlur);
    this._bound.push([window, 'focus', onFocus], [window, 'blur', onBlur]);
  }

  PresenceMonitor.prototype.sample = function () {
    if (!this.visible) return { state: STATE.ABSENT, confidence: 0.9, source: 'visibility' };
    var idleFor = now() - this.lastInput;
    if (idleFor > IDLE_MS) {
      // Idle with the tab in front is genuinely ambiguous — reading a book
      // and having walked off look identical from here — so this is a weak
      // signal by design and the camera outranks it when present.
      return { state: STATE.AWAY, confidence: 0.4, source: 'idle' };
    }
    if (!this.focused) return { state: STATE.AWAY, confidence: 0.55, source: 'blur' };
    return { state: STATE.FOCUSED, confidence: 0.6, source: 'input' };
  };

  PresenceMonitor.prototype.stop = function () {
    this._bound.forEach(function (entry) {
      entry[0].removeEventListener(entry[1], entry[2]);
    });
    this._bound = [];
  };

  /**
   * Camera check-in via the browser's native on-device FaceDetector.
   * Returns null from start() when the API is unavailable, which the caller
   * must surface honestly rather than silently substituting something else.
   */
  function CameraMonitor() {
    this.stream = null;
    this.video = null;
    this.canvas = null;
    this.ctx = null;
    this.detector = null;
    this.available = typeof window.FaceDetector === 'function';
  }

  CameraMonitor.prototype.start = function () {
    var self = this;
    if (!this.available) return Promise.resolve(false);
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      this.available = false;
      return Promise.resolve(false);
    }
    return navigator.mediaDevices
      .getUserMedia({ video: { width: 320, height: 240, facingMode: 'user' }, audio: false })
      .then(function (stream) {
        self.stream = stream;
        // Deliberately detached from the DOM: there is no preview, and a
        // element that is never appended cannot be screenshotted by
        // anything else on the page.
        self.video = document.createElement('video');
        self.video.srcObject = stream;
        self.video.muted = true;
        self.video.playsInline = true;
        self.canvas = document.createElement('canvas');
        self.canvas.width = CAPTURE_WIDTH;
        self.canvas.height = Math.round(CAPTURE_WIDTH * 0.75);
        self.ctx = self.canvas.getContext('2d', { willReadFrequently: true });
        self.detector = new window.FaceDetector({ fastMode: true, maxDetectedFaces: 1 });
        return self.video.play().then(function () { return true; });
      })
      .catch(function () {
        self.stop();
        return false;
      });
  };

  CameraMonitor.prototype.sample = function () {
    var self = this;
    if (!this.detector || !this.video || this.video.readyState < 2) {
      return Promise.resolve({ state: STATE.UNKNOWN, confidence: 0, source: 'camera' });
    }
    try {
      this.ctx.drawImage(this.video, 0, 0, this.canvas.width, this.canvas.height);
    } catch (err) {
      return Promise.resolve({ state: STATE.UNKNOWN, confidence: 0, source: 'camera' });
    }
    return this.detector
      .detect(this.canvas)
      .then(function (faces) {
        if (!faces || !faces.length) {
          return { state: STATE.ABSENT, confidence: 0.7, source: 'camera' };
        }
        return self._classify(faces[0]);
      })
      .catch(function () {
        return { state: STATE.UNKNOWN, confidence: 0, source: 'camera' };
      });
  };

  /**
   * Turn one bounding box into a coarse orientation guess.
   *
   * This is intentionally crude. FaceDetector gives a box and, on some
   * platforms, eye landmarks — not a head pose. So "facing the screen" is
   * approximated by "the face is reasonably large and reasonably centred",
   * and when eye landmarks exist their horizontal balance sharpens it: a
   * head turned away puts both eyes toward one edge of the box.
   *
   * A more precise answer needs a real head-pose model. Until one ships,
   * the honest move is a low-confidence estimate the rest of the pipeline
   * treats sceptically, not a precise-looking number we invented.
   */
  CameraMonitor.prototype._classify = function (face) {
    var box = face.boundingBox || {};
    var frameW = this.canvas.width;
    var frameH = this.canvas.height;
    var area = (box.width * box.height) / (frameW * frameH);
    if (!isFinite(area) || area < MIN_FACE_AREA_RATIO) {
      return { state: STATE.ABSENT, confidence: 0.55, source: 'camera' };
    }

    var centreX = box.x + box.width / 2;
    var offset = Math.abs(centreX - frameW / 2) / frameW;
    var confidence = clamp(0.5 + area * 2, 0.4, 0.85);

    var landmarks = face.landmarks || [];
    var eyes = landmarks.filter(function (l) { return l.type === 'eye'; });
    if (eyes.length === 2) {
      var e0 = eyes[0].locations[0];
      var e1 = eyes[1].locations[0];
      var eyeMid = (e0.x + e1.x) / 2;
      // How far the eye midpoint sits from the box centre, as a share of
      // box width. A head turned away pushes it toward one side.
      var skew = Math.abs(eyeMid - centreX) / Math.max(1, box.width);
      confidence = clamp(confidence + 0.1, 0.4, 0.9);
      if (skew > 0.22) {
        return { state: STATE.AWAY, confidence: confidence, source: 'camera' };
      }
    }

    if (offset > MAX_OFFSET_RATIO) {
      return { state: STATE.AWAY, confidence: confidence, source: 'camera' };
    }
    return { state: STATE.FOCUSED, confidence: confidence, source: 'camera' };
  };

  CameraMonitor.prototype.stop = function () {
    if (this.stream) {
      this.stream.getTracks().forEach(function (t) { t.stop(); });
      this.stream = null;
    }
    if (this.video) { this.video.srcObject = null; this.video = null; }
    this.canvas = null;
    this.ctx = null;
    this.detector = null;
  };

  /**
   * The public object. Fuses the monitors, smooths the result, aggregates
   * buckets, and calls back only when something has genuinely changed.
   */
  function FocusTracker(options) {
    options = options || {};
    this.onState = options.onState || function () {};
    this.onBucket = options.onBucket || function () {};
    this.onDistraction = options.onDistraction || function () {};
    this.onCameraStatus = options.onCameraStatus || function () {};

    this.presence = null;
    this.camera = null;
    this.timer = null;
    this.startedAt = 0;

    this.state = STATE.UNKNOWN;
    this.candidate = STATE.UNKNOWN;
    this.candidateRun = 0;
    this.stateSince = 0;
    this.lastNudge = 0;

    this.bucket = null;
    this.summary = {
      samples: 0, present: 0, away: 0, absent: 0,
      distraction_events: 0, longest_streak_seconds: 0
    };
  }

  FocusTracker.prototype.start = function (useCamera) {
    var self = this;
    this.startedAt = now();
    this.stateSince = this.startedAt;
    this.presence = new PresenceMonitor();
    this._resetBucket();

    var cameraReady = Promise.resolve(false);
    if (useCamera) {
      this.camera = new CameraMonitor();
      if (!this.camera.available) {
        // Say so plainly. Quietly falling back to tab-visibility while the
        // UI still says "camera focus" is the dishonest option.
        this.onCameraStatus({
          ok: false,
          reason: 'unsupported',
          message: 'This browser has no on-device face detection, so the camera check-in is off. Presence tracking still works.'
        });
      } else {
        cameraReady = this.camera.start().then(function (ok) {
          self.onCameraStatus(
            ok
              ? { ok: true, message: 'Camera check-in on. Frames stay on this device.' }
              : { ok: false, reason: 'denied', message: 'Camera unavailable, so the check-in is off. Presence tracking still works.' }
          );
          if (!ok) { self.camera.stop(); self.camera = null; }
          return ok;
        });
      }
    }

    return cameraReady.then(function () {
      self.timer = window.setInterval(function () { self._tick(); }, SAMPLE_INTERVAL_MS);
      return true;
    });
  };

  FocusTracker.prototype._tick = function () {
    var self = this;
    var presenceSample = this.presence.sample();

    // The tab being hidden is definitive. Skip the camera entirely — most
    // browsers throttle or blank a background tab's video anyway, so
    // sampling it would produce noise, not information.
    if (presenceSample.state === STATE.ABSENT) {
      this._record(presenceSample);
      return;
    }
    if (!this.camera) { this._record(presenceSample); return; }

    this.camera.sample().then(function (cameraSample) {
      self._record(self._fuse(presenceSample, cameraSample));
    });
  };

  /**
   * Combine the two sources. The camera wins when it is confident, because
   * it observes the student rather than the software; presence wins when
   * the camera is unsure, because a weak real signal beats a strong guess.
   */
  FocusTracker.prototype._fuse = function (presenceSample, cameraSample) {
    if (cameraSample.state === STATE.UNKNOWN || cameraSample.confidence < MIN_CONFIDENCE) {
      return presenceSample;
    }
    if (presenceSample.state === STATE.FOCUSED && cameraSample.state === STATE.FOCUSED) {
      return { state: STATE.FOCUSED, confidence: Math.min(0.95, cameraSample.confidence + 0.1), source: 'fused' };
    }
    return cameraSample;
  };

  FocusTracker.prototype._record = function (sample) {
    this.summary.samples += 1;
    if (sample.state === STATE.FOCUSED) this.summary.present += 1;
    else if (sample.state === STATE.AWAY) this.summary.away += 1;
    else if (sample.state === STATE.ABSENT) this.summary.absent += 1;

    this.bucket.samples += 1;
    this.bucket.confidenceSum += sample.confidence || 0;
    if (sample.state === STATE.FOCUSED) this.bucket.present += 1;
    else if (sample.state === STATE.AWAY) this.bucket.away += 1;
    else if (sample.state === STATE.ABSENT) this.bucket.absent += 1;

    this._advanceState(sample);
    this._maybeFlushBucket();
  };

  FocusTracker.prototype._advanceState = function (sample) {
    if (sample.state === this.candidate) {
      this.candidateRun += 1;
    } else {
      this.candidate = sample.state;
      this.candidateRun = 1;
    }
    if (this.candidateRun < HYSTERESIS_SAMPLES || this.candidate === this.state) return;

    var previous = this.state;
    var heldFor = now() - this.stateSince;
    if (previous === STATE.FOCUSED) {
      this.summary.longest_streak_seconds = Math.max(
        this.summary.longest_streak_seconds, Math.round(heldFor / 1000)
      );
    }

    this.state = this.candidate;
    this.stateSince = now();
    this.onState({ state: this.state, previous: previous, source: sample.source });

    var drifted = this.state === STATE.AWAY || this.state === STATE.ABSENT;
    if (drifted && previous === STATE.FOCUSED) {
      this.summary.distraction_events += 1;
      if (now() - this.lastNudge > MIN_NUDGE_GAP_MS) {
        this.lastNudge = now();
        this.onDistraction({ state: this.state, source: sample.source });
      }
    }
  };

  FocusTracker.prototype._resetBucket = function () {
    this.bucket = {
      openedAt: now(), samples: 0, present: 0, away: 0, absent: 0, confidenceSum: 0
    };
  };

  FocusTracker.prototype._maybeFlushBucket = function (force) {
    if (!this.bucket || !this.bucket.samples) return;
    if (!force && now() - this.bucket.openedAt < BUCKET_MS) return;
    this.onBucket({
      offset_seconds: Math.round((this.bucket.openedAt - this.startedAt) / 1000),
      present: this.bucket.present,
      away: this.bucket.away,
      absent: this.bucket.absent,
      confidence: this.bucket.samples ? this.bucket.confidenceSum / this.bucket.samples : 0,
      summary: this.snapshot()
    });
    this._resetBucket();
  };

  FocusTracker.prototype.snapshot = function () {
    var live = this.state === STATE.FOCUSED ? Math.round((now() - this.stateSince) / 1000) : 0;
    return {
      samples: this.summary.samples,
      present: this.summary.present,
      away: this.summary.away,
      absent: this.summary.absent,
      distraction_events: this.summary.distraction_events,
      longest_streak_seconds: Math.max(this.summary.longest_streak_seconds, live)
    };
  };

  FocusTracker.prototype.ratio = function () {
    if (this.summary.samples < 10) return null;   // too little to mean anything
    return this.summary.present / this.summary.samples;
  };

  FocusTracker.prototype.stop = function () {
    if (this.timer) { window.clearInterval(this.timer); this.timer = null; }
    this._maybeFlushBucket(true);
    if (this.presence) { this.presence.stop(); this.presence = null; }
    if (this.camera) { this.camera.stop(); this.camera = null; }
    return this.snapshot();
  };

  window.IPFocus = {
    Tracker: FocusTracker,
    STATE: STATE,
    cameraSupported: function () { return typeof window.FaceDetector === 'function'; }
  };
})(window, document);
