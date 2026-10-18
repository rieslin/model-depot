// Pick a folder (or a file) on the computer the server runs on. A browser cannot hand a page a real path, so the
// server lists folders and the choice is made here.
import { api } from './api.js';
import { h, fill } from './dom.js';
import { t } from './i18n.js';
import { icon } from './ui.js';

export function pickPath({ start = '', files = false } = {}) {
  const dialog = document.getElementById('picker');
  return new Promise((resolve) => {
    let current = '';
    const finish = (value) => {
      dialog.close();
      resolve(value);
    };
    dialog.oncancel = () => resolve(null);

    async function show(path) {
      let data;
      try {
        data = await api('GET', '/api/ui/browse?path=' + encodeURIComponent(path) + (files ? '&files=1' : ''));
      } catch (e) {
        if (path) return show('');      // an unreadable or vanished folder: start from the drives
        data = { path: '', parent: null, dirs: [], files: [] };
      }
      current = data.path;
      const goto = h('input', { type: 'text', value: current, placeholder: t('picker.type_path') });
      fill(dialog,
        h('h1', null, files ? t('picker.title_file') : t('picker.title_folder')),
        h('div', { class: 'field' }, goto, h('button', { onclick: () => show(goto.value.trim()) }, t('picker.go'))),
        h('div', { class: 'bar' },
          current ? h('button', { onclick: () => show(data.parent || '') }, t('picker.up')) : null,
          h('span', { class: 'dim' }, current || t('picker.drives'))),
        h('ul', null,
          data.dirs.map((d) => h('li', null, h('button', { class: 'dir', onclick: () => show(d.path) }, icon('i-folder'), d.name))),
          (data.files || []).map((f) => h('li', null, h('button', { onclick: () => finish(f.path) }, f.name))),
          !data.dirs.length && !(data.files || []).length ? h('li', { class: 'dim' }, t('picker.empty')) : null),
        h('div', { class: 'actions' },
          files ? null : h('button', { class: 'primary', disabled: !current, onclick: () => finish(current) }, t('picker.choose')),
          h('button', { onclick: () => finish(null) }, t('common.cancel'))),
      );
      goto.addEventListener('keydown', (e) => { if (e.key === 'Enter') show(goto.value.trim()); });
    }

    show(start).then(() => { if (!dialog.open) dialog.showModal(); });
  });
}
