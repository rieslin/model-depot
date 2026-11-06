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
  document.documentElement.lang = table['meta.html_lang'] ? textIn(lang, 'meta.html_lang') : lang;
}

export function currentLang() {
  return lang;
}

// The languages the table has, in its order.
export function languages() {
  return [...langs];
}

export function t(key, vars) {
  const row = table[key];
  let text = row ? row[Math.max(0, langs.indexOf(lang))] : key;
  if (vars) text = text.replace(/\{(\w+)\}/g, (whole, name) => (name in vars ? vars[name] : whole));
  return text;
}

// A line in a given language, whatever the interface's is (a language's own name, in the language switch).
export function textIn(language, key) {
  const row = table[key];
  return row ? row[Math.max(0, langs.indexOf(language))] : key;
}

// The interface language's locale, for dates and times.
export function locale() {
  return t('meta.locale');
}

// A list as the interface language writes it: "a、b、c" or "a, b, c"; the long separator between whole statements.
export function joinList(items, long) {
  return items.join(t(long ? 'list.sep_long' : 'list.sep'));
}

// "a、b 与 c", "a, b and c".
export function andList(items) {
  if (items.length < 2) return items.join('');
  return items.slice(0, -1).join(t('list.sep')) + t('list.and') + items[items.length - 1];
}
