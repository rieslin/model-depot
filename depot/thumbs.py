"""Thumbnails: a small JPEG per model cover, made once and kept with the model's record, and per example image."""
import io

from PIL import Image, ImageOps

MAX_WIDTH, MAX_HEIGHT = 512, 1024           # covers
SAMPLE_WIDTH, SAMPLE_HEIGHT = 768, 1536     # the author's example images in the detail view
_BACKDROP = (32, 32, 32)
_LANCZOS = getattr(Image, "Resampling", Image).LANCZOS


def make_thumbnail(source, max_width, max_height):
    """(jpeg bytes, (width, height) of the original). `source` is a file path or the image's bytes."""
    with Image.open(source if isinstance(source, str) else io.BytesIO(source)) as im:
        size = im.size
        im.seek(0)                                  # first frame of an animated image
        im = ImageOps.exif_transpose(im) or im
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            flat = Image.new("RGB", rgba.size, _BACKDROP)
            flat.paste(rgba, mask=rgba.split()[-1])
            im = flat
        else:
            im = im.convert("RGB")
        im.thumbnail((max_width, max_height), _LANCZOS)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=86, optimize=True)
        return out.getvalue(), size


def make_cover(source):
    return make_thumbnail(source, MAX_WIDTH, MAX_HEIGHT)


def make_sample(source):
    return make_thumbnail(source, SAMPLE_WIDTH, SAMPLE_HEIGHT)
