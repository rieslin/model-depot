/* The loading screen's shared driver. Every motion on the page is a function of one clock, t in seconds since the screen
   opened, so any frame can be drawn for any t (the checks capture frames that way: LOADER.seek(t, exitAt)).
   A direction registers itself with LOADER.use({ mark, layout, draw(t, e), still(e), failed(), hold, bar }):
     mark      the wordmark element; at the hand-over it travels to where the app's top bar letters the name
     layout()  measure the window again (called after a resize, and before the first frame)
     draw(t,e) draw the frame for t; e is null, or the seconds since the window called ready()
     still(e)  the calm frame shown when less motion is asked for (e as in draw)
     failed()  the frame left standing when the program did not start
     hold      seconds after ready() during which the direction still finishes its picture; the shared hand-over
               (fades, the name's journey, the top bar) begins that much later
     bar       true when the direction shows the app's top bar from the start (nothing travels at the hand-over)
   The window calls LOADER.ready() once the program answers, waits LOADER.EXIT seconds (the promise ready() returns
   ends with the last frame; the page also posts {cmd: "handed"} then), and shows the program's page in its place.
   LOADER.fail(message, html) stops on the failure note. */
(() => {
  const q = new URLSearchParams(location.search);
  const root = document.documentElement;
  const mq = matchMedia("(prefers-reduced-motion: reduce)");

  const WORDS = {
    zh: { starting: "正在启动 Model Depot…", long: "启动所需时间较平常更长，Model Depot 仍在启动。", failed: "Model Depot 未能启动。",
      retry: "重试", logs: "打开日志文件夹", window: "窗口", min: "最小化", max: "最大化", restore: "向下还原", close: "关闭" },
    en: { starting: "Starting Model Depot…", long: "This is taking longer than usual. Model Depot is still starting.",
      failed: "Model Depot could not start.", retry: "Retry", logs: "Open log folder", window: "Window",
      min: "Minimize", max: "Maximize", restore: "Restore Down", close: "Close" },
  };
  // the drafts only: a log tail of the kind the window passes (the message is the program's own, depot/main.py)
  const SAMPLE_LOG = "2026-10-08 09:14:03,402 INFO depot: settings read from data\\config.json\n" +
    "2026-10-08 09:14:03,618 ERROR depot: port 8194 is already in use: is Model Depot already running?";

  // until the first frame is drawn the composition is not shown (it would show once where its opening ends); without
  // the script it shows as it stands
  root.dataset.wait = "";
  // the language: the address (?lang=en, the drafts) or the window's LOADER_WORDS.lang (the app shows the page
  // without an address)
  const given = window.LOADER_WORDS || {};
  const L = window.LOADER = {
    lang: (q.get("lang") || given.lang) === "en" ? "en" : "zh",
    reduced: q.get("motion") === "reduce" || (q.get("motion") !== "full" && mq.matches),
    capture: q.has("capture"),
    embed: q.has("embed"),
    long: q.has("long"),
    EXIT: 0.4,           // seconds from the start of the hand-over to its last frame (set again by use())
    LONG_AFTER: 12,      // seconds before the second line says the start is slow
    MIN_SHOWN: 0.45,     // the opening always finishes before the hand-over begins
    t: 0, exitAt: null, failed: false, frozen: null, dir: null, dirty: true, t0: 0, running: false, canCapture: false,
    stats: { frames: 0, ms: 0, worst: 0 },
  };
  root.lang = L.lang === "en" ? "en" : "zh-Hans";
  if (L.capture) root.dataset.capture = "";
  if (L.embed) root.dataset.embed = "";

  // ---- small math ------------------------------------------------------------------------------------------------
  L.clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
  L.ramp = (t, a, b) => L.clamp((t - a) / (b - a));
  L.lerp = (a, b, k) => a + (b - a) * k;
  L.bezier = (x1, y1, x2, y2) => {
    const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
    const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
    const sx = (s) => ((ax * s + bx) * s + cx) * s;
    const sy = (s) => ((ay * s + by) * s + cy) * s;
    const dx = (s) => (3 * ax * s + 2 * bx) * s + cx;
    return (x) => {
      if (x <= 0) return 0;
      if (x >= 1) return 1;
      let s = x;
      for (let i = 0; i < 8; i++) {
        const e = sx(s) - x, d = dx(s);
        if (Math.abs(e) < 1e-5 || Math.abs(d) < 1e-6) break;
        s -= e / d;
      }
      return sy(L.clamp(s));
    };
  };
  L.EASE = L.bezier(0.16, 1, 0.3, 1);          // the app's --ease
  L.EASE_READOUT = L.bezier(0.25, 1, 0.5, 1);  // the app's readout
  L.EASE_INOUT = L.bezier(0.45, 0, 0.2, 1);
  L.EASE_IN = L.bezier(0.6, 0, 0.84, 0);
  L.rng = (seed) => {  // mulberry32
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  };
  L.hash = (...n) => { let h = 2166136261; for (const v of n) { h ^= v | 0; h = Math.imul(h, 16777619); } return h >>> 0; };
  const rgbCache = new Map();
  L.rgb = (c) => {
    if (!rgbCache.has(c)) {
      const m = c.startsWith("rgb") ? c.match(/[\d.]+/g).slice(0, 3).map(Number) : null;
      const h = c.replace("#", "");
      rgbCache.set(c, m || [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]);
    }
    return rgbCache.get(c);
  };
  L.mix = (a, b, k) => {
    const A = L.rgb(a), B = L.rgb(b);
    return `rgb(${Math.round(A[0] + (B[0] - A[0]) * k)},${Math.round(A[1] + (B[1] - A[1]) * k)},${Math.round(A[2] + (B[2] - A[2]) * k)})`;
  };
  L.css = (name) => getComputedStyle(root).getPropertyValue(name).trim();
  L.dpr = () => Math.max(1, Math.min(3, window.devicePixelRatio || 1));
  // type wiped in from the left, as the app's readout does it
  L.wipe = (el, k) => { el.style.clipPath = k >= 1 ? "" : `inset(0 ${(100 * (1 - L.EASE_READOUT(L.clamp(k)))).toFixed(2)}% 0 0)`; };
  // a section rising 8 px into place, as the app's rise does it
  L.rise = (el, k) => {
    const v = L.EASE(L.clamp(k));
    el.style.opacity = v >= 1 ? "" : v.toFixed(3);
    el.style.transform = v >= 1 ? "" : `translateY(${(8 * (1 - v)).toFixed(2)}px)`;
  };
  // the same for a large dark shape: its opacity follows a gentler curve, so that it does not stand at half strength
  // in the first frame (on a light ground the app's ease is too abrupt for a block this size)
  L.riseSoft = (el, k) => {
    const v = L.EASE(L.clamp(k)), o = L.EASE_INOUT(L.clamp(k));
    el.style.opacity = o >= 1 ? "" : o.toFixed(3);
    el.style.transform = v >= 1 ? "" : `translateY(${(8 * (1 - v)).toFixed(2)}px)`;
  };
  // The app's decode, for a canvas: the picture `src` (or the part sx, sy, sw, sh of it, in its own pixels) reduced to
  // blocks of `cell` CSS pixels for a box of bw x bh CSS pixels; drawn back with L.blocksTo, without smoothing.
  L.blocks = (src, cell, bw, bh, sx = 0, sy = 0, sw = src.width, sh = src.height) => {
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(bw / cell)); c.height = Math.max(1, Math.round(bh / cell));
    const g = c.getContext("2d");
    g.imageSmoothingEnabled = true; g.imageSmoothingQuality = "high";
    g.drawImage(src, sx, sy, sw, sh, 0, 0, c.width, c.height);
    return c;
  };
  L.blocksTo = (g, small, dx, dy, dw, dh) => {
    g.save(); g.imageSmoothingEnabled = false; g.drawImage(small, dx, dy, dw, dh); g.restore();
  };
  L.words = Object.assign({}, WORDS[L.lang], given);

  // ---- the page's own parts ----------------------------------------------------------------------------------------
  const $ = (s) => document.querySelector(s);
  let bar, ground, barWm, msg, msg2, failBox, win, fly = null, flyFrom = null, outs = [], touched = false;

  function post(cmd) {
    try { window.chrome.webview.postMessage({ cmd }); } catch { /* a browser, not the app's window */ }
  }

  function labelWindow() {
    const W = L.words;
    document.querySelectorAll("[data-l-aria]").forEach((el) => el.setAttribute("aria-label", W[el.dataset.lAria]));
    const max = $(".win [data-cmd=max]");
    if (max) max.setAttribute("aria-label", root.dataset.max !== undefined ? W.restore : W.max);
  }

  function wire() {
    bar = $(".hand .bar"); ground = $(".hand .ground"); barWm = $(".hand .wm");
    msg = $("[data-msg]"); msg2 = $("[data-msg2]"); failBox = $("[data-fail]"); win = $(".win");
    outs = [...document.querySelectorAll("[data-out]")];
    if (msg) msg.textContent = L.words.starting;
    if (msg2) msg2.textContent = L.words.long;
    labelWindow();
    document.addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-cmd]");
      if (b) post(b.dataset.cmd);
      const p = ev.target.closest("[data-proto]");
      if (p) proto(p.dataset.proto);
    });
    document.addEventListener("keydown", (ev) => {
      const k = { r: "ready", f: "fail", l: "long", m: "motion", e: "lang", " ": "replay" }[ev.key.toLowerCase()];
      // the draft keys exist only where the draft controls do (never in the page the app carries)
      if (k && !L.capture && !L.embed && document.querySelector(".proto")) { ev.preventDefault(); proto(k); }
    });
    // the index page plays the drafts in frames and sends them the same commands
    addEventListener("message", (ev) => { if (ev.data && typeof ev.data.proto === "string") proto(ev.data.proto); });
    // the window says whether it is maximized, for the glyph of the middle button (as the app's page asks for it)
    try {
      window.chrome.webview.addEventListener("message", (ev) => {
        const m = ev.data;
        if (m && m.cmd === "window") { if (m.maximized) root.dataset.max = ""; else delete root.dataset.max; labelWindow(); }
      });
      post("window");
    } catch { /* a browser */ }
    addEventListener("resize", () => { L.dirty = true; if (!L.running) render(L.t); });
    mq.addEventListener?.("change", () => { if (q.get("motion")) return; L.reduced = mq.matches; L.dirty = true; kick(); });
  }

  function proto(what) {
    const u = new URL(location.href);
    if (what === "ready") L.ready();
    else if (what === "fail") L.fail();
    else if (what === "long") { L.long = true; render(L.t); }
    else if (what === "replay") location.reload();
    else if (what === "motion") { u.searchParams.set("motion", L.reduced ? "full" : "reduce"); location.href = u.href; }
    else if (what === "lang") { u.searchParams.set("lang", L.lang === "en" ? "zh" : "en"); location.href = u.href; }
  }

  // ---- the hand-over ---------------------------------------------------------------------------------------------
  function prepFly() {
    const mark = L.dir && L.dir.mark;
    if (!mark) return;
    const r = mark.getBoundingClientRect();
    const fs = parseFloat(getComputedStyle(mark).fontSize) * (r.width / (mark.offsetWidth || r.width));
    fly = mark.cloneNode(true);
    fly.removeAttribute("data-mark");
    fly.classList.add("fly");
    Object.assign(fly.style, { left: `${r.left}px`, top: `${r.top}px`, fontSize: `${fs}px`, transform: "none", clipPath: "none", opacity: "1" });
    document.body.append(fly);
    const b = barWm.getBoundingClientRect();
    const f = fly.getBoundingClientRect();
    flyFrom = { dx: b.left - f.left, dy: b.top - f.top, s: b.width / f.width };
    mark.style.visibility = "hidden";
  }

  function undoExit() {
    if (fly) { fly.remove(); fly = null; flyFrom = null; }
    if (L.dir && L.dir.mark) { L.dir.mark.style.visibility = ""; L.dir.mark.style.opacity = ""; }
    for (const el of outs) el.style.opacity = "";
    ground.style.opacity = ""; bar.style.opacity = ""; barWm.style.opacity = "";
    bar.style.removeProperty("--rule-k"); win.style.removeProperty("--win-rule");
  }

  function exitFrame(e) {
    const H = (L.dir && L.dir.hold) || 0;
    if (L.dir.bar) {  // the top bar is there already: the composition clears (the direction draws its own way out)
      const k = L.reduced ? L.ramp(e, 0, 0.16) : L.EASE(L.ramp(e - H, 0, 0.22));
      for (const el of outs) el.style.opacity = (1 - k).toFixed(3);
      ground.style.opacity = (L.reduced ? k : L.ramp(e - H, 0.12, 0.3)).toFixed(3);
      return;
    }
    if (L.reduced) {  // fades only: the composition gives way to the top bar in 0.16 s
      const k = L.ramp(e, 0, 0.16);
      for (const el of outs) el.style.opacity = (1 - k).toFixed(3);
      if (L.dir.mark) L.dir.mark.style.opacity = (1 - k).toFixed(3);
      ground.style.opacity = k; bar.style.opacity = k; barWm.style.opacity = k;
      bar.style.setProperty("--rule-k", 1); win.style.setProperty("--win-rule", k);
      return;
    }
    const x = e - H;
    if (x < 0) return;
    const out = L.EASE_INOUT(L.ramp(x, 0, 0.18));
    for (const el of outs) el.style.opacity = (1 - out).toFixed(3);
    ground.style.opacity = L.ramp(x, 0.04, 0.24).toFixed(3);
    bar.style.opacity = L.ramp(x, 0.1, 0.3).toFixed(3);
    bar.style.setProperty("--rule-k", L.EASE(L.ramp(x, 0.12, 0.38)).toFixed(4));
    win.style.setProperty("--win-rule", L.ramp(x, 0.16, 0.38).toFixed(3));
    // the name sets off once the composition has mostly cleared, so that it does not cross it
    if (x >= 0.4) {   // landed: the bar's own name takes over and the travelling copy goes
      if (fly) { fly.remove(); fly = null; flyFrom = null; }
      if (L.dir.mark) L.dir.mark.style.visibility = "hidden";
      barWm.style.opacity = 1;
      return;
    }
    if (!fly) prepFly();
    if (fly) {
      const k = L.EASE(L.ramp(x, 0.08, 0.4));
      const { dx, dy, s } = flyFrom;
      fly.style.transform = `translate(${(dx * k).toFixed(2)}px, ${(dy * k).toFixed(2)}px) scale(${(1 + (s - 1) * k).toFixed(5)})`;
      barWm.style.opacity = 0;
    }
  }
  // how long the hand-over lasts: with less motion asked for, only the 0.16 s fades
  const exitLength = () => (L.reduced ? 0.2 : L.EXIT);

  // ---- one frame -------------------------------------------------------------------------------------------------
  function render(t) {
    if (!L.dir) return;
    const a = performance.now();
    L.t = t;
    if (L.dirty) { L.dir.layout(); L.dirty = false; if (fly) undoExit(); }
    if (L.failed) { L.dir.failed(); delete root.dataset.wait; return; }
    const e = L.exitAt === null || t < L.exitAt ? null : t - L.exitAt;
    if (e === null && touched) { undoExit(); touched = false; }
    if (L.reduced) L.dir.still(e); else L.dir.draw(t, e);
    if (msg2) msg2.style.opacity = (L.long ? 1 : L.ramp(t, L.LONG_AFTER, L.LONG_AFTER + 0.4)).toFixed(3);
    if (e !== null) { exitFrame(e); touched = true; }
    delete root.dataset.wait;
    const ms = performance.now() - a;
    L.stats.frames++; L.stats.ms += ms; L.stats.worst = Math.max(L.stats.worst, ms);
  }

  const now = () => (L.frozen !== null ? L.frozen : (performance.now() - L.t0) / 1000);
  function loop() {
    if (!L.running) return;
    const t = now();
    render(t);
    // after the hand-over the last frame stands; the window is told, and shows the program's page in its place
    if (L.exitAt !== null && t > L.exitAt + exitLength() + 0.02) {
      L.running = false; post("handed");
      if (parent !== window) parent.postMessage({ loader: "handed" }, "*");
      return;
    }
    requestAnimationFrame(loop);
  }
  function kick() {
    if (L.capture || L.failed) { render(L.t); return; }
    if (L.reduced && L.exitAt === null) { L.running = false; render(now()); return; }
    if (!L.running) { L.running = true; requestAnimationFrame(loop); }
  }

  // ---- what the window (or a check) calls ---------------------------------------------------------------------------
  L.use = async (dir) => {
    L.dir = dir;
    L.EXIT = (dir.hold || 0) + (dir.bar ? 0.3 : 0.44);
    if (dir.bar) root.dataset.bar = "";
    wire();
    try {
      await Promise.all([document.fonts.load('900 31px "MD Mark"'), document.fonts.load('600 12px "MD Code"'),
        document.fonts.load('700 12px "MD Code"'), document.fonts.load('500 12px "MD Code"')]);
    } catch { /* the fallbacks are fine */ }
    L.dirty = true;
    L.t0 = performance.now();
    if (q.get("state") === "error") L.fail();
    L.canCapture = true;
    kick();
  };
  // a check: draw the frame for t (and, when given, a hand-over that began at exitAt)
  L.seek = (t, exitAt) => {
    L.frozen = t;
    L.running = false;
    if (exitAt !== undefined) L.exitAt = exitAt;
    render(t);
  };
  // the program answered: play the hand-over (after the opening, if it is still playing); the promise ends with its
  // last frame
  L.ready = () => {
    if (L.failed) return Promise.resolve();
    if (L.exitAt === null) L.exitAt = Math.max(now(), L.reduced ? 0 : L.MIN_SHOWN);
    if (L.reduced || !L.running) { L.running = true; requestAnimationFrame(loop); }
    const left = Math.max(0, L.exitAt + exitLength() - now());
    return new Promise((done) => setTimeout(done, left * 1000 + 20));
  };
  // the program did not start: the motion stops on a calm frame, the note says so, two buttons
  L.fail = (message, extraHtml) => {
    L.failed = true;
    L.running = false;
    L.exitAt = null;
    root.dataset.state = "failed";
    undoExit();
    if (msg) msg.textContent = message || L.words.failed;
    if (failBox) {
      if (extraHtml !== undefined) failBox.innerHTML = extraHtml;
      else {
        failBox.innerHTML = `<pre></pre><div class="act"><button type="button" class="solid" data-cmd="retry"></button><button type="button" data-cmd="logs"></button></div>`;
        if (document.querySelector(".proto")) failBox.querySelector("pre").textContent = SAMPLE_LOG;
        else failBox.querySelector("pre").remove();
        failBox.querySelector(".solid").textContent = L.words.retry;
        failBox.querySelector("[data-cmd=logs]").textContent = L.words.logs;
      }
    }
    if (L.dir) { L.dirty = true; render(L.t); }
  };
})();
