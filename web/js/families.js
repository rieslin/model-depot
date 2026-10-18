// Base-model families: the short code on the index and the group blocks, and the product's own name. The codes are
// set in Latin capitals in both languages, like the plates of a team; the names are product names and stay as they
// are. Only "unknown" has a sentence, in the strings table.
import { t } from './i18n.js';

const FAMILIES = {
  sd15: ['SD15', 'SD 1.5'], sd2: ['SD2', 'SD 2'], sdxl: ['SDXL', 'SDXL'], pony: ['PONY', 'Pony'],
  illustrious: ['ILXL', 'Illustrious'], flux: ['FLUX', 'Flux.1'], flux2: ['FLUX2', 'Flux.2'], chroma: ['CHROMA', 'Chroma'],
  sd3: ['SD3', 'SD 3'], zimage: ['ZIMG', 'Z-Image'], lumina2: ['LUMINA', 'Lumina 2'], qwen: ['QWEN', 'Qwen-Image'],
  krea2: ['KREA2', 'Krea 2'], anima: ['ANIMA', 'Anima'], cosmos: ['COSMOS', 'Cosmos'], wan: ['WAN', 'Wan'],
  hunyuan_video: ['HYVID', 'HunyuanVideo'], hunyuan_dit: ['HYDIT', 'Hunyuan-DiT'], hidream: ['HIDREAM', 'HiDream'],
  auraflow: ['AURA', 'AuraFlow'], pixart: ['PIXART', 'PixArt'], kolors: ['KOLORS', 'Kolors'], pony7: ['PONY7', 'Pony V7'],
  seedvr2: ['SEEDVR2', 'SeedVR2'], ltxv: ['LTXV', 'LTX-Video'], cogvideox: ['COGVX', 'CogVideoX'], mochi: ['MOCHI', 'Mochi'],
  cascade: ['CASCADE', 'Stable Cascade'], svd: ['SVD', 'Stable Video Diffusion'], playground: ['PGV', 'Playground'],
};

// Families built on another architecture: their LoRAs load in each other (weaker).
const ARCH = { pony: 'SDXL', illustrious: 'SDXL' };

export function familyCode(key) {
  if (FAMILIES[key]) return FAMILIES[key][0];
  return key && key !== 'unknown' ? key.toUpperCase().slice(0, 8) : 'N/A';
}

export function familyName(key) {
  if (FAMILIES[key]) return FAMILIES[key][1];
  return key && key !== 'unknown' ? key : t('fam.unknown');
}

export function familyArch(key) {
  return ARCH[key] || null;
}
