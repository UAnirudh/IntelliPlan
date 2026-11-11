/* Soundtrack + SFX for the daily AI-tips video. 120 BPM, A minor.
   Pure JS DSP → full.wav (music + SFX) and sfx.wav (SFX only), in the cwd.
   The timeline is fixed (hook 0–4s, 7 tips × 4s, CTA 32–36s), so the
   same score fits every day's content. */
const fs = require('fs');
const SR = 44100, LEN = 36.8, N = Math.ceil(LEN * SR);
const BEAT = 0.5, S16 = 0.125;

/* buses */
const mus = [new Float32Array(N), new Float32Array(N)];
const sfx = [new Float32Array(N), new Float32Array(N)];
const verbM = new Float32Array(N), verbS = new Float32Array(N);   // reverb sends per bus

/* deterministic noise */
let seed = 1234567;
const rnd = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296 * 2 - 1; };

function put(bus, t0, len, fn, pan = 0, send = 0) {
  const i0 = Math.round(t0 * SR), n = Math.round(len * SR);
  const gl = Math.cos((pan + 1) * Math.PI / 4), gr = Math.sin((pan + 1) * Math.PI / 4);
  for (let k = 0; k < n; k++) {
    const i = i0 + k; if (i < 0 || i >= N) continue;
    const v = fn(k / SR, k);
    bus[0][i] += v * gl * 1.414; bus[1][i] += v * gr * 1.414;
    if (send) (bus === sfx ? verbS : verbM)[i] += v * send;
  }
}
/* biquad (RBJ) */
function biquad(type, f, q) {
  const w = 2 * Math.PI * f / SR, c = Math.cos(w), s = Math.sin(w), a = s / (2 * q);
  let b0, b1, b2, a0, a1, a2;
  if (type === 'lp') { b0 = (1-c)/2; b1 = 1-c; b2 = (1-c)/2; }
  else if (type === 'hp') { b0 = (1+c)/2; b1 = -(1+c); b2 = (1+c)/2; }
  else { b0 = a; b1 = 0; b2 = -a; }
  a0 = 1 + a; a1 = -2 * c; a2 = 1 - a;
  let x1 = 0, x2 = 0, y1 = 0, y2 = 0;
  return x => { const y = (b0*x + b1*x1 + b2*x2 - a1*y1 - a2*y2) / a0; x2 = x1; x1 = x; y2 = y1; y1 = y; return y; };
}

/* ── instruments ─────────────────────────────────────────── */
function kick(t0, g = 1, bus = mus) {
  let ph = 0;
  put(bus, t0, 0.5, (t) => {
    const f = 46 + 120 * Math.exp(-t * 30);
    ph += 2 * Math.PI * f / SR;
    const body = Math.sin(ph) * Math.exp(-t * 6.5);
    const click = t < 0.004 ? rnd() * (1 - t / 0.004) * 0.5 : 0;
    return (Math.tanh(body * 1.6) * 0.9 + click) * g;
  });
}
function clap(t0, g = 1) {
  const bp = biquad('bp', 1300, 0.9);
  put(mus, t0, 0.3, (t) => {
    const e = (t < 0.036 ? Math.exp(-((t % 0.012) * 260)) : 0) + Math.exp(-(t) * 16) * 0.8;
    return bp(rnd()) * e * 2.4 * g;
  }, 0, 0.35);
}
function snare(t0, g = 1, bus = mus) {
  const hp = biquad('hp', 1800, 0.7);
  let ph = 0;
  put(bus, t0, 0.22, (t) => {
    ph += 2 * Math.PI * 190 / SR;
    return (hp(rnd()) * Math.exp(-t * 20) * 0.9 + Math.sin(ph) * Math.exp(-t * 32) * 0.5) * g;
  }, 0, 0.2);
}
function hat(t0, g = 1, open = false, pan = 0.2) {
  const hp = biquad('hp', 7500, 0.8);
  put(mus, t0, open ? 0.3 : 0.06, (t) => hp(rnd()) * Math.exp(-t * (open ? 11 : 70)) * g, pan);
}
function pluck(t0, f, g = 1, pan = 0, dur = 0.9, send = 0.35, bus = mus) {
  const L = Math.max(2, Math.round(SR / f)); const buf = new Float32Array(L);
  for (let i = 0; i < L; i++) buf[i] = rnd();
  let idx = 0, last = 0;
  put(bus, t0, dur, (t) => {
    const v = buf[idx]; const nx = buf[(idx + 1) % L];
    buf[idx] = 0.4985 * (v + nx); idx = (idx + 1) % L;
    last = last * 0.3 + v * 0.7;
    return last * g * Math.min(1, (dur - t) * 20);
  }, pan, send);
}
function bassNote(t0, f, len, g = 1) {
  const lp = biquad('lp', 380, 1.1); let ph = 0;
  put(mus, t0, len, (t) => {
    ph = (ph + f / SR) % 1;
    const saw = ph * 2 - 1, sub = Math.sin(2 * Math.PI * ph);
    const env = Math.min(1, t / 0.006) * Math.min(1, (len - t) / 0.02) * (0.75 + 0.25 * Math.exp(-t * 8));
    return (lp(saw) * 0.7 + sub * 0.6) * env * g;
  });
}
function padChord(t0, freqs, len, g = 1) {
  freqs.forEach((f, k) => [-0.12, 0.12].forEach((det, j) => {
    const lp = biquad('lp', 1500, 0.7); let ph = rnd() * 0.5 + 0.5;
    const ff = f * Math.pow(2, det / 12);
    put(mus, t0, len, (t) => {
      ph = (ph + ff / SR) % 1;
      const env = Math.min(1, t / 0.25) * Math.min(1, (len - t) / 0.3);
      return lp(ph * 2 - 1) * env * g * 0.09;
    }, j ? 0.45 : -0.45, 0.25);
  }));
}
function boom(t0, g = 1) {
  let ph = 0; const lp = biquad('lp', 900, 0.7);
  put(sfx, t0, 1.6, (t) => {
    const f = 38 + 90 * Math.exp(-t * 9); ph += 2 * Math.PI * f / SR;
    return (Math.tanh(Math.sin(ph) * 2.2) * Math.exp(-t * 3.2) * 0.9 + lp(rnd()) * Math.exp(-t * 14) * 0.7) * g;
  }, 0, 0.4);
}
function whoosh(t0, dur, up = true, g = 1, pan = 0) {
  let bp = null; let lastF = 0;
  put(sfx, t0, dur, (t) => {
    const u = t / dur, f = up ? 300 * Math.pow(22, u) : 6600 / Math.pow(22, u);
    if (!bp || Math.abs(f - lastF) > 40) { const nb = biquad('bp', f, 1.4); bp = nb; lastF = f; }
    return bp(rnd()) * Math.sin(Math.PI * u) * 2.6 * g;
  }, pan, 0.2);
}
function riser(t0, dur, g = 1) {
  let ph = 0, bp = null, lastF = 0;
  put(sfx, t0, dur, (t) => {
    const u = t / dur, fn = 400 * Math.pow(18, u);
    if (!bp || Math.abs(fn - lastF) > 30) { bp = biquad('bp', fn, 2); lastF = fn; }
    const fs = 110 * Math.pow(8, u); ph = (ph + fs / SR) % 1;
    return (bp(rnd()) * 2 + (ph * 2 - 1) * 0.12) * Math.pow(u, 1.8) * g;
  }, 0, 0.3);
}
function swell(t0, dur, g = 1) {            // reverse-cymbal suck into a hit
  const hp = biquad('hp', 2500, 0.7);
  put(sfx, t0, dur, (t) => hp(rnd()) * Math.pow(t / dur, 3) * 1.1 * g, 0, 0.2);
}
function pop(t0, g = 1, base = 380, pan = 0) {
  let ph = 0;
  put(sfx, t0, 0.09, (t) => { const f = base + (base * 2) * (t / 0.09); ph += 2 * Math.PI * f / SR;
    return Math.sin(ph) * Math.exp(-t * 38) * 0.9 * g; }, pan, 0.15);
}
function ding(t0, f, g = 1, pan = 0) {
  put(sfx, t0, 0.5, (t) => (Math.sin(2 * Math.PI * f * t) + 0.35 * Math.sin(2 * Math.PI * f * 2.01 * t))
    * Math.exp(-t * 9) * Math.min(1, t / 0.003) * 0.42 * g, pan, 0.3);
}
function tick(t0, g = 1, f = 2600, pan = 0) {
  put(sfx, t0, 0.02, (t) => Math.sin(2 * Math.PI * f * t) * Math.exp(-t * 300) * g, pan);
}
function tap(t0, g = 1) {
  const hp = biquad('hp', 3000, 0.7); let ph = 0;
  put(sfx, t0, 0.07, (t) => { ph += 2 * Math.PI * 140 / SR;
    return (hp(rnd()) * Math.exp(-t * 500) * 0.8 + Math.sin(ph) * Math.exp(-t * 60) * 0.7) * g; });
}
function marimba(t0, f, g = 1, pan = 0) {
  put(sfx, t0, 0.5, (t) => (Math.sin(2 * Math.PI * f * t) * Math.exp(-t * 11)
    + 0.25 * Math.sin(2 * Math.PI * f * 3.9 * t) * Math.exp(-t * 40)) * Math.min(1, t / 0.002) * 0.5 * g, pan, 0.3);
}
function buzz(t0, dur, g = 1) {
  const lp = biquad('lp', 700, 0.9);
  put(sfx, t0, dur, (t) => lp(Math.sign(Math.sin(2 * Math.PI * 172 * t))) * Math.min(1, t / 0.01, (dur - t) / 0.01) * 0.55 * g);
}
function glitch(t0, dur, g = 1) {
  let hold = 0, v = 0;
  put(sfx, t0, dur, (t, k) => { if (k % 220 === 0) { hold = Math.floor(rnd() * 6 + 2); }
    if (k % hold === 0) v = Math.round(rnd() * 3) / 3; return v * 0.5 * g * (Math.sin(t * 90) > -0.2 ? 1 : 0); }, 0);
}
function sparkle(t0, dur, count, g = 1) {
  for (let i = 0; i < count; i++) {
    const tt = t0 + Math.abs(rnd()) * dur, f = 3500 + Math.abs(rnd()) * 4500;
    put(sfx, tt, 0.08, (t) => Math.sin(2 * Math.PI * f * t) * Math.exp(-t * 55) * 0.18 * g, rnd() * 0.8, 0.4);
  }
}
function slot(t0, dur, g = 1) {           // decelerating clicks
  let t = 0, gap = 0.028;
  while (t < dur) { tick(t0 + t, 0.5 * g, 1800 + (t / dur) * 900); t += gap; gap *= 1.09; }
}

/* ── notes ───────────────────────────────────────────────── */
const hz = n => 440 * Math.pow(2, (n - 69) / 12);          // midi → Hz
const CH = {                                                 // chord tones (midi)
  Am: {bass:33, tones:[57, 60, 64]}, F: {bass:29, tones:[53, 57, 60]},
  C:  {bass:36, tones:[60, 64, 67]}, G: {bass:31, tones:[55, 59, 62]}
};
const PROG = ['Am', 'F', 'C', 'G'];
const chordAt = t => CH[PROG[Math.floor((t - 4) / 2) % 4]];
const PENTA = [57, 60, 62, 64, 67, 69, 72, 74, 76, 79, 81, 84];   // A minor pentatonic


/* ══════════════ ARRANGEMENT ══════════════
   0–4 hook · 4–32 seven tips (4s each) · 32–36 CTA.
   Tip i starts at T = 4 + 4i; the wipe into it lands on T. */
const kicks = [];
const TIPS = [0, 1, 2, 3, 4, 5, 6].map(i => 4 + i * 4);

/* HOOK: a hit on every line, a ticking clock, then a riser into the drop */
[0, 0.5, 1.0, 1.5].forEach((t, i) => { boom(t, 0.5 + i * 0.08); kick(t, 0.6, sfx); });
for (let t = 0; t < 3.5; t += 0.25) tick(t, 0.3, t % 0.5 ? 3200 : 2600, 0.3);
marimba(2.0, hz(76), 0.8); whoosh(1.95, 0.35, true, 0.7, 0.2);
padChord(0, [45, 52].map(hz), 4.0, 1.2);
riser(2.4, 1.55, 0.9);
for (let t = 3.0; t < 3.875; t += (t < 3.5 ? 0.125 : 0.0625)) snare(t, 0.25 + (t - 3) * 0.4, sfx);

/* GROOVE 4–32, softer tail through the CTA */
for (let t = 4; t < 34; t += 0.5) { kick(t, t < 32 ? 1.0 : 0.7); kicks.push(t); }
for (let t = 4; t < 32; t += 1) clap(t + 0.5, 0.65);
for (let t = 4; t < 34; t += 0.125) { const off = Math.abs((t % 0.5) - 0.25) < 1e-6;
  if (off) hat(t, 0.26, true, -0.25); else hat(t, (t % 0.25) ? 0.12 : 0.19); }
for (let t = 4; t < 34; t += 0.25) {
  const ch = chordAt(t); const oct = (Math.round(t / 0.25) % 4 === 3) ? 12 : 0;
  bassNote(t, hz(ch.bass + oct), 0.22, 0.85);
}
for (let b = 4; b < 34; b += 2) padChord(b, chordAt(b).tones.map(hz), 2.0, 0.9);
const ARP = [0, 1, 2, 3, 2, 1, 3, 2];
for (let t = 4; t < 32; t += 0.25) {
  const ch = chordAt(t), step = Math.round((t - 4) / 0.25) % 8;
  const tones = ch.tones.concat(ch.tones.map(x => x + 12));
  pluck(t, hz(tones[ARP[step]] + 12), 0.22, step % 2 ? 0.35 : -0.35, 0.5, 0.3);
}
boom(4.0, 1.1); sparkle(4.0, 0.3, 10, 0.7);

/* per-tip sound design: wipe, numeral, words, marker, card, typing */
TIPS.forEach((T, i) => {
  if (i > 0) whoosh(T - 0.42, 0.44, true, 1.0, (i % 2) ? 0.3 : -0.3);
  pop(T + 0.05, 1.0, 300 + i * 20);
  for (let k = 0; k < 5; k++) marimba(T + 0.35 + k * 0.07, hz(PENTA[(i + k) % 8]), 0.55, (k - 2) * 0.2);
  whoosh(T + 1.0, 0.26, true, 0.55, 0.2);
  kick(T + 1.9, 0.55, sfx); pop(T + 1.92, 0.7, 240);
  for (let t = T + 2.0; t < T + 3.2; t += 0.047) tick(t, 0.16, 3600 + Math.abs(rnd()) * 900, rnd() * 0.3);
});
whoosh(31.58, 0.44, true, 1.0);

/* CTA: three hits, the final chord, a pop on the save icon */
[32.0, 32.5, 33.0].forEach(t => { boom(t, 0.55); });
marimba(33.5, hz(79), 0.9);
boom(34.0, 1.0); kick(34.0, 1.0);
padChord(34.0, [60, 64, 67, 72].map(hz), 2.6, 2.0);
[48, 60, 64, 67, 72].forEach((n, i) => pluck(34.0 + i * 0.02, hz(n), 0.5, (i - 2) * 0.25, 2.2, 0.6));
pop(34.5, 1.0, 340); sparkle(34.5, 0.5, 18, 0.8);

/* ── sidechain on the music bus ─────────────────────────── */
kicks.sort((a, b) => a - b);
{
  let k = 0;
  for (let i = 0; i < N; i++) {
    const t = i / SR;
    while (k + 1 < kicks.length && kicks[k + 1] <= t) k++;
    const since = t - kicks[k];
    const duck = (since >= 0 && since < 0.3 && t >= 4) ? 1 - 0.55 * Math.exp(-since * 14) : 1;
    mus[0][i] *= duck; mus[1][i] *= duck;
  }
}

/* ── reverb (Schroeder) ─────────────────────────────────── */
function reverb(inp, tweak) {
  const combs = [1557, 1617, 1491, 1422, 1277, 1356].map(d => ({d: d + tweak, b: new Float32Array(d + tweak), i: 0, f: 0}));
  const aps = [225, 556, 441].map(d => ({d: d + tweak, b: new Float32Array(d + tweak), i: 0}));
  const out = new Float32Array(N);
  for (let n = 0; n < N; n++) {
    let s = 0; const x = inp[n] * 0.12;
    for (const c of combs) { const y = c.b[c.i]; c.f = y * 0.7 + c.f * 0.3; c.b[c.i] = x + c.f * 0.86; c.i = (c.i + 1) % c.d; s += y; }
    for (const a of aps) { const y = a.b[a.i]; const v = -s * 0.5 + y; a.b[a.i] = s + y * 0.5; a.i = (a.i + 1) % a.d; s = v; }
    out[n] = s;
  }
  return out;
}
const rvML = reverb(verbM, 0), rvMR = reverb(verbM, 23), rvSL = reverb(verbS, 0), rvSR = reverb(verbS, 23);

/* ── mixdown ────────────────────────────────────────────── */
function master(parts, name, withMusicVerb) {
  const L = new Float32Array(N), R = new Float32Array(N);
  parts.forEach(([bus, g]) => { for (let i = 0; i < N; i++) { L[i] += bus[0][i] * g; R[i] += bus[1][i] * g; } });
  for (let i = 0; i < N; i++) { L[i] += rvSL[i] * 0.55 + (withMusicVerb ? rvML[i] * 0.4 : 0); R[i] += rvSR[i] * 0.55 + (withMusicVerb ? rvMR[i] * 0.4 : 0); }
  let pk = 0;
  for (let i = 0; i < N; i++) { L[i] = Math.tanh(L[i] * 1.1); R[i] = Math.tanh(R[i] * 1.1); pk = Math.max(pk, Math.abs(L[i]), Math.abs(R[i])); }
  const norm = 0.89 / pk;
  /* 20ms fade at the very end */
  const buf = Buffer.alloc(44 + N * 4);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + N * 4, 4); buf.write('WAVE', 8); buf.write('fmt ', 12);
  buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(2, 22); buf.writeUInt32LE(SR, 24);
  buf.writeUInt32LE(SR * 4, 28); buf.writeUInt16LE(4, 32); buf.writeUInt16LE(16, 34); buf.write('data', 36); buf.writeUInt32LE(N * 4, 40);
  for (let i = 0; i < N; i++) {
    const f = Math.min(1, (N - i) / (0.02 * SR));
    buf.writeInt16LE(Math.round(Math.max(-1, Math.min(1, L[i] * norm * f)) * 32767), 44 + i * 4);
    buf.writeInt16LE(Math.round(Math.max(-1, Math.min(1, R[i] * norm * f)) * 32767), 46 + i * 4);
  }
  fs.writeFileSync(name, buf); console.log('wrote', name, 'peak pre-norm', pk.toFixed(3));
}
master([[mus, 0.6], [sfx, 0.85]], 'full.wav', true);
master([[sfx, 1.0]], 'sfx.wav', false);
