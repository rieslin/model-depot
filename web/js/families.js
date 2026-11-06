// Base-model families: the short code on the index and the group blocks, and the product's own name. The codes are
// set in Latin capitals in both languages, like the plates of a team; the names are product names and stay as they
// are. The families come from the server's catalog (/api/ui/state); only "unknown" has a sentence, in the strings table.
import { t } from './i18n.js';
import { serverState } from './ui.js';

function familyOf(key) {
  const families = serverState().families || {};
  return Object.prototype.hasOwnProperty.call(families, key) ? families[key] : null;
}

export function familyCode(key) {
  const f = familyOf(key);
  if (f) return f.code;
  return key && key !== 'unknown' ? key.toUpperCase().slice(0, 8) : t('fam.code_unknown');
}

export function familyName(key) {
  const f = familyOf(key);
  if (f) return f.name;
  return key && key !== 'unknown' ? key : t('fam.unknown');
}

// The code of the family whose architecture this one is built on (their LoRAs load in each other, weaker), or null.
export function familyArch(key) {
  const f = familyOf(key);
  return f && f.arch ? familyCode(f.arch) : null;
}
