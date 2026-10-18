// The setup's page: five steps to install (ComfyUI, where, models and data, installing, finished), three to remove. It asks
// the window (SetupForm.cs) for everything it needs over postMessage; ?demo shows it in a browser without the window,
// with sample answers.
'use strict';

const params = new URLSearchParams(location.search);
const DEMO = params.has('demo') || !(window.chrome && window.chrome.webview);
const UNINSTALL = params.has('uninstall');

// ---- words -------------------------------------------------------------------------------------------------------
const WORDS = {
  tagline: ['<span class="cjk">安装程序</span> · {v}', 'SETUP · {v}'],
  tagline_remove: ['<span class="cjk">卸载</span>', 'UNINSTALL'],
  meta: ['<span class="cjk">MODEL DEPOT · COMFYUI {c} · 本地模型 · 版本 {v}</span>', 'MODEL DEPOT · COMFYUI {c} · LOCAL MODEL · VERSION {v}'],
  meta_remove: ['<span class="cjk">MODEL DEPOT · COMFYUI · 本地模型运行程序</span>', 'MODEL DEPOT · COMFYUI · LOCAL MODEL RUNTIME'],
  steps: [['ComfyUI', '安装位置', '模型与数据', '安装', '完成'], ['ComfyUI', 'Location', 'Models & Data', 'Install', 'Finish']],
  steps_remove: [['确认', '卸载', '完成'], ['Confirm', 'Remove', 'Finish']],
  comfy_title: ['ComfyUI', 'ComfyUI'],
  comfy_lead: ['Model Depot 与 ComfyUI 共用同一个模型库。本机已装有 ComfyUI 时可直接沿用，不再另装一份。',
    'Model Depot and ComfyUI share one model library. A ComfyUI already on this computer can be used instead of installing another.'],
  comfy_existing: ['使用本机已有的 ComfyUI', 'Use the ComfyUI on this computer'],
  comfy_bundled: ['安装自带的 ComfyUI 便携版', 'Install the bundled portable ComfyUI'],
  comfy_bundled_note: ['ComfyUI {c}，含 {nodes} 个插件、{official} 个默认工作流与 {optional} 个可选工作流',
    'ComfyUI {c}, with {nodes} custom nodes, {official} default and {optional} optional workflows'],
  comfy_found_note: ['便携版 {c}', 'Portable {c}'],
  comfy_found_plain: ['便携版', 'Portable'],
  comfy_other: ['更改…', 'Change…'],
  comfy_none: ['未在本机找到 ComfyUI。', 'No ComfyUI was found on this computer.'],
  comfy_pick: ['选择文件夹…', 'Choose Folder…'],
  comfy_nodes_all: ['插件：{n} 个均已具备', 'Custom nodes: all {n} present'],
  comfy_nodes_some: ['插件：缺少 {m} 个（共 {n} 个）', 'Custom nodes: {m} of {n} missing'],
  comfy_older: ['版本较旧，部分默认工作流可能无法使用', 'An older version: some default workflows may not work'],
  comfy_library: ['模型库：沿用其模型文件夹', 'Model library: its models folder'],
  comfy_workflows: ['工作流：{k} 个放入「Model Depot」文件夹', 'Workflows: {k} into a "Model Depot" folder'],
  comfy_missing_note: ['工作流放入单独的文件夹，原有文件不变。缺少的插件可在安装完成后经 ComfyUI 管理器安装，补齐之前用到它们的功能无法使用。',
    'The workflows go into a folder of their own; nothing already there changes. The missing custom nodes can be installed afterwards with ComfyUI Manager; the features that use them do not work until then.'],
  nodes: {
    'ComfyUI-Crystools': ['资源监视与图像信息', 'Resource monitor and image details'],
    'ComfyUI-Impact-Pack': ['细节修复（人脸、手部）', 'Detail fixing (faces, hands)'],
    'ComfyUI-Impact-Subpack': ['细节修复所用的检测模型', 'Detection models for detail fixing'],
    'ComfyUI-KJNodes': ['常用辅助节点：尺寸、遮罩与批处理', 'Common helpers: sizes, masks, batches'],
    'ComfyUI-Krea2-Ostris-Edit': ['Krea 2 图像编辑', 'Krea 2 image editing'],
    'ComfyUI-PixAI-Tagger': ['图片标签读取（PixAI）', 'Image tags (PixAI)'],
    'ComfyUI_UltimateSDUpscale': ['分块放大', 'Tiled upscaling'],
    'ComfyUI_essentials': ['基础图像处理节点', 'Essential image nodes'],
    'comfyui_controlnet_aux': ['ControlNet 预处理（姿势、深度、线稿）', 'ControlNet preprocessors (pose, depth, lines)'],
    'rgthree-comfy': ['工作流整理与开关', 'Workflow tidying and switches'],
    'seedvr2_videoupscaler': ['SeedVR2 放大', 'SeedVR2 upscaling'],
    'was-node-suite-comfyui': ['通用图像与文本节点', 'General image and text nodes'],
  },
  where_title: ['安装位置', 'Installation folder'],
  where_lead: ['Model Depot、ComfyUI 与本地模型运行程序安装在同一文件夹中。路径中不宜包含中文或其他非英文字符。',
    'Model Depot, ComfyUI and the local model runtime are installed in one folder. Use a path with English characters only.'],
  where_lead_existing: ['Model Depot 与本地模型运行程序安装在同一文件夹中。路径中不宜包含中文或其他非英文字符。',
    'Model Depot and the local model runtime are installed in one folder. Use a path with English characters only.'],
  where_label: ['安装位置', 'Install to'],
  browse: ['浏览…', 'Browse…'],
  need: ['需要 {n}', '{n} required'],
  free: ['该磁盘可用 {n}', '{n} free on this drive'],
  gpu_ok: ['显卡：{name}，驱动 {driver}', 'Graphics: {name}, driver {driver}'],
  gpu_old: ['显卡：{name}，驱动 {driver}（需要 580 或更新）', 'Graphics: {name}, driver {driver} (580 or later needed)'],
  gpu_none: ['未检测到 NVIDIA 显卡', 'No NVIDIA graphics card found'],
  upgrade: ['此电脑已安装 Model Depot。程序文件将在原位置更新，设置、资料与模型保留。',
    'Model Depot is already installed on this computer. Its program files are updated in place; settings, records and models are kept.'],
  part_app: ['Model Depot {v}', 'Model Depot {v}'],
  part_app_note: ['模型库管理、civitai 下载与资料卡', 'Model library, civitai downloads and model cards'],
  part_comfy: ['ComfyUI {c}', 'ComfyUI {c}'],
  part_comfy_note: ['含 {nodes} 个插件、{official} 个默认工作流与 {optional} 个可选工作流',
    'With {nodes} custom nodes, {official} default workflows and {optional} optional ones'],
  part_comfy_existing: ['沿用 {p}；工作流放入其中的「Model Depot」文件夹', 'Uses {p}; the workflows go into its "Model Depot" folder'],
  part_llm: ['本地模型运行程序', 'Local model runtime'],
  part_llm_note: ['llama.cpp，用于读取作者说明', 'llama.cpp, for reading the authors\' notes'],
  part_tagger: ['PixAI 打标模型', 'PixAI Tagger'],
  data_title: ['模型与数据', 'Models and data'],
  data_lead: ['模型文件通常较大，可将模型库放在空间充足的磁盘上。', 'Model files are large: the model library can go on a drive with more room.'],
  library_label: ['模型库', 'Model library'],
  library_help: ['ComfyUI 与 Model Depot 均从此处读取模型；按 ComfyUI 的类型建立子文件夹。',
    'ComfyUI and Model Depot both read models from here; a sub-folder is made for each ComfyUI model type.'],
  library_fixed: ['沿用所选 ComfyUI 的模型文件夹；ComfyUI 与 Model Depot 均从此处读取模型。',
    'The chosen ComfyUI\'s models folder: ComfyUI and Model Depot both read models from here.'],
  data_label: ['数据文件夹', 'Data folder'],
  data_help: ['Model Depot 的设置、资料卡与封面。', 'Model Depot\'s settings, model cards and covers.'],
  inbox_some: ['安装包旁的 models 文件夹中有 <b>{n}</b> 个模型文件，共 <b>{size}</b>。首次打开 Model Depot 时列出整理清单，确认后移入模型库。',
    'The models folder next to this setup holds <b>{n}</b> model files, <b>{size}</b> in all. When Model Depot first opens, it lists where each will go and moves them into the library once confirmed.'],
  inbox_none: ['模型文件可放入安装位置的 models 文件夹。Model Depot 打开时列出整理清单，确认后移入模型库。',
    'Model files can be put into the models folder of the installation. When Model Depot opens, it lists where each will go and moves them into the library once confirmed.'],
  installing: ['正在安装', 'Installing'],
  installing_lead: ['安装期间无需联网。', 'No internet connection is needed.'],
  unpacking: ['正在安装 {part}', 'Installing {part}'],
  configuring: ['正在完成设置', 'Finishing the setup'],
  elapsed: ['已用时 {t}', 'Elapsed {t}'],
  left: ['剩余约 {t}', 'About {t} left'],
  part_names: { app: ['Model Depot', 'Model Depot'], python: ['Python', 'Python'], llm: ['本地模型运行程序', 'the local model runtime'],
    comfyui: ['ComfyUI', 'ComfyUI'], library: ['PixAI 打标模型', 'the PixAI Tagger'], workflows: ['工作流', 'the workflows'] },
  done_title: ['安装完成', 'Installation complete'],
  done_lead: ['已在桌面与开始菜单中创建 Model Depot 与 ComfyUI 的快捷方式。', 'Shortcuts to Model Depot and ComfyUI are on the desktop and in the Start menu.'],
  done_lead_existing: ['已在桌面与开始菜单中创建 Model Depot 的快捷方式。', 'Shortcuts to Model Depot are on the desktop and in the Start menu.'],
  open_now: ['立即打开 Model Depot', 'Open Model Depot now'],
  done_note: ['首次打开时依次完成设置（语言、civitai 密钥与网络）与模型整理。',
    'The first time it opens, Model Depot asks for its settings (language, civitai key and network), then sorts the models.'],
  failed_title: ['安装未完成', 'Installation did not finish'],
  failed_lead: ['已复制的文件已删除。安装包与 models 文件夹中的模型不受影响。', 'The files copied so far were removed. This setup and the models folder were not touched.'],
  cancelled_title: ['安装已取消', 'Installation cancelled'],
  cancel: ['取消', 'Cancel'], next: ['下一步', 'Next'], back: ['上一步', 'Back'], install: ['安装', 'Install'],
  finish: ['完成', 'Finish'], close: ['关闭', 'Close'], retry: ['返回', 'Back'],
  ask_old_title: ['显卡驱动版本较旧', 'The graphics driver is out of date'],
  ask_old: ['当前 NVIDIA 驱动为 {driver}。ComfyUI 与本地模型需要 580 或更新的版本，否则无法使用显卡。可先从 NVIDIA 官网更新驱动，再继续安装。',
    'The NVIDIA driver is version {driver}. ComfyUI and the local model need version 580 or later to use the graphics card. Update the driver from NVIDIA\'s website, then continue.'],
  ask_none_title: ['未检测到 NVIDIA 显卡', 'No NVIDIA graphics card found'],
  ask_none: ['ComfyUI 与本地模型需要 NVIDIA 显卡（RTX 20 系列或更新）。没有 NVIDIA 显卡时，Model Depot 的模型库管理与下载仍可使用。',
    'ComfyUI and the local model need an NVIDIA graphics card (RTX 20 series or later). Without one, Model Depot\'s library and downloads still work.'],
  quit: ['退出安装', 'Quit Setup'], anyway: ['仍然安装', 'Install Anyway'],
  ask_cancel_title: ['取消安装', 'Cancel installation'],
  ask_cancel: ['已复制的文件将删除。安装包与 models 文件夹中的模型不受影响。', 'The files copied so far are removed. This setup and the models folder are not touched.'],
  keep_going: ['继续安装', 'Continue'], stop_install: ['取消安装', 'Cancel Installation'],
  problem: {
    bad_path: ['路径无效。', 'This path is not valid.'],
    protected: ['不能安装到 Windows 或 Program Files 文件夹。', 'Windows and Program Files cannot be used.'],
    not_writable: ['该位置无法写入。', 'This place cannot be written to.'],
    no_room: ['磁盘空间不足：需要 {need}，可用 {free}。', 'Not enough room: {need} needed, {free} free.'],
    non_ascii: ['路径包含中文或其他非英文字符，部分 ComfyUI 插件可能无法正常工作。', 'The path has non-English characters; some ComfyUI custom nodes may not work with it.'],
    comfyui_running: ['ComfyUI 正在运行。请先关闭 ComfyUI 的窗口，再继续。', 'ComfyUI is running. Close its window, then continue.'],
    no_payload: ['安装包不完整：缺少 setup 文件夹中的文件。', 'This setup is incomplete: files are missing from its setup folder.'],
    unpack_failed: ['解压失败：{detail}', 'Unpacking failed: {detail}'],
    update_incomplete: ['更新未完成。再次运行安装程序即可完成更新，设置、资料与模型不受影响。', 'The update did not finish. Run the setup again to complete it; settings, records and models are not affected.'],
    not_installed: ['未找到要卸载的程序。', 'The installation to remove was not found.'],
    not_comfyui: ['所选文件夹不是 ComfyUI 便携版。', 'The chosen folder is not a portable ComfyUI.'],
    failed: ['出现错误：{detail}', 'An error occurred: {detail}'],
  },
  remove_title: ['卸载 Model Depot', 'Uninstall Model Depot'],
  remove_lead: ['将删除 Model Depot、ComfyUI 与本地模型运行程序的程序文件。以下内容保留，如不再需要可手动删除：',
    'The program files of Model Depot, ComfyUI and the local model runtime are removed. These are kept; delete them by hand if they are no longer wanted:'],
  remove_lead_existing: ['将删除 Model Depot 与本地模型运行程序的程序文件。以下内容保留，如不再需要可手动删除：',
    'The program files of Model Depot and the local model runtime are removed. These are kept; delete them by hand if they are no longer wanted:'],
  kept_library: ['模型库', 'Model library'], kept_data: ['数据文件夹', 'Data folder'], kept_inbox: ['待整理文件夹', 'Inbox folder'],
  kept_comfy: ['工作流与出图', 'Workflows, images'],
  kept_comfy_path: ['{p} 中的 user、output、input', 'user, output and input in {p}'],
  removing: ['正在卸载', 'Uninstalling'],
  uninstall: ['卸载', 'Uninstall'],
  removed_title: ['卸载完成', 'Uninstalled'],
  removed_lead: ['程序文件已删除，模型与资料保留在原处。', 'The program files are removed; the models and records stay where they are.'],
  picker_title: ['选择文件夹', 'Choose a folder'], picker_path: ['输入路径', 'Enter a path'], picker_go: ['转到', 'Go'],
  picker_up: ['上一级', 'Up'], picker_drives: ['此电脑', 'This computer'], picker_empty: ['没有子文件夹。', 'No subfolders.'],
  picker_choose: ['选择此文件夹', 'Choose this folder'],
};

let LANG = 0;               // 0 Chinese, 1 English
function t(key, vars) {
  let row = key.split('.').reduce((o, k) => (o ? o[k] : undefined), WORDS);
  let text = Array.isArray(row) ? row[LANG] : key;
  if (vars) text = String(text).replace(/\{(\w+)\}/g, (m, k) => (vars[k] !== undefined ? vars[k] : m));
  return text;
}
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
// a sentence with markup of its own (bold numbers) and values that are text
function th(key, vars) {
  const safe = {};
  for (const k in vars || {}) safe[k] = esc(vars[k]);
  return t(key, safe);
}

// ---- a small builder, as Model Depot's dom.js ------------------------------------------------------------------
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'html') el.innerHTML = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) el.append(c.nodeType ? c : String(c));
  return el;
}
function fill(el, ...children) { el.replaceChildren(); for (const c of children.flat()) if (c) el.append(c); return el; }
function gb(n) {
  if (n >= 1e9) return (n / 1024 ** 3).toFixed(1) + ' GB';
  if (n >= 1e6) return Math.round(n / 1024 ** 2) + ' MB';
  return Math.max(1, Math.round(n / 1024)) + ' KB';
}
function clock(s) {
  s = Math.max(0, Math.round(s));
  return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
}

// ---- talking to the window ---------------------------------------------------------------------------------------
const waiting = new Map();
let seq = 0;
let onEvent = () => {};
function call(cmd, args) {
  if (DEMO) return Demo.call(cmd, args || {});
  return new Promise((resolve, reject) => {
    const id = ++seq;
    waiting.set(id, { resolve, reject });
    window.chrome.webview.postMessage(Object.assign({ id, cmd }, args || {}));
  });
}
if (!DEMO) {
  window.chrome.webview.addEventListener('message', (e) => {
    const m = e.data;
    if (m && m.id && waiting.has(m.id)) {
      const w = waiting.get(m.id);
      waiting.delete(m.id);
      if (m.ok) w.resolve(m.result);
      else w.reject(Object.assign(new Error(m.code), { code: m.code, detail: m.detail }));
    } else if (m && m.event) {
      onEvent(m);
    }
  });
}

// ---- the frame ---------------------------------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);
let info = null;
let busy = false;           // an install or a removal is under way
const form = { install: '', library: '', data: '', comfy: '', libraryEdited: false, dataEdited: false };   // comfy: an existing ComfyUI, or '' for the bundled one
let checked = null;         // the window's answer about the places chosen

function frame(step) {
  const names = t(UNINSTALL ? 'steps_remove' : 'steps');
  $('tagline').innerHTML = UNINSTALL ? t('tagline_remove') : th('tagline', { v: info.version });
  $('head-title').textContent = UNINSTALL ? 'UNINSTALL' : 'SETUP';
  $('head-meta').innerHTML = UNINSTALL ? t('meta_remove') : th('meta', { v: info.version, c: info.comfyui });
  if (!UNINSTALL && step !== 3) $('head-count').textContent = String(step + 1).padStart(2, '0') + '/' + String(names.length).padStart(2, '0');
  if (UNINSTALL) $('head-count').textContent = '';
  fill($('steps'), names.map((name, i) => h('span', { class: 'type-tab' + (i < step ? ' done' : ''), 'aria-pressed': String(i === step) },
    name, h('span', { class: 'n' }, String(i + 1).padStart(2, '0')))));
}
function body(...nodes) { fill($('body'), ...nodes); }
function actions(...nodes) { fill($('actions'), ...nodes); }
const btn = (label, kind, onclick, attrs) => h('button', Object.assign({ class: 'btn btn-' + kind, type: 'button', onclick }, attrs || {}), label);
const grow = () => h('span', { class: 'su-grow' });

// A question in the delete dialog's form; resolves true for the second (solid) button.
function ask(title, lead, no, yes) {
  const d = $('ask');
  return new Promise((resolve) => {
    const done = (v) => { d.close(); resolve(v); };
    d.oncancel = (e) => { e.preventDefault(); done(false); };
    fill(d, h('h2', null, title), h('p', { class: 'confirm-lead' }, lead),
      h('div', { class: 'confirm-actions' }, btn(no, 'line', () => done(false), { autofocus: true }), btn(yes, 'solid', () => done(true))));
    d.showModal();
  });
}

function closeWindow() { call('window', { action: 'close' }); }
$('btn-min').onclick = () => call('window', { action: 'min' });
$('btn-close').onclick = () => onEvent({ event: 'close-request' });

// ---- the folder picker, as Model Depot's ---------------------------------------------------------------------------
function pickFolder(start) {
  const d = $('picker');
  return new Promise((resolve) => {
    const finish = (v) => { d.close(); resolve(v); };
    d.oncancel = () => resolve(null);
    async function show(path) {
      let data;
      try { data = await call('list', { path }); } catch (e) { data = { path: '', parent: null, dirs: [] }; }
      const goto = h('input', { type: 'text', value: data.path, placeholder: t('picker_path') });
      goto.addEventListener('keydown', (e) => { if (e.key === 'Enter') show(goto.value.trim()); });
      fill(d,
        h('h1', null, t('picker_title')),
        h('div', { class: 'field' }, goto, h('button', { type: 'button', onclick: () => show(goto.value.trim()) }, t('picker_go'))),
        h('div', { class: 'bar' },
          data.path ? h('button', { type: 'button', onclick: () => show(data.parent || '') }, t('picker_up')) : null,
          h('span', { class: 'dim' }, data.path || t('picker_drives'))),
        h('ul', null, data.dirs.map((dir) => h('li', null, h('button', { type: 'button', class: 'dir', onclick: () => show(dir.path) },
          h('svg', { class: 'i', html: '<use href="#i-folder"/>' }), dir.name))),
          data.dirs.length ? null : h('li', { class: 'dim' }, t('picker_empty'))),
        h('div', { class: 'actions' },
          h('button', { type: 'button', class: 'primary', disabled: !data.path, onclick: () => finish(data.path) }, t('picker_choose')),
          h('button', { type: 'button', onclick: () => finish(null) }, t('cancel'))));
      if (!d.open) d.showModal();
    }
    // start where the field points, or at its nearest parent that exists (the window lists drives otherwise)
    const parent = start ? start.replace(/[\\/][^\\/]*$/, '') || start : '';
    show(/^[A-Za-z]:$/.test(parent) ? parent + '\\' : parent);
  });
}

const APP_FOLDER = 'Model Depot';

// The installation goes into a folder of its own: a folder picked for it gets "Model Depot" inside it, unless it is one.
function ownFolder(path) {
  const trimmed = path.replace(/[\\/]+$/, '');
  return /(^|[\\/])model depot$/i.test(trimmed) ? trimmed : trimmed + '\\' + APP_FOLDER;
}

function field(label, key, help, onPick) {
  const input = h('input', { type: 'text', value: form[key], spellcheck: 'false' });
  input.addEventListener('input', () => { form[key] = input.value; if (onPick) onPick(); recheck(); });
  const pick = btn(t('browse'), 'line', async () => {
    let chosen = await pickFolder(form[key]);
    if (chosen && key === 'install') chosen = ownFolder(chosen);
    if (chosen) { form[key] = chosen; input.value = chosen; if (onPick) onPick(); recheck(); }
  });
  return h('div', { class: 'su-field', 'data-field': key }, h('label', null, label), h('div', { class: 'su-row' }, input, pick),
    help ? h('p', { class: 'su-help' }, help) : null, h('p', { class: 'su-help bad', hidden: true }), h('p', { class: 'su-help warn', hidden: true }));
}

// ---- installing ----------------------------------------------------------------------------------------------------
let recheckTimer = null;
let checkSeq = 0;
function recheck() {
  clearTimeout(recheckTimer);
  recheckTimer = setTimeout(async () => {
    const mine = ++checkSeq;
    let answer = null;
    try { answer = await call('check', { install: form.install, library: form.library, data: form.data, comfy: form.comfy }); } catch (e) { answer = null; }
    if (mine !== checkSeq) return;
    checked = answer;
    showProblems();
  }, 250);
}

function showProblems() {
  document.querySelectorAll('.su-field').forEach((el) => {
    const key = el.getAttribute('data-field');
    const [bad, warn] = el.querySelectorAll('.su-help.bad, .su-help.warn');
    const mine = (list) => (checked ? checked[list].filter((p) => p.field === key) : []);
    const say = (p) => t('problem.' + p.code, { need: gb(p.need || 0), free: gb(p.free || 0) });
    bad.textContent = mine('problems').map(say).join(' ');
    bad.hidden = !bad.textContent;
    warn.textContent = mine('warnings').map(say).join(' ');
    warn.hidden = !warn.textContent;
  });
  const facts = document.getElementById('facts-free');
  if (facts && checked) facts.textContent = t('free', { n: gb(checked.free) });
  const next = document.getElementById('btn-next');
  if (next) next.disabled = !checked || checked.problems.some((p) => currentFields.includes(p.field));
}
let currentFields = [];     // the fields of the step shown: a problem in one of them holds its button

function partBytes(...names) { return info.parts.filter((p) => names.includes(p.name)).reduce((n, p) => n + p.bytes, 0); }

function gpuLine() {
  const g = info.gpu;
  if (!g.name) return h('li', { class: 'warn' }, t('gpu_none'));
  return h('li', { class: g.ok ? 'good' : 'warn' }, t(g.ok ? 'gpu_ok' : 'gpu_old', { name: g.name, driver: g.driver }));
}

// ---- ComfyUI: the one already on this computer, or the bundled one ---------------------------------------------------
let comfyFound = null;      // the existing ComfyUI the first choice shows (found, or picked by hand)
let comfyProblem = '';      // the folder picked by hand was not one
function chooseExisting(found) { comfyFound = found; form.comfy = found.path; form.library = found.models; }
function chooseBundled() {
  form.comfy = '';
  if (!form.libraryEdited) form.library = form.install.replace(/[\\/]+$/, '') + '\\ComfyUI\\ComfyUI\\models';
}

async function pickComfy(e) {
  e.stopPropagation();
  const chosen = await pickFolder(comfyFound ? comfyFound.path + '\\' : '');
  if (!chosen) return;
  try {
    chooseExisting(await call('comfy', { path: chosen }));
    comfyProblem = '';
  } catch (err) {
    comfyProblem = t('problem.' + (err.code === 'not_comfyui' ? 'not_comfyui' : 'failed'), { detail: err.detail || err.code });
  }
  stepComfy();
}

function choiceRow(on, off, title, note, end, onpick) {
  const row = h('div', { class: 'choice' + (on ? ' on' : '') + (off ? ' off' : ''), role: 'radio', 'aria-checked': String(on),
    'aria-disabled': off ? 'true' : null, tabindex: off ? '-1' : '0' },
  h('span', { class: 'box' }), h('span', null, h('span', { class: 'ch-title' }, title), note ? h('small', { class: 'ch-note' }, note) : null), end);
  if (!off) {
    row.addEventListener('click', onpick);
    row.addEventListener('keydown', (e) => { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); onpick(); } });
  }
  return row;
}

function stepComfy() {
  frame(0);
  currentFields = [];
  const counts = info.counts || {};
  const f = comfyFound;
  const existing = !!form.comfy;
  const pick = btn(f ? t('comfy_other') : t('comfy_pick'), 'line', pickComfy, { class: 'btn btn-line ch-btn' });
  const note = f ? f.path + ' · ' + (f.version ? t('comfy_found_note', { c: f.version }) : t('comfy_found_plain')) : t('comfy_none');
  const missing = f ? f.missing || [] : [];
  const detail = existing && f ? h('div', { class: 'ch-detail' },
    h('ul', { class: 'facts' },
      h('li', { class: missing.length ? 'warn' : 'good' }, missing.length ? t('comfy_nodes_some', { n: f.nodes, m: missing.length }) : t('comfy_nodes_all', { n: f.nodes })),
      f.older ? h('li', { class: 'warn' }, t('comfy_older')) : null,
      h('li', null, t('comfy_library')),
      h('li', null, t('comfy_workflows', { k: (counts.official || 0) + (counts.optional || 0) }))),
    missing.length ? h('div', { class: 'ch-missing' },
      h('ul', { class: 'su-list' }, missing.map((name) => h('li', null, h('span', null, name), h('small', null, WORDS.nodes[name] ? t('nodes.' + name) : '')))),
      h('p', { class: 'su-note' }, t('comfy_missing_note'))) : null) : null;
  body(
    h('h2', null, t('comfy_title')),
    h('p', { class: 'su-lead' }, t('comfy_lead')),
    h('div', { class: 'choices ch-choices' },
      h('div', { class: 'ch-group' },
        choiceRow(existing, !f, t('comfy_existing'), note, pick, () => { chooseExisting(f); stepComfy(); }),
        comfyProblem ? h('p', { class: 'su-help bad ch-problem' }, comfyProblem) : null,
        detail),
      choiceRow(!existing, false, t('comfy_bundled'),
        t('comfy_bundled_note', { c: info.comfyui, nodes: counts.custom_nodes || 0, official: counts.official || 0, optional: counts.optional || 0 }),
        h('span', { class: 'size' }, gb(partBytes('comfyui', 'workflows'))), () => { chooseBundled(); stepComfy(); })));
  actions(grow(), btn(t('cancel'), 'line', closeWindow), btn(t('next'), 'solid', () => stepWhere(), { id: 'btn-next', disabled: existing && !f }));
}

function stepWhere() {
  frame(1);
  currentFields = ['install'];
  const counts = info.counts || {};
  body(
    h('h2', null, t('where_title')),
    h('p', { class: 'su-lead' }, t(form.comfy ? 'where_lead_existing' : 'where_lead')),
    field(t('where_label'), 'install', null, () => {
      const root = form.install.replace(/[\\/]+$/, '');
      if (!form.libraryEdited && !form.comfy) form.library = root + '\\ComfyUI\\ComfyUI\\models';
      if (!form.dataEdited) form.data = root + '\\data';
    }),
    h('ul', { class: 'facts' }, h('li', null, t('need', { n: gb(info.bytes - (form.comfy ? partBytes('comfyui') : 0)) })), h('li', { id: 'facts-free' }, ''), gpuLine()),
    info.upgrade ? h('p', { class: 'su-note' }, t('upgrade')) : null,
    h('ul', { class: 'su-list' },
      h('li', null, h('span', null, t('part_app', { v: info.version }), h('small', null, t('part_app_note'))), h('span', { class: 'size' }, gb(partBytes('app', 'python')))),
      form.comfy
        ? h('li', null, h('span', null, 'ComfyUI', h('small', null, t('part_comfy_existing', { p: form.comfy }))), h('span', { class: 'size' }, gb(partBytes('workflows'))))
        : h('li', null, h('span', null, t('part_comfy', { c: info.comfyui }), h('small', null, t('part_comfy_note', { nodes: counts.custom_nodes || 0, official: counts.official || 0, optional: counts.optional || 0 }))),
          h('span', { class: 'size' }, gb(partBytes('comfyui', 'workflows')))),
      h('li', null, h('span', null, t('part_llm'), h('small', null, t('part_llm_note'))), h('span', { class: 'size' }, gb(partBytes('llm')))),
      partBytes('library') ? h('li', null, h('span', null, t('part_tagger'), h('small', null, 'pixai-tagger-v1.0')), h('span', { class: 'size' }, gb(partBytes('library')))) : null));
  actions(btn(t('back'), 'line', () => stepComfy()), grow(), btn(t('cancel'), 'line', closeWindow), btn(t('next'), 'solid', () => stepData(), { id: 'btn-next', disabled: true }));
  recheck();
}

// The library of an existing ComfyUI is where that ComfyUI reads its models: shown, not changed here.
function fixedField(label, key, help) {
  return h('div', { class: 'su-field', 'data-field': key }, h('label', null, label),
    h('div', { class: 'su-row' }, h('input', { type: 'text', value: form[key], readonly: true, class: 'fixed' })),
    h('p', { class: 'su-help' }, help), h('p', { class: 'su-help bad', hidden: true }), h('p', { class: 'su-help warn', hidden: true }));
}

function stepData() {
  frame(2);
  currentFields = ['install', 'library', 'data'];
  const inbox = info.inbox || { files: 0 };
  body(
    h('h2', null, t('data_title')),
    h('p', { class: 'su-lead' }, t('data_lead')),
    form.comfy ? fixedField(t('library_label'), 'library', t('library_fixed'))
      : field(t('library_label'), 'library', t('library_help'), () => { form.libraryEdited = true; }),
    field(t('data_label'), 'data', t('data_help'), () => { form.dataEdited = true; }),
    h('p', { class: 'su-note', html: inbox.files ? th('inbox_some', { n: inbox.files, size: gb(inbox.bytes) }) : t('inbox_none') }));
  actions(btn(t('back'), 'line', () => stepWhere()), grow(), btn(t('cancel'), 'line', closeWindow),
    btn(t('install'), 'solid', () => startInstall(), { id: 'btn-next', disabled: true }));
  recheck();
}

async function startInstall() {
  const g = info.gpu;
  if (!g.ok) {
    const go = g.name ? await ask(t('ask_old_title'), t('ask_old', { driver: g.driver }), t('quit'), t('anyway'))
      : await ask(t('ask_none_title'), t('ask_none'), t('quit'), t('anyway'));
    if (!go) { closeWindow(); return; }
  }
  stepInstalling();
  busy = true;
  try {
    await call('install', { install: form.install, library: form.library, data: form.data, comfy: form.comfy, lang: LANG ? 'en' : 'zh' });
  } catch (e) {
    busy = false;
    stepFailed(e.code, e.detail);
  }
}

function stepInstalling() {
  frame(3);
  $('head-count').textContent = '0%';
  const now = h('span', null, t('unpacking', { part: 'Model Depot' }));
  const pct = h('span', { class: 'n' }, '0%');
  const bar = h('i');
  const done = h('li', null, '0 / ' + gb(info.bytes));
  const took = h('li', null, t('elapsed', { t: '00:00' }));
  const left = h('li', null, '');
  body(h('h2', null, t('installing')), h('p', { class: 'su-lead' }, t('installing_lead')),
    h('div', { class: 'su-now' }, now, pct), h('div', { class: 'su-gauge' }, bar), h('ul', { class: 'facts' }, done, took, left));
  actions(grow(), btn(t('cancel'), 'line', () => onEvent({ event: 'close-request' })));
  onProgress = (m) => {
    const share = m.total ? m.done / m.total : 0;
    const p = Math.min(100, Math.floor(share * 100));
    $('head-count').textContent = p + '%';
    pct.textContent = p + '%';
    bar.style.width = (share * 100).toFixed(1) + '%';
    now.textContent = m.stage === 'configure' ? t('configuring') : t('unpacking', { part: t('part_names.' + m.part) });
    done.textContent = gb(m.done) + ' / ' + gb(m.total);
    took.textContent = t('elapsed', { t: clock(m.elapsed) });
    left.textContent = share > 0.03 && share < 1 ? t('left', { t: clock(m.elapsed / share - m.elapsed) }) : '';
    left.hidden = !left.textContent;
  };
}
let onProgress = () => {};

function stepDone() {
  frame(4);
  const open = h('input', { type: 'checkbox', checked: true });
  body(h('h2', null, t('done_title')), h('p', { class: 'su-lead' }, t(form.comfy ? 'done_lead_existing' : 'done_lead')),
    h('label', { class: 'su-tick opt-check' }, open, t('open_now')),
    h('p', { class: 'su-note', style: 'margin-top:18px' }, t('done_note')));
  actions(grow(), btn(t('finish'), 'solid', async () => {
    if (open.checked) await call('open', { install: checked ? checked.install : form.install });
    closeWindow();
  }));
}

function stepFailed(code, detail, cancelled) {
  frame(3);
  $('head-count').textContent = '';
  body(h('h2', null, t(cancelled ? 'cancelled_title' : 'failed_title')),
    cancelled ? null : h('p', { class: 'su-note bad' }, t('problem.' + (WORDS.problem[code] ? code : 'failed'), { detail: detail || code })),
    code === 'update_incomplete' ? null : h('p', { class: 'su-lead', style: 'margin-top:12px' }, t('failed_lead')));
  actions(btn(t('retry'), 'line', () => stepWhere()), grow(), btn(t('close'), 'solid', closeWindow));
}

// ---- removing ------------------------------------------------------------------------------------------------------
function stepRemove() {
  frame(0);
  const k = info.kept;
  const row = (label, path) => h('li', null, h('span', { class: 'what' }, label), h('span', { class: 'path' }, path));
  body(h('h2', null, t('remove_title')), h('p', { class: 'su-lead' }, t(k.comfy_external ? 'remove_lead_existing' : 'remove_lead')),
    h('ul', { class: 'su-list kept' }, row(t('kept_library'), k.library), row(t('kept_data'), k.data), row(t('kept_inbox'), k.inbox),
      k.comfy_external ? row('ComfyUI', k.comfy_external) : row(t('kept_comfy'), t('kept_comfy_path', { p: k.comfy_user }))));
  actions(grow(), btn(t('cancel'), 'line', closeWindow), btn(t('uninstall'), 'solid', async () => {
    stepRemoving();
    busy = true;
    try { await call('uninstall'); } catch (e) { busy = false; stepRemoveFailed(e.code, e.detail); }
  }));
}

function stepRemoving() {
  frame(1);
  const bar = h('i');
  body(h('h2', null, t('removing')), h('div', { class: 'su-gauge', style: 'margin-top:14px' }, bar));
  actions();
  onProgress = (m) => { bar.style.width = (m.total ? 100 * m.done / m.total : 0) + '%'; };
}

function stepRemoved() {
  frame(2);
  body(h('h2', null, t('removed_title')), h('p', { class: 'su-lead' }, t('removed_lead')));
  actions(grow(), btn(t('finish'), 'solid', closeWindow));
}

function stepRemoveFailed(code, detail) {
  frame(1);
  body(h('h2', null, t('remove_title')), h('p', { class: 'su-note bad' }, t('problem.' + (WORDS.problem[code] ? code : 'failed'), { detail: detail || code })));
  actions(grow(), btn(t('close'), 'line', closeWindow), btn(t('uninstall'), 'solid', () => stepRemove()));
}

// ---- events from the window ------------------------------------------------------------------------------------------
onEvent = async (m) => {
  if (m.event === 'progress') onProgress(m);
  else if (m.event === 'done') { busy = false; if (UNINSTALL) stepRemoved(); else stepDone(); }
  else if (m.event === 'cancelled') { busy = false; stepFailed('', '', true); }
  else if (m.event === 'failed') { busy = false; if (UNINSTALL) stepRemoveFailed(m.code, m.detail); else stepFailed(m.code, m.detail); }
  else if (m.event === 'close-request') {
    if (!busy) { closeWindow(); return; }
    if (UNINSTALL) return;                  // removal runs to its end
    if (await ask(t('ask_cancel_title'), t('ask_cancel'), t('keep_going'), t('stop_install'))) call('cancel');
  }
};

// ---- start -----------------------------------------------------------------------------------------------------------
async function start() {
  info = await call('info');
  LANG = info.lang === 'en' ? 1 : 0;
  if (params.get('lang')) LANG = params.get('lang') === 'en' ? 1 : 0;
  document.documentElement.lang = LANG ? 'en' : 'zh-CN';
  if (UNINSTALL) { stepRemove(); return; }
  Object.assign(form, info.defaults);
  // an existing ComfyUI is offered first; an update keeps the choice the installation made
  const c = info.comfy || { found: [], own: true };
  if (c.found.length && (!info.upgrade || !c.own)) chooseExisting(c.found[0]);
  else { comfyFound = c.found[0] || null; form.comfy = ''; }
  const step = params.get('step');
  if (DEMO && step) Demo.show(step);
  else stepComfy();
}

// ---- the sample answers of ?demo ----------------------------------------------------------------------------------
const Demo = {
  info: {
    mode: 'install', lang: 'zh', version: '0.6.1', comfyui: '0.37.0', upgrade: false,
    parts: [{ name: 'app', bytes: 52e6 }, { name: 'python', bytes: 40e6 }, { name: 'llm', bytes: 700e6 }, { name: 'comfyui', bytes: 6.2e9 },
      { name: 'library', bytes: 1.95e9 }, { name: 'workflows', bytes: 1e6 }],
    bytes: 8.94e9, counts: { custom_nodes: 12, official: 7, optional: 20 },
    comfy: { own: true, found: [{ path: 'D:\\ComfyUI_windows_portable', version: '0.37.0', older: false,
      models: 'D:\\ComfyUI_windows_portable\\ComfyUI\\models', nodes: 12, missing: [] }] },
    defaults: { install: 'D:\\Model Depot', library: 'D:\\Model Depot\\ComfyUI\\ComfyUI\\models', data: 'D:\\Model Depot\\data' },
    gpu: { name: 'NVIDIA GeForce RTX 5070 Ti', driver: '616.92', ok: true, problem: '' },
    inbox: { path: 'E:\\Model Depot Setup\\models', files: 6, bytes: 41.0 * 1024 ** 3 },
  },
  call(cmd, args) {
    if (cmd === 'info') return Promise.resolve(UNINSTALL ? { mode: 'uninstall', lang: 'zh', version: '0.6.1', install: 'D:\\Model Depot',
      kept: { library: 'D:\\Model Depot\\ComfyUI\\ComfyUI\\models', data: 'D:\\Model Depot\\data', inbox: 'D:\\Model Depot\\models',
        comfy_user: 'D:\\Model Depot\\ComfyUI\\ComfyUI' } } : this.info);
    if (cmd === 'check') {
      const warn = /[^\x00-\x7f]/.test(args.install) ? [{ field: 'install', code: 'non_ascii' }] : [];
      return Promise.resolve({ install: args.install, library: args.library, data: args.data, need: this.info.bytes, free: 193 * 1024 ** 3,
        problems: [], warnings: warn });
    }
    if (cmd === 'comfy') {
      if (!/comfy/i.test(args.path)) return Promise.reject(Object.assign(new Error('not_comfyui'), { code: 'not_comfyui', detail: args.path }));
      return Promise.resolve({ path: args.path.replace(/[\\/]+$/, ''), version: '0.36.2', older: true, models: args.path.replace(/[\\/]+$/, '') + '\\ComfyUI\\models',
        nodes: 12, missing: ['ComfyUI-KJNodes', 'ComfyUI-PixAI-Tagger', 'seedvr2_videoupscaler'] });
    }
    if (cmd === 'list') return Promise.resolve({ path: args.path || '', parent: '', dirs: [{ name: 'Model Depot', path: 'D:\\Model Depot' }, { name: 'Games', path: 'D:\\Games' }] });
    return Promise.resolve(true);
  },
  show(step) {
    if (step === 'comfy-missing') { chooseExisting({ path: 'E:\\AI\\ComfyUI_windows_portable', version: '0.36.2', older: true,
      models: 'E:\\AI\\ComfyUI_windows_portable\\ComfyUI\\models', nodes: 12, missing: ['ComfyUI-KJNodes', 'ComfyUI-PixAI-Tagger', 'seedvr2_videoupscaler'] }); stepComfy(); }
    else if (step === 'comfy-none') { comfyFound = null; chooseBundled(); stepComfy(); }
    else if (step === 'where') stepWhere();
    else if (step === 'data') stepData();
    else if (step === 'installing') { stepInstalling(); onProgress({ stage: 'unpack', part: 'comfyui', done: 4.2e9, total: 8.94e9, elapsed: 72 }); busy = true; }
    else if (step === 'done') stepDone();
    else if (step === 'failed') stepFailed('comfyui_running', 'python.exe (1234)');
    else if (step === 'cancel') { this.show('installing'); onEvent({ event: 'close-request' }); }
    else if (step === 'old') { this.info.gpu = { name: 'NVIDIA GeForce RTX 3060', driver: '551.86', ok: false, problem: 'old_driver' }; stepData(); startInstall(); }
    else if (step === 'picker') { stepWhere(); pickFolder('D:\\Model Depot'); }
    else stepComfy();
  },
};

start();
