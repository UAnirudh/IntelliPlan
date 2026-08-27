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
 *   2. An optional camera check-in, entirely on-device. Frames never leave
 *      the machine, nothing is recorded, nothing is uploaded, and no
 *      identity information is derived. Two methods, depending on browser:
 *        - FaceDetector where it exists: "is there a face, roughly pointed
 *          this way". Rare in practice — absent in Firefox and Safari,
 *          flagged off in Chrome. Requiring it left the whole feature dead
 *          for almost everyone who tried to turn it on.
 *        - Frame-to-frame movement everywhere else. This can confirm that
 *          somebody is there; it cannot prove nobody is, because a still
 *          reader and an empty chair produce identical frames. So it
 *          reports presence and stays quiet otherwise, and its real job is
 *          to stop the idle-input heuristic deciding you left while you sat
 *          reading.
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
 * confidence. No frames, no landmarks, no descriptors. The capture canvas is
 * 160px wide.
 *
 * The capture element is created detached and is never added to the page by
 * this module. A student can opt into a self-view, which attaches that same
 * stream to a <video> they control — deliberately the same stream, not a
 * second capture, so what they see is exactly the input being examined.
 * That turns "frames stay on this device" from a paragraph they have to
 * believe into something they can watch.
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
  //: Motion fallback. A pixel counts as changed only above sensor noise,
  //: and enough of the frame must change before movement is called real —
  //: otherwise a flickering light or auto-exposure reads as a person.
  var PIXEL_NOISE_FLOOR = 12;        // 0-255 luma difference
  var MOTION_PRESENT_RATIO = 0.02;   // 2% of sampled pixels
  var DARK_FRAME_LUMA = 18;          // covered lens or closed lid

  //: The monitor currently running, if any. One camera, one stream: a
  //: preview must show the frames being examined, not a second capture.
  var _activeCamera = null;

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
   * Camera check-in, on-device either way: native FaceDetector when the
   * browser has it, frame-to-frame movement when it does not.
   *
   * ``start()`` resolves false when there is no camera or access is refused,
   * and the caller must surface which of those happened rather than
   * silently substituting something else. The active method is reported too
   * — telling someone "face detection" when it is watching for movement
   * would be the same class of lie.
   */
  function CameraMonitor() {
    this.stream = null;
    this.video = null;
    this.canvas = null;
    this.ctx = null;
    this.detector = null;
    this.prevFrame = null;
    /* Which of the two methods below is actually running. FaceDetector is
       the better signal but has effectively never shipped: it is absent in
       Firefox and Safari and flagged off in Chrome, so requiring it meant
       the camera check-in was permanently unavailable for almost everyone
       who turned it on. Motion is the fallback that works everywhere. */
    this.mode = typeof window.FaceDetector === 'function' ? 'face' : 'motion';
    this.available = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  }

  CameraMonitor.prototype.start = function () {
    var self = this;
    if (!this.available) return Promise.resolve(false);
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
        if (self.mode === 'face') {
          self.detector = new window.FaceDetector({ fastMode: true, maxDetectedFaces: 1 });
        }
        _activeCamera = self;
        return self.video.play().then(function () { return true; });
      })
      .catch(function () {
        self.stop();
        return false;
      });
  };

  CameraMonitor.prototype.sample = function () {
    var self = this;
    if (!this.video || this.video.readyState < 2) {
      return Promise.resolve({ state: STATE.UNKNOWN, confidence: 0, source: 'camera' });
    }
    try {
      this.ctx.drawImage(this.video, 0, 0, this.canvas.width, this.canvas.height);
    } catch (err) {
      return Promise.resolve({ state: STATE.UNKNOWN, confidence: 0, source: 'camera' });
    }

    if (this.mode === 'motion') return Promise.resolve(this._sampleMotion());

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
   * Presence from frame-to-frame movement.
   *
   * The honest boundary of this method: movement in front of the lens is
   * good evidence somebody is there, but stillness is NOT evidence that
   * nobody is. An empty desk and a student reading without shifting produce
   * the same near-identical frames. So this reports PRESENT on motion and
   * UNKNOWN on stillness — it never claims absence, and the fusion step
   * discards UNKNOWN rather than counting it against the student.
   *
   * That sounds like a weak signal, and as an absence detector it is. Its
   * actual job is the opposite one, and it does that well: it stops the
   * idle-input heuristic concluding you left when you have been sitting
   * still reading for ten minutes.
   *
   * A very dark frame is the one absence-ish case worth reporting, and even
   * then only at low confidence: a closed lid or covered lens is more often
   * "stopped studying" than "camera fault", but it is a guess either way.
   */
  CameraMonitor.prototype._sampleMotion = function () {
    var w = this.canvas.width;
    var h = this.canvas.height;
    var frame;
    try {
      frame = this.ctx.getImageData(0, 0, w, h).data;
    } catch (err) {
      // A tainted canvas cannot be read. Nothing useful to say.
      return { state: STATE.UNKNOWN, confidence: 0, source: 'camera' };
    }

    // Luminance only, sampled every 4th pixel: enough for a movement
    // signal, a quarter of the work, and it never reconstructs an image.
    var step = 16;                       // 4 pixels * 4 channels
    var luma = new Uint8Array(Math.ceil(frame.length / step));
    var total = 0;
    var n = 0;
    for (var i = 0; i < frame.length; i += step) {
      var value = (frame[i] * 0.299 + frame[i + 1] * 0.587 + frame[i + 2] * 0.114) | 0;
      luma[n++] = value;
      total += value;
    }
    var brightness = total / n;

    var previous = this.prevFrame;
    this.prevFrame = luma;

    if (brightness < DARK_FRAME_LUMA) {
      return { state: STATE.ABSENT, confidence: 0.4, source: 'camera' };
    }
    if (!previous || previous.length !== luma.length) {
      return { state: STATE.UNKNOWN, confidence: 0, source: 'camera' };
    }

    var changed = 0;
    for (var j = 0; j < n; j++) {
      if (Math.abs(luma[j] - previous[j]) > PIXEL_NOISE_FLOOR) changed++;
    }
    var ratio = changed / n;

    if (ratio >= MOTION_PRESENT_RATIO) {
      // Scale confidence with how much moved, capped: a lot of movement is
      // not proportionally stronger evidence of studying.
      var confidence = Math.min(0.75, 0.5 + ratio);
      return { state: STATE.FOCUSED, confidence: confidence, source: 'camera' };
    }
    return { state: STATE.UNKNOWN, confidence: 0, source: 'camera' };
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

  /**
   * Show the student the frames being examined.
   *
   * The camera element is created detached and never appended, which is what
   * keeps anything else on the page from reading it. That also meant nobody
   * could see what the check-in was looking at — they were asked to trust a
   * paragraph. Attaching the *same* stream to a preview they control is the
   * one way to make "frames stay on this device" checkable rather than
   * asserted: what they see is the input, and there is no second capture.
   *
   * Returns false when there is no stream to show yet.
   */
  CameraMonitor.prototype.attachPreview = function (element) {
    if (!element || !this.stream) return false;
    element.srcObject = this.stream;
    element.muted = true;
    element.playsInline = true;
    var playing = element.play();
    if (playing && playing.catch) playing.catch(function () {});
    return true;
  };

  CameraMonitor.prototype.detachPreview = function (element) {
    if (element) element.srcObject = null;
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
    // Drop the reference frame too, or a resumed session compares the first
    // new frame against a scene from before the break and reads as motion.
    this.prevFrame = null;
    if (_activeCamera === this) _activeCamera = null;
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
          message: 'This browser will not give any site camera access, so the check-in is off. Presence tracking still works.'
        });
      } else {
        var mode = this.camera.mode;
        cameraReady = this.camera.start().then(function (ok) {
          self.onCameraStatus(
            ok
              ? {
                  ok: true,
                  mode: mode,
                  // Name the method rather than implying one. Motion knows
                  // someone moved; it does not know it was a face.
                  message: mode === 'face'
                    ? 'Camera check-in on, using face detection. Frames stay on this device.'
                    : 'Camera check-in on, watching for movement. Frames stay on this device.'
                }
              : {
                  ok: false,
                  reason: 'denied',
                  message: 'No camera access, so the check-in is off. Presence tracking still works.'
                }
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
    // Whether the toggle can do anything at all. This asked for
    // FaceDetector, an API that never shipped in Firefox or Safari and is
    // flagged off in Chrome — so the switch reported itself unsupported for
    // very nearly everyone who tried to turn it on. What the check-in
    // actually needs is a camera.
    // Exposed so the motion classifier can be exercised directly. It is the
    // one piece here with real arithmetic in it, and it decides whether the
    // camera says anything at all.
    CameraMonitor: CameraMonitor,
    // The running monitor, so the page can attach a self-view to the same
    // stream the detector reads. Null when the camera is not on.
    activeCamera: function () { return _activeCamera; },
    cameraSupported: function () {
      return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
    },
    // Which method would run: 'face' where the browser has on-device face
    // detection, otherwise 'motion'.
    cameraMode: function () {
      return typeof window.FaceDetector === 'function' ? 'face' : 'motion';
    }
  };
})(window, document);
