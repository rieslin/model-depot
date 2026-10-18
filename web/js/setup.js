// The setup wizard, which is also the settings page: where the models are, the language, the civitai key, the
// network route. Nothing is saved until the button at the bottom is pressed.
import { api } from './api.js';
import { h, fill, formatBytes } from './dom.js';
import { t } from './i18n.js';
import { pickPath } from './picker.js';

const SOURCE_KEYS = { builtin: 'setup.source_builtin', extra: 'setup.source_extra', output: 'setup.source_output', unlisted: 'setup.source_unlisted',
  manual: 'setup.source_manual', saved: 'setup.source_saved' };
const INSTALLING = ['starting', 'downloading', 'extracting', 'verifying', 'configuring'];      // a download of the local model under way

export function renderSetup(root, ctx) {
  const cfg = ctx.state.config;
  const form = {
    language: ctx.lang,
    comfyDir: cfg.comfyui.dir || '',
    modelsDir: '',
    detection: null,
    folders: cfg.folders.map((f) => ({ path: f.path, comfy_type: f.comfy_type, enabled: f.enabled, source: 'saved' })),
    tokenSource: cfg.civitai.token_source,
    token: '',
    envFile: cfg.civitai.env_file || '',
    envVar: cfg.civitai.env_var || ctx.state.defaults.env_var,
    netMode: cfg.network.mode,
    proxy: cfg.network.proxy || '',
    readerMode: cfg.reader.mode,
    readerServer: cfg.reader.managed.server || '',
    readerModel: cfg.reader.managed.model_file || '',
    readerContext: cfg.reader.managed.context,
    readerArgs: cfg.reader.managed.args || '',
    endpointUrl: cfg.reader.endpoint.url || '',
    endpointModel: cfg.reader.endpoint.model || '',
    endpointKey: '',
  };
  const messages = { detect: null, add: null, env: null, save: null, probe: null, install: null };
  let reader = null;            // what /api/ui/reader says: the card, the recommendation, a download under way
  let readerTimer = null;
  let statusNode = null;        // the local model's state, refreshed in place while the page is open (no redraw of the form)

  async function fetchReader() {
    try {
      reader = await api('GET', '/api/ui/reader');
    } catch (e) {
      reader = null;
    }
  }

  // A download is followed every second (the whole page is drawn again); a model that is set up, every few seconds
  // (only its state line, so that nothing being typed is disturbed).
  function followReader() {
    if (readerTimer) clearTimeout(readerTimer);
    readerTimer = null;
    if (!root.isConnected || !reader) return;
    if (reader.install && INSTALLING.includes(reader.install.state)) readerTimer = setTimeout(loadReader, 1000);
    else if (reader.mode === 'managed' && reader.ready) readerTimer = setTimeout(refreshStatus, 4000);
  }

  async function loadReader() {
    await fetchReader();
    const install = reader && reader.install;
    if (install && install.state === 'done' && reader.managed) {
      form.readerMode = 'managed';
      form.readerServer = reader.managed.server;
      form.readerModel = reader.managed.model_file;
    }
    if (root.isConnected) draw();
    followReader();
  }

  async function refreshStatus() {
    await fetchReader();
    if (statusNode && statusNode.isConnected) fillStatus();
    followReader();
  }

  async function stopReader() {
    messages.install = null;
    try {
      reader = await api('POST', '/api/ui/reader/stop', {});
    } catch (e) {
      messages.install = { text: e.message, cls: 'bad' };
    }
    loadReader();
  }

  async function installModel() {
    messages.install = null;
    try {
      await api('POST', '/api/ui/reader/install', { model: reader && reader.recommended });
    } catch (e) {
      messages.install = { text: e.message, cls: 'bad' };
    }
    loadReader();
  }

  async function probe() {
    messages.probe = { text: t('setup.reading'), cls: 'dim' };
    draw();
    try {
      const r = await api('POST', '/api/ui/reader/probe', { url: form.endpointUrl, key: form.endpointKey });
      if (r.models.length && !form.endpointModel) form.endpointModel = r.models[0];
      messages.probe = { text: r.models.length ? t('setup.reader_probe_ok', { list: r.models.join(', ') }) : t('setup.reader_probe_none'),
        cls: r.models.length ? 'good' : 'warn' };
    } catch (e) {
      messages.probe = { text: e.message, cls: 'bad' };
    }
    draw();
  }

  const typeLabel = (name) => {
    const key = 'type.' + name;
    const text = t(key);
    return text === key ? t('type.other', { name }) : text;
  };

  function typeSelect(value, onChange) {
    const names = [...ctx.state.types];
    if (value && !names.includes(value)) names.push(value);
    const select = h('select', {
      onchange: () => {
        if (select.value === '__custom') {
          const name = (window.prompt(t('setup.custom_type_prompt'), value || '') || '').trim();
          onChange(name || value);
        } else onChange(select.value);
      },
    }, names.map((n) => h('option', { value: n, selected: n === value }, typeLabel(n))),
    h('option', { value: '__custom' }, t('setup.custom_type')));
    return select;
  }

  async function detect() {
    messages.detect = { text: t('setup.reading'), cls: 'dim' };
    draw();
    try {
      const result = await api('POST', '/api/ui/detect', { comfy_dir: form.comfyDir, models_dir: form.modelsDir });
      form.detection = result;
      if (!result.found && !result.models_dir) {
        messages.detect = { text: t('setup.not_comfy'), cls: 'bad' };
      } else {
        const before = new Map(form.folders.map((f) => [f.path.toLowerCase() + '|' + f.comfy_type, f]));
        const detected = result.folders.map((d) => {
          const old = before.get(d.path.toLowerCase() + '|' + d.comfy_type);
          return { ...d, enabled: old ? old.enabled : d.exists };
        });
        const keys = new Set(detected.map((d) => d.path.toLowerCase() + '|' + d.comfy_type));
        const kept = form.folders.filter((f) => !keys.has(f.path.toLowerCase() + '|' + f.comfy_type)
          && (f.source === 'manual' || f.source === 'saved'));
        form.folders = [...detected, ...kept];
        messages.detect = null;
      }
    } catch (e) {
      messages.detect = { text: e.message, cls: 'bad' };
    }
    draw();
  }

  async function addFolder(path, type) {
    if (!path) return;
    try {
      const info = await api('POST', '/api/ui/check-folder', { path });
      if (!info.exists) {
        messages.add = { text: t('setup.folder_missing', { path: info.path }), cls: 'bad' };
      } else {
        const comfyType = type || info.suggested_type || 'checkpoints';
        const dup = form.folders.some((f) => f.path.toLowerCase() === info.path.toLowerCase() && f.comfy_type === comfyType);
        if (!dup) form.folders.push({ path: info.path, comfy_type: comfyType, enabled: true, source: 'manual',
          exists: true, files: info.files, bytes: info.bytes });
        messages.add = dup ? { text: t('setup.folder_duplicate'), cls: 'warn' } : null;
      }
    } catch (e) {
      messages.add = { text: e.message, cls: 'bad' };
    }
    draw();
  }

  async function checkEnv() {
    try {
      const r = await api('POST', '/api/ui/check-env', { env_file: form.envFile, env_var: form.envVar });
      messages.env = r.ok ? { text: t('setup.env_ok'), cls: 'good' }
        : { text: t(r.problem === 'file_missing' ? 'setup.env_file_missing' : 'setup.env_var_missing'), cls: 'bad' };
    } catch (e) {
      messages.env = { text: e.message, cls: 'bad' };
    }
    draw();
  }

  async function save() {
    messages.save = { text: t('setup.saving'), cls: 'dim' };
    draw();
    try {
      await api('PUT', '/api/ui/config', {
        setup_done: true,
        language: form.language,
        comfyui: { dir: form.comfyDir, models_dir: form.detection ? form.detection.models_dir : cfg.comfyui.models_dir },
        folders: form.folders.map((f) => ({ path: f.path, comfy_type: f.comfy_type, enabled: !!f.enabled })),
        civitai: { token_source: form.tokenSource, token: form.token, env_file: form.envFile, env_var: form.envVar },
        network: { mode: form.netMode, proxy: form.proxy },
        reader: {
          mode: form.readerMode,
          managed: { server: form.readerServer, model_file: form.readerModel, context: Number(form.readerContext) || 16384,
            args: form.readerArgs },
          endpoint: { url: form.endpointUrl, model: form.endpointModel, key: form.endpointKey },
        },
      });
      messages.save = null;
      await ctx.onSaved();
    } catch (e) {
      const details = e.data && e.data.problems ? e.data.problems.map((p) => p.message).join(' ') : '';
      messages.save = { text: [e.message, details].filter(Boolean).join(' '), cls: 'bad' };
      draw();
    }
  }

  const note = (m) => (m ? h('p', { class: m.cls }, m.text) : null);
  const radio = (name, value, current, label, onPick) => h('label', null,
    h('input', { type: 'radio', name, checked: current === value, onchange: () => { onPick(value); draw(); } }), label);

  function folderTable() {
    if (!form.folders.length) return h('p', { class: 'dim' }, t('setup.no_folders'));
    return h('table', null,
      h('thead', null, h('tr', null,
        h('th', null, t('setup.col_use')), h('th', null, t('setup.col_folder')), h('th', null, t('setup.col_type')),
        h('th', { class: 'num' }, t('setup.col_files')), h('th', null, t('setup.col_source')), h('th', null, ''))),
      h('tbody', null, form.folders.map((f, i) => h('tr', null,
        h('td', null, h('input', { type: 'checkbox', checked: !!f.enabled, onchange: (e) => { f.enabled = e.target.checked; } })),
        h('td', { class: 'path' }, f.path, f.exists === false ? h('span', { class: 'warn' }, ' ' + t('setup.missing')) : null),
        h('td', null, typeSelect(f.comfy_type, (v) => { f.comfy_type = v; draw(); })),
        h('td', { class: 'num' }, f.files === undefined || f.files === null ? '' : f.files + ' · ' + formatBytes(f.bytes)),
        h('td', null, t(SOURCE_KEYS[f.source] || 'setup.source_saved')),
        h('td', null, h('button', { onclick: () => { form.folders.splice(i, 1); draw(); } }, t('setup.remove')))))));
  }

  function detectionNotes() {
    const d = form.detection;
    if (!d || (!d.found && !d.models_dir)) return null;
    const lines = [];
    const sourceKey = { default: 'setup.models_default', launcher: 'setup.models_launcher', manual: 'setup.models_manual' }[d.models_dir_source];
    lines.push(h('p', null, t('setup.models_dir_is', { path: d.models_dir }), ' ', h('span', { class: 'dim' }, t(sourceKey))));
    for (const hint of d.hints) {
      lines.push(h('p', { class: 'dim' }, t('setup.hint_line', { file: hint.file, option: hint.option, value: hint.value })));
    }
    for (const f of d.extra_files) {
      lines.push(h('p', { class: f.ok ? 'dim' : 'bad' },
        f.ok ? t('setup.extra_ok', { path: f.path, n: f.entries }) : t('setup.extra_bad', { path: f.path, error: f.error })));
    }
    return h('div', { class: 'note' }, lines);
  }

  // ---- the local model that reads the authors' notes -----------------------------------------------------------
  const INSTALL_ERRORS = { unsupported: 'setup.install_err_unsupported', no_release: 'setup.install_err_no_release',
    not_found: 'setup.install_err_not_found', network: 'setup.install_err_network', disk_full: 'setup.install_err_disk_full',
    checksum: 'setup.install_err_checksum', cancelled: 'setup.install_err_cancelled', settings: 'setup.install_err_settings',
    disk: 'setup.install_err_disk' };
  const INSTALL_STEPS = { starting: 'setup.install_starting', downloading: 'setup.install_downloading',
    extracting: 'setup.install_extracting', verifying: 'setup.install_verifying', configuring: 'setup.install_configuring' };

  function gb(mb) { return mb ? Math.round(mb / 1024) : 0; }

  function hardwareLine() {
    if (!reader) return null;
    const hw = reader.hardware || {};
    const gpu = (hw.gpus || [])[0];
    const ram = hw.ram_mb ? t('setup.reader_ram', { gb: gb(hw.ram_mb) }) : '';
    return h('p', null, gpu ? t('setup.reader_gpu', { name: gpu.name, gb: gb(gpu.vram_mb) }) + ram : t('setup.reader_no_gpu') + ram);
  }

  function tierTable() {
    if (!reader) return null;
    const rows = reader.tiers.map((tier) => {
      const m = reader.models[tier.model];
      return h('tr', { class: tier.model === reader.recommended ? 'chosen' : null },
        h('td', null, t('setup.reader_tier', { gb: tier.vram_gb })), h('td', null, m.label),
        h('td', { class: 'num' }, m.gb + ' GB'), h('td', { class: 'num' }, t('setup.reader_tier_ram', { gb: m.ram })));
    });
    return h('table', { class: 'tiers' },
      h('thead', null, h('tr', null, h('th', null, t('setup.reader_col_card')), h('th', null, t('setup.reader_col_model')),
        h('th', { class: 'num' }, t('setup.reader_col_size')), h('th', { class: 'num' }, t('setup.reader_col_ram')))),
      h('tbody', null, rows));
  }

  function idleText(seconds) {
    const s = Number(seconds) || 60;
    return s % 60 === 0 ? t('setup.idle_min', { n: s / 60 }) : t('setup.idle_sec', { n: s });
  }

  // The model in the settings is the one saved, and its files are there: its state instead of the download, so that
  // nobody downloads 15 GB again by mistake (the user's choice of the drafts, 2026-10-02).
  function modelInPlace() {
    return Boolean(reader && reader.mode === 'managed' && reader.ready && reader.managed
      && form.readerServer === reader.managed.server && form.readerModel === reader.managed.model_file);
  }

  function fillStatus() {
    const idle = idleText(reader.idle_stop);
    const parts = [];
    if (reader.install && reader.install.state === 'done') parts.push(h('p', { class: 'good' }, t('setup.install_done')));
    if (reader.server_running) {
      parts.push(h('p', null, t('setup.reader_running', { idle })),
        h('div', { class: 'actions' }, h('button', { onclick: stopReader }, t('setup.reader_stop_now'))));
    } else {
      parts.push(h('p', { class: 'good' }, t('setup.reader_ready')), h('p', { class: 'dim' }, t('setup.reader_ready_help', { idle })));
    }
    parts.push(note(messages.install));
    fill(statusNode, parts);
  }

  function statusBlock() {
    statusNode = h('div', { class: 'note', role: 'status' });
    fillStatus();
    return statusNode;
  }

  function installBlock() {
    if (!reader) return null;
    if (modelInPlace()) return statusBlock();
    const install = reader.install || {};
    const running = INSTALLING.includes(install.state);
    const model = reader.recommended && reader.models[reader.recommended];
    const parts = [h('p', { class: 'dim' }, t(reader.install_dir_default ? 'setup.reader_install_help' : 'setup.reader_install_help_custom'))];
    if (running) {
      const total = install.bytes_total;
      const share = total ? Math.floor((install.bytes_done / total) * 100) : null;
      parts.push(h('p', null, t(INSTALL_STEPS[install.state]), install.file ? ' · ' + install.file : '',
        share !== null ? ' · ' + share + '%' : '', install.speed ? ' · ' + formatBytes(install.speed) + '/s' : ''),
      h('div', { class: 'actions' }, h('button', { onclick: async () => { await api('POST', '/api/ui/reader/install/cancel', {}); loadReader(); } },
        t('common.cancel'))));
    } else {
      if (install.state === 'done') parts.push(h('p', { class: 'good' }, t('setup.install_done')));
      if (install.state === 'failed' || install.state === 'cancelled') {
        parts.push(h('p', { class: install.state === 'failed' ? 'bad' : 'warn' }, t(INSTALL_ERRORS[install.error] || 'setup.install_err_network'),
          install.detail ? h('span', { class: 'dim' }, ' (' + install.detail + ')') : null));
      }
      parts.push(h('div', { class: 'actions' }, h('button', { class: 'primary', disabled: !model, onclick: installModel },
        model ? t('setup.reader_install', { model: model.label, gb: model.gb }) : t('setup.reader_install_none'))));
    }
    parts.push(note(messages.install));
    return h('div', { class: 'note' }, parts);
  }

  function readerSection() {
    const rec = reader && reader.recommended && reader.models[reader.recommended];
    return [
      h('h2', null, t('setup.reader')),
      h('p', { class: 'dim' }, t('setup.reader_intro')),
      hardwareLine(),
      reader ? h('p', null, rec ? t('setup.reader_recommend', { model: rec.label })
        : h('span', { class: 'bad' }, t('setup.reader_recommend_none', { gb: reader.min_vram_gb }))) : null,
      tierTable(),
      h('div', { class: 'choices' },
        radio('reader', 'rules', form.readerMode, t('setup.reader_rules'), (v) => { form.readerMode = v; }),
        radio('reader', 'managed', form.readerMode, t('setup.reader_managed'), (v) => { form.readerMode = v; }),
        form.readerMode === 'managed' ? h('div', { class: 'indent' },
          h('div', { class: 'field' }, h('span', null, t('setup.reader_server')),
            h('input', { type: 'text', value: form.readerServer, placeholder: 'llama-server.exe', oninput: (e) => { form.readerServer = e.target.value; } }),
            h('button', { onclick: async () => { const p = await pickPath({ start: form.readerServer, files: true }); if (p) { form.readerServer = p; draw(); } } }, t('common.browse'))),
          h('div', { class: 'field' }, h('span', null, t('setup.reader_model_file')),
            h('input', { type: 'text', value: form.readerModel, placeholder: '*.gguf', oninput: (e) => { form.readerModel = e.target.value; } }),
            h('button', { onclick: async () => { const p = await pickPath({ start: form.readerModel, files: true }); if (p) { form.readerModel = p; draw(); } } }, t('common.browse'))),
          h('div', { class: 'field' }, h('span', null, t('setup.reader_context')),
            h('input', { type: 'number', min: 2048, max: 131072, step: 1024, value: form.readerContext, oninput: (e) => { form.readerContext = e.target.value; } })),
          h('div', { class: 'field' }, h('span', null, t('setup.reader_args')),
            h('input', { type: 'text', value: form.readerArgs, oninput: (e) => { form.readerArgs = e.target.value; } })),
          installBlock()) : null,
        radio('reader', 'endpoint', form.readerMode, t('setup.reader_endpoint'), (v) => { form.readerMode = v; }),
        form.readerMode === 'endpoint' ? h('div', { class: 'indent' },
          h('div', { class: 'field' }, h('span', null, t('setup.reader_url')),
            h('input', { type: 'text', value: form.endpointUrl, placeholder: 'http://127.0.0.1:8080/v1', oninput: (e) => { form.endpointUrl = e.target.value; } })),
          h('div', { class: 'field' }, h('span', null, t('setup.reader_model_name')),
            h('input', { type: 'text', value: form.endpointModel, oninput: (e) => { form.endpointModel = e.target.value; } }),
            h('button', { onclick: probe }, t('setup.check'))),
          h('div', { class: 'field' }, h('span', null, t('setup.reader_key')),
            h('input', { type: 'password', autocomplete: 'off', value: form.endpointKey,
              placeholder: cfg.reader.endpoint.has_key ? t('setup.token_kept') : t('setup.reader_key_placeholder'),
              oninput: (e) => { form.endpointKey = e.target.value; } })),
          note(messages.probe)) : null),
    ];
  }

  function draw() {
    const comfyInput = h('input', { type: 'text', value: form.comfyDir, placeholder: t('setup.comfy_placeholder'),
      oninput: (e) => { form.comfyDir = e.target.value; } });
    const modelsInput = h('input', { type: 'text', value: form.modelsDir, placeholder: t('setup.models_placeholder'),
      oninput: (e) => { form.modelsDir = e.target.value; } });
    const addInput = h('input', { type: 'text', placeholder: t('setup.add_placeholder') });
    let addType = '';
    const addSelect = h('select', { onchange: (e) => { addType = e.target.value; } },
      h('option', { value: '' }, t('setup.type_by_name')),
      ctx.state.types.map((n) => h('option', { value: n }, typeLabel(n))));

    fill(root,
      h('h1', null, ctx.firstRun ? t('setup.title_first') : t('setup.title')),
      ctx.firstRun ? h('p', null, t('setup.intro')) : null,
      ctx.state.config_problem ? h('p', { class: 'bad' }, t('setup.config_unreadable')) : null,

      h('h2', null, t('setup.language')),
      h('div', { class: 'field' },
        h('select', { onchange: (e) => { form.language = e.target.value; ctx.setLanguage(e.target.value); draw(); } },
          h('option', { value: 'zh', selected: form.language === 'zh' }, '中文'),
          h('option', { value: 'en', selected: form.language === 'en' }, 'English'))),

      h('h2', null, t('setup.models')),
      h('p', null, t('setup.models_intro')),
      h('h3', null, t('setup.comfy_heading')),
      h('p', { class: 'dim' }, t('setup.comfy_help')),
      h('div', { class: 'field' }, h('span', null, t('setup.comfy_dir')), comfyInput,
        h('button', { onclick: async () => { const p = await pickPath({ start: form.comfyDir }); if (p) { form.comfyDir = p; draw(); } } }, t('common.browse')),
        h('button', { class: 'primary', onclick: detect }, t('setup.read'))),
      h('div', { class: 'field' }, h('span', null, t('setup.models_dir')), modelsInput,
        h('button', { onclick: async () => { const p = await pickPath({ start: form.modelsDir || form.comfyDir }); if (p) { form.modelsDir = p; draw(); } } }, t('common.browse'))),
      h('p', { class: 'dim' }, t('setup.models_dir_help')),
      note(messages.detect),
      detectionNotes(),

      h('h3', null, t('setup.folders_heading')),
      h('p', { class: 'dim' }, t('setup.folders_help')),
      folderTable(),
      h('div', { class: 'field' }, h('span', null, t('setup.add_folder')), addInput,
        h('button', { onclick: async () => { const p = await pickPath({ start: addInput.value }); if (p) addInput.value = p; } }, t('common.browse')),
        addSelect,
        h('button', { onclick: () => addFolder(addInput.value.trim(), addType) }, t('setup.add'))),
      note(messages.add),

      h('h2', null, t('setup.civitai')),
      h('p', { class: 'dim' }, t('setup.civitai_help')),
      h('div', { class: 'choices' },
        radio('token', 'none', form.tokenSource, t('setup.token_none'), (v) => { form.tokenSource = v; }),
        radio('token', 'value', form.tokenSource, t('setup.token_value'), (v) => { form.tokenSource = v; }),
        form.tokenSource === 'value' ? h('div', { class: 'field indent' },
          h('input', { type: 'password', autocomplete: 'off', value: form.token,
            placeholder: cfg.civitai.has_token && cfg.civitai.token_source === 'value' ? t('setup.token_kept') : t('setup.token_placeholder'),
            oninput: (e) => { form.token = e.target.value; } })) : null,
        radio('token', 'env_file', form.tokenSource, t('setup.token_env'), (v) => { form.tokenSource = v; }),
        form.tokenSource === 'env_file' ? h('div', { class: 'indent' },
          h('div', { class: 'field' }, h('span', null, t('setup.env_file')),
            h('input', { type: 'text', value: form.envFile, oninput: (e) => { form.envFile = e.target.value; } }),
            h('button', { onclick: async () => { const p = await pickPath({ start: '', files: true }); if (p) { form.envFile = p; draw(); } } }, t('common.browse'))),
          h('div', { class: 'field' }, h('span', null, t('setup.env_var')),
            h('input', { type: 'text', value: form.envVar, oninput: (e) => { form.envVar = e.target.value; } }),
            h('button', { onclick: checkEnv }, t('setup.check'))),
          note(messages.env)) : null),

      h('h2', null, t('setup.network')),
      h('div', { class: 'choices' },
        radio('net', 'direct', form.netMode, t('setup.net_direct'), (v) => { form.netMode = v; }),
        radio('net', 'proxy', form.netMode, t('setup.net_proxy'), (v) => { form.netMode = v; }),
        form.netMode === 'proxy' ? h('div', { class: 'field indent' },
          h('input', { type: 'text', value: form.proxy, placeholder: 'http://127.0.0.1:7890',
            oninput: (e) => { form.proxy = e.target.value; } })) : null),

      ctx.firstRun ? null : readerSection(),

      // the end of the page: where the data is, and the buttons, under a rule without a heading (the user's choice,
      // 2026-10-03: a heading 「保存」 over a button 「保存」 said the same thing twice)
      h('div', { class: 'page-end' },
        h('p', { class: 'dim' }, t('setup.data_dir', { path: ctx.state.data_dir })),
        note(messages.save),
        h('div', { class: 'actions' },
          h('button', { class: 'primary', onclick: save }, ctx.firstRun ? t('setup.save_first') : t('setup.save')),
          ctx.firstRun ? null : h('button', { onclick: () => ctx.onCancel() }, t('common.cancel')))),
    );
  }

  draw();
  if (!ctx.firstRun) loadReader();
}
