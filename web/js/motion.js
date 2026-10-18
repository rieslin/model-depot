// Motion in the Roster Archive's world: scheme 3 "decode", as the recorded draft the user chose did it (2026-10-03;
// the user asked for the draft itself back after a reworked version the same day). A picture arrives as data being
// decoded: canvases of coarser and finer blocks lie over it, the coarsest on top, and are cut away one after another
// until the picture itself shows, which was underneath all along. The model's name is wiped in from left to right, the
// sections under it rise a few pixels into place, and a chosen card's notch snaps on; a filed download's entry number
// runs through other characters before it settles. Every motion is an animation with an end (the Web Animations API,
// filled backwards only, or a canvas taken away), so whatever a motion hides for a while shows again when it ends, and
// at once when a script fails. Less motion asked for: pictures and type only fade in briefly, nothing moves.

// The pace of every motion, from app.css (--motion-scale: the one knob). The lengths below are at a scale of 1, the
// draft's own, and every length and delay here goes through pace().
let scale = null;
export function pace(ms) {
  if (scale === null) scale = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--motion-scale')) || 1;
  return ms * scale;
}

const T = { readout: 300, rise: 240, snap: 150, fade: 180, quiet: 160 };
const EASE = 'cubic-bezier(0.16, 1, 0.3, 1)';
const EASE_READOUT = 'cubic-bezier(0.25, 1, 0.5, 1)';

export function reduced() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

// The motion running on an element, as the function that stops it: a new motion on the same element stops the last.
const running = new WeakMap();

function stopOn(el) {
  const stop = running.get(el);
  if (stop) stop();
}

function play(el, keyframes, options) {
  stopOn(el);
  const animation = el.animate(keyframes, { fill: 'backwards', ...options });
  const stop = () => animation.cancel();
  running.set(el, stop);
  const forget = () => { if (running.get(el) === stop) running.delete(el); };
  animation.finished.then(forget, forget);
  return animation;
}

function quietly(el, delay = 0) {
  return play(el, [{ opacity: 0 }, { opacity: 1 }], { duration: T.quiet, delay: pace(delay), easing: 'ease-out' });
}

// The part of a picture its box shows, and where in the box: object-fit cover, contain or fill, at its object-position.
function shown(img) {
  const w = img.offsetWidth, h = img.offsetHeight;
  const nw = img.naturalWidth, nh = img.naturalHeight;
  const style = getComputedStyle(img);
  const [px, py] = style.objectPosition.split(' ').map((v) => (v.endsWith('%') ? parseFloat(v) / 100 : 0.5));
  if (style.objectFit === 'cover') {
    const s = Math.max(w / nw, h / nh);
    const sw = w / s, sh = h / s;
    return { sx: (nw - sw) * px, sy: (nh - sh) * py, sw, sh, dx: 0, dy: 0, dw: w, dh: h };
  }
  if (style.objectFit === 'contain') {
    const s = Math.min(w / nw, h / nh);
    const dw = nw * s, dh = nh * s;
    return { sx: 0, sy: 0, sw: nw, sh: nh, dx: (w - dw) * px, dy: (h - dh) * py, dw, dh };
  }
  return { sx: 0, sy: 0, sw: nw, sh: nh, dx: 0, dy: 0, dw: w, dh: h };
}

// A picture decoded: one canvas for each block size in `cells` (CSS pixels, coarsest first), laid over the picture with
// the coarsest on top; each is cut away at the end of its `step` ms, and all are taken away 20 ms after the last. The
// picture is never hidden: it shows through as the canvases go. A picture still loading is decoded when it arrives.
export function decode(img, { cells = [30, 15, 7, 3], step = 90 } = {}) {
  if (!img) return;
  stopOn(img);
  const canvases = [];
  let timer = 0;
  let over = false;
  const stop = () => {
    if (over) return;
    over = true;
    clearTimeout(timer);
    for (const c of canvases) c.remove();
    if (running.get(img) === stop) running.delete(img);
  };
  running.set(img, stop);
  let tries = 0;
  const begin = () => {
    if (over) return;
    if (reduced()) { stop(); quietly(img); return; }
    if (!img.isConnected || !img.parentElement) { stop(); return; }
    if (!img.offsetWidth || !img.offsetHeight) {
      // not laid out yet (a dialog about to open): look again once, then show the picture as it is
      if (tries++ === 0) { timer = setTimeout(begin, 0); return; }
      stop();
      return;
    }
    const box = shown(img);
    const w = img.offsetWidth, h = img.offsetHeight;
    const total = step * cells.length + 20;
    // each goes in right after the picture, coarsest first: the coarsest ends up last, so on top of the others, and
    // what the box shows over the picture (its count, its buttons, a badge) stays on top of them all
    for (let i = 0; i < cells.length; i++) {
      const c = document.createElement('canvas');
      c.className = 'decode-px';
      c.setAttribute('aria-hidden', 'true');
      c.width = Math.max(1, Math.round(w / cells[i]));
      c.height = Math.max(1, Math.round(h / cells[i]));
      Object.assign(c.style, { left: img.offsetLeft + 'px', top: img.offsetTop + 'px', width: w + 'px', height: h + 'px' });
      const g = c.getContext('2d');
      const kx = c.width / w, ky = c.height / h;
      g.imageSmoothingEnabled = true;
      g.imageSmoothingQuality = 'high';
      g.drawImage(img, box.sx, box.sy, box.sw, box.sh, box.dx * kx, box.dy * ky, box.dw * kx, box.dh * ky);
      img.after(c);
      canvases.push(c);
      const end = step * (i + 1);
      c.animate([{ opacity: 1 }, { opacity: 1, offset: end / total }, { opacity: 0, offset: Math.min(1, (end + 1) / total) }, { opacity: 0 }],
        { duration: pace(total), fill: 'forwards' });
    }
    timer = setTimeout(stop, pace(total));
  };
  if (img.complete && img.naturalWidth) {
    begin();
  } else {
    img.addEventListener('load', begin, { once: true });
    img.addEventListener('error', stop, { once: true });
  }
}

// Type wiped in from left to right, across its box.
export function readout(el, delay = 0, { duration = T.readout } = {}) {
  if (!el) return;
  if (reduced()) { quietly(el, delay); return; }
  play(el, [{ clipPath: 'inset(0 100% 0 0)' }, { clipPath: 'inset(0 0 0 0)' }],
    { duration: pace(duration), delay: pace(delay), easing: EASE_READOUT });
}

// A section rising a few pixels into place as it appears.
export function rise(el, delay = 0, { duration = T.rise } = {}) {
  if (!el) return;
  if (reduced()) { quietly(el, delay); return; }
  play(el, [{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }],
    { duration: pace(duration), delay: pace(delay), easing: EASE });
}

// The notch of the chosen card snapping on, from larger and clear into place.
export function snap(card) {
  if (!card) return;
  if (reduced()) {
    play(card, [{ opacity: 0 }, { opacity: 1 }], { duration: T.quiet, easing: 'ease-out', pseudoElement: '::after' });
    return;
  }
  play(card, [{ opacity: 0, transform: 'scale(1.9)', easing: EASE }, { opacity: 1, offset: 0.4 }, { opacity: 1, transform: 'none' }],
    { duration: pace(T.snap), pseudoElement: '::after' });
}

// Something that only appears: a masked picture's mosaic.
export function fade(el, { duration = T.fade } = {}) {
  if (!el) return;
  if (reduced()) { quietly(el); return; }
  play(el, [{ opacity: 0 }, { opacity: 1 }], { duration: pace(duration), easing: 'ease-out' });
}

// A code decoded (the entry number): its letters and digits run through others before they settle. The code itself is
// written back at the end whatever happens on the way.
const HEX = '0123456789ABCDEF';
export function scramble(el, { steps = 6, step = 45 } = {}) {
  if (!el || reduced()) return;
  const text = el.textContent;
  let n = 0;
  const tick = () => {
    if (!el.isConnected) return;
    if (n++ < steps) {
      el.textContent = text.replace(/[0-9A-Za-z]/g, () => HEX[Math.floor(Math.random() * HEX.length)]);
      setTimeout(tick, pace(step));
    } else {
      el.textContent = text;
    }
  };
  tick();
  setTimeout(() => { if (el.textContent !== text) el.textContent = text; }, pace(step * (steps + 2)));
}

// Is a motion running on the element now (a picture still waiting for its data counts)?
export function moving(el) {
  return Boolean(el) && running.has(el);
}

// Is the element on screen now (any part of it)?
export function onScreen(el) {
  const r = el.getBoundingClientRect();
  return r.bottom > 0 && r.right > 0 && r.top < window.innerHeight && r.left < window.innerWidth;
}
