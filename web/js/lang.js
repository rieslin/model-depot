// The language of an author's notes, paragraph by paragraph. Many civitai notes give the same text twice, in Chinese
// and in English (line by line, or as two sections); the dossier then shows the part in the interface's language.
// Notes in one language only are shown whole, in both interfaces: English notes without a Chinese part are what a
// Chinese reader sees too (the user's rule, 2026-10-02).
//
// A paragraph counts as Chinese when Chinese characters carry it, as English when it is English prose. Settings
// ("Steps: 20-30"), prompts and tag lists, links, numbers and lines that mix the two languages belong to both, and
// so does Japanese.

const HAN = /[㐀-䶿一-鿿豈-﫿]/g;
const KANA = /[぀-ヿ]/;
const LATIN = /[A-Za-z]/g;
const ZH_HEADING = /^\s*(?:中文|简体中文|繁體中文|中文说明|中文版|chinese)\s*[:：]?\s*$/i;
const EN_HEADING = /^\s*(?:english|eng|english version)\s*[:：]?\s*$/i;
const CONTAINERS = new Set(['DIV', 'UL', 'OL', 'BLOCKQUOTE', 'TABLE', 'THEAD', 'TBODY']);

function isProse(text) {
  if (/<lora:|\w_\w|\(\w[^)]*:\d/.test(text)) return false;                     // prompts and tags
  const pieces = text.split(/[,，]/);
  if (pieces.length >= 3 && pieces.every((p) => p.trim().split(/\s+/).length <= 3)) return false;   // a tag list
  return /[a-z]{2,}\s+[a-z]{2,}/i.test(text);                                    // words with spaces between them
}

// 'zh', 'en', or null for a piece that belongs to both
export function langOf(text) {
  const s = String(text || '').trim();
  if (!s) return null;
  if (ZH_HEADING.test(s)) return 'zh';
  if (EN_HEADING.test(s)) return 'en';
  if (KANA.test(s)) return null;
  const han = (s.match(HAN) || []).length;
  const latin = (s.match(LATIN) || []).length;
  if (han >= 2 && han * 3 >= latin) return 'zh';
  if (han === 0 && latin >= 12 && isProse(s)) return 'en';
  return null;
}

// Do these pieces hold the same notes in both languages? Each language must carry a fair share: a Chinese model name
// in English notes, or an English line in Chinese notes, does not make them bilingual. A Chinese character says
// about as much as three English letters.
function bilingual(pieces) {
  let zh = 0;
  let en = 0;
  for (const p of pieces) {
    if (p.lang === 'zh') zh += p.size * 3;
    else if (p.lang === 'en') en += p.size;
  }
  return zh > 0 && en > 0 && Math.min(zh, en) >= 0.3 * Math.max(zh, en);
}

function collect(parent, out) {
  for (const node of Array.from(parent.childNodes)) {
    if (node.nodeType === Node.ELEMENT_NODE && CONTAINERS.has(node.tagName) && node.children.length) {
      collect(node, out);
    } else if ((node.textContent || '').trim()) {
      const text = node.textContent;
      out.push({ node, lang: langOf(text), size: text.trim().length });
    }
  }
}

// The fragment (from sanitize.safeFragment) with the other language's paragraphs taken out, when it has both.
export function inLanguage(fragment, lang) {
  const pieces = [];
  collect(fragment, pieces);
  if (!bilingual(pieces)) return fragment;
  const other = lang === 'zh' ? 'en' : 'zh';
  for (const p of pieces) if (p.lang === other) p.node.remove();
  return fragment;
}

// The same for a list of lines (the excerpt of the notes).
export function linesInLanguage(lines, lang) {
  const pieces = (lines || []).map((text) => ({ text, lang: langOf(text), size: String(text).trim().length }));
  if (!bilingual(pieces)) return lines || [];
  const other = lang === 'zh' ? 'en' : 'zh';
  return pieces.filter((p) => p.lang !== other).map((p) => p.text);
}
