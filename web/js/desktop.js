// The Windows app (desktop/) shows this page in a window without a title bar: the top bar takes its place. In the
// app, the bar gets the three window buttons and is where the window is dragged by; in a browser none of this shows.
// "?shell=desktop" shows the buttons in a browser too, for looking at them.
import { h, fill } from './dom.js';
import { t } from './i18n.js';
import { icon } from './ui.js';

const view = window.chrome && window.chrome.webview;
const inApp = Boolean(view) || new URLSearchParams(location.search).get('shell') === 'desktop';
let maximized = false;
let group = null;

function post(cmd) {
  if (view) view.postMessage({ cmd });
}

function draw() {
  const button = (cls, iconId, label, title, cmd) => h('button', { class: 'win-btn ' + cls, type: 'button', title,
    'aria-label': label, onclick: () => post(cmd) }, icon(iconId));
  fill(group,
    button('min', 'i-win-min', t('nav.window_min'), t('nav.window_min'), 'min'),
    maximized ? button('max', 'i-win-restore', t('nav.window_restore'), t('nav.window_restore'), 'max')
      : button('max', 'i-win-max', t('nav.window_max'), t('nav.window_max'), 'max'),
    button('close', 'i-x', t('nav.window_close'), t('nav.window_close_hint'), 'close'));
  group.setAttribute('aria-label', t('nav.window'));
}

// Puts the buttons at the end of the top bar; called again after the language changes.
export function mountWindowButtons() {
  if (!inApp) return;
  if (!group) {
    document.documentElement.classList.add('desktop');
    group = h('div', { class: 'win-ctl', role: 'group' });
    document.getElementById('top').append(group);
    if (view) {
      view.addEventListener('message', (e) => {
        const m = e.data;
        if (m && m.cmd === 'window') { maximized = Boolean(m.maximized); draw(); }
      });
      post('window');     // the window answers with its state
    }
    // the browser's own keys are off in the app; F5 still reloads the page
    window.addEventListener('keydown', (e) => {
      if (e.key === 'F5' && !e.ctrlKey && !e.altKey) { e.preventDefault(); location.reload(); }
    });
  }
  draw();
}
