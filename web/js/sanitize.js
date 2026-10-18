// The authors' descriptions come from civitai as HTML. Only plain text formatting and links are kept: no scripts,
// no styles, no pictures (they would be fetched from other sites), no frames, no event attributes.

const KEEP = new Set(['P', 'BR', 'B', 'STRONG', 'I', 'EM', 'U', 'S', 'DEL', 'UL', 'OL', 'LI', 'A', 'H1', 'H2', 'H3', 'H4',
  'H5', 'H6', 'BLOCKQUOTE', 'CODE', 'PRE', 'HR', 'SPAN', 'DIV', 'SUB', 'SUP', 'TABLE', 'THEAD', 'TBODY', 'TR', 'TD', 'TH']);
const DROP_WITH_CONTENT = new Set(['SCRIPT', 'STYLE', 'IFRAME', 'OBJECT', 'EMBED', 'TEMPLATE', 'NOSCRIPT', 'SVG', 'MATH',
  'FORM', 'INPUT', 'BUTTON', 'SELECT', 'TEXTAREA', 'VIDEO', 'AUDIO', 'IMG', 'PICTURE', 'SOURCE', 'CANVAS', 'LINK', 'META']);

function clean(node, into) {
  for (const child of Array.from(node.childNodes)) {
    if (child.nodeType === Node.TEXT_NODE) {
      into.append(document.createTextNode(child.textContent));
      continue;
    }
    if (child.nodeType !== Node.ELEMENT_NODE) continue;
    const tag = child.tagName.toUpperCase();
    if (DROP_WITH_CONTENT.has(tag)) continue;
    if (!KEEP.has(tag)) {           // unknown wrapper: keep what it says, drop the wrapper
      clean(child, into);
      continue;
    }
    const copy = document.createElement(tag.toLowerCase());
    if (tag === 'A') {
      const href = (child.getAttribute('href') || '').trim();
      if (/^https?:\/\//i.test(href)) {
        copy.setAttribute('href', href);
        copy.setAttribute('target', '_blank');
        copy.setAttribute('rel', 'noopener noreferrer');
      }
    }
    clean(child, copy);
    into.append(copy);
  }
}

// A DocumentFragment holding the safe part of `html`.
export function safeFragment(html) {
  const doc = new DOMParser().parseFromString(String(html || ''), 'text/html');
  const out = document.createDocumentFragment();
  clean(doc.body, out);
  return out;
}
