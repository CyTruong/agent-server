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


def prune_images(messages: list, keep: int) -> int:
    """Drop images from all but the newest `keep` messages that carry one, in
    place, leaving a text note. Returns how many were dropped."""
    with_images = [m for m in messages if isinstance(m, dict) and m.get("images")]
    stale = with_images[:-keep] if keep > 0 else with_images
    for message in stale:
        del message["images"]
        message["content"] = (message.get("content") or "") + " [ảnh cũ đã được lược bỏ]"
    return len(stale)
