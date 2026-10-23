// Interface text. The table lives on the server (depot/strings.py): every line has a Chinese and an English column.

let lang = '';         // none until the settings have been read: the first request must not ask for a language
let langs = ['zh', 'en'];
let table = {};

export async function loadStrings() {
  const resp = await fetch('/api/ui/strings');
  const data = await resp.json();
  table = data.strings;
  langs = data.langs;
}

export function setLang(value) {
  lang = langs.includes(value) ? value : 'en';
  document.documentElement.lang = lang === 'zh' ? 'zh-Hans' : lang;
}

export function currentLang() {
  return lang;
}

export function t(key, vars) {
  const row = table[key];
  let text = row ? row[Math.max(0, langs.indexOf(lang))] : key;
  if (vars) text = text.replace(/\{(\w+)\}/g, (whole, name) => (name in vars ? vars[name] : whole));
  return text;
}
