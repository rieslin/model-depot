// Masked pictures: the image drawn into a canvas a few cells across, which the page then shows scaled up with hard
// edges. A real mosaic of the picture, not a blur: its colours and layout stay readable as a shape.

// A canvas `cols` cells wide covering a box of the given aspect (width / height), cropped like object-fit: cover
// with the crop anchored `anchorY` of the way down (0 top, 0.5 centre).
export function mosaicOf(img, aspect, cols = 9, anchorY = 0.3) {
  const rows = Math.max(1, Math.round(cols / aspect));
  const canvas = document.createElement('canvas');
  canvas.width = cols;
  canvas.height = rows;
  const iw = img.naturalWidth || img.width;
  const ih = img.naturalHeight || img.height;
  if (!iw || !ih) return canvas;
  let sw = iw;
  let sh = iw / aspect;
  if (sh > ih) { sh = ih; sw = ih * aspect; }
  const sx = (iw - sw) / 2;
  const sy = (ih - sh) * anchorY;
  const g = canvas.getContext('2d');
  g.imageSmoothingEnabled = true;
  g.imageSmoothingQuality = 'medium';
  g.drawImage(img, sx, sy, sw, sh, 0, 0, cols, rows);
  return canvas;
}

// Load an image off-screen. Resolves with the element, rejects when it cannot be had.
export function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.decoding = 'async';
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('image'));
    img.src = src;
  });
}
