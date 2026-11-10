"""Screenshot helpers shared by the runner and grounding code."""


def annotate_grid(jpeg: bytes, step: int = 100):
    """Draw a labelled coordinate grid over the screenshot so the vision model
    can READ pixel coordinates off the gridlines instead of guessing. Returns
    (png_bytes, (w, h)). This image goes only to the local Ollama, not the relay,
    so size is unconstrained. Returns the original bytes if Pillow is missing."""
    try:
        from io import BytesIO
        from PIL import Image, ImageDraw
    except Exception:  # noqa: BLE001
        return jpeg, (0, 0)
    im = Image.open(BytesIO(jpeg)).convert("RGB")
    w, h = im.size
    d = ImageDraw.Draw(im)
    red = (255, 40, 40)
    for x in range(0, w, step):
        d.line([(x, 0), (x, h)], fill=red, width=1)
        if x:
            d.text((x + 1, 1), str(x), fill=red)
    for y in range(0, h, step):
        d.line([(0, y), (w, y)], fill=red, width=1)
        if y:
            d.text((1, y + 1), str(y), fill=red)
    out = BytesIO()
    im.save(out, format="PNG")
    return out.getvalue(), (w, h)


def frame_signature(image: bytes, size: tuple = (32, 18)):
    """A tiny grayscale thumbnail used to tell whether the screen changed, or None
    if the image cannot be read."""
    try:
        from io import BytesIO
        from PIL import Image
        return Image.open(BytesIO(image)).convert("L").resize(size, Image.BOX).tobytes()
    except Exception:  # noqa: BLE001
        return None


def frames_similar(a: bytes, b: bytes, tolerance: float = 2.5) -> bool:
    """Mean absolute difference of two signatures (0-255 scale) within tolerance.
    A blinking caret or clock barely moves it; a scrolled page moves it a lot."""
    if not a or len(a) != len(b):
        return False
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a) <= tolerance


def image_size(image: bytes):
    """(width, height) of an encoded image, or None."""
    try:
        from io import BytesIO
        from PIL import Image
        return Image.open(BytesIO(image)).size
    except Exception:  # noqa: BLE001
        return None


def crop_image(image: bytes, box: tuple):
    """PNG of the (x0, y0, x1, y1) part of an encoded image, or None."""
    try:
        from io import BytesIO
        from PIL import Image
        im = Image.open(BytesIO(image)).convert("RGB")
        x0, y0, x1, y1 = (int(round(v)) for v in box)
        crop = im.crop((max(0, x0), max(0, y0), min(im.width, x1), min(im.height, y1)))
        out = BytesIO()
        crop.save(out, format="PNG")
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return None


def upscale(image: bytes, factor: int = 2) -> bytes:
    """The image enlarged (PNG) so OCR can read small text; the input on failure."""
    try:
        from io import BytesIO
        from PIL import Image
        im = Image.open(BytesIO(image)).convert("RGB")
        im = im.resize((im.width * factor, im.height * factor), Image.LANCZOS)
        out = BytesIO()
        im.save(out, format="PNG")
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return image


def prune_images(messages: list, keep: int) -> int:
    """Drop images from all but the newest `keep` messages that carry one, in
    place, leaving a text note. Returns how many were dropped."""
    with_images = [m for m in messages if isinstance(m, dict) and m.get("images")]
    stale = with_images[:-keep] if keep > 0 else with_images
    for message in stale:
        del message["images"]
        message["content"] = (message.get("content") or "") + " [ảnh cũ đã được lược bỏ]"
    return len(stale)
