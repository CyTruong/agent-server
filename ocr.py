"""OCR wrapper (CPU) for Set-of-Mark locating.

Runs entirely on the Linux agent, on a screenshot already received. Returns each
detected text region with its recognised string and bounding box, so the agent
can label them and let the VLM pick one by number (coordinates come from OCR, not
from the model guessing pixels).

Backends (OCR_BACKEND env): "auto" (default: PaddleOCR, else EasyOCR), "paddle",
or "easyocr". Install one of:
    pip install "paddleocr>=2.7,<3.0" "paddlepaddle>=2.6"
    pip install easyocr      # tip: install CPU torch first to keep it small
Both run on CPU (gpu off) so they never compete with Ollama for VRAM. If no
backend loads, run_ocr returns None and the caller falls back to the grid locate.
The engine is loaded once and reused.
"""
import os
import threading

_engine = None
_kind = None
_tried = False
# The engines are not thread-safe; a cancelled task's OCR can still be running.
_lock = threading.Lock()


def _load(lang: str):
    global _engine, _kind, _tried
    if _tried:
        return _kind, _engine
    _tried = True
    backend = os.environ.get("OCR_BACKEND", "auto").lower()

    if backend in ("auto", "paddle"):
        try:
            from paddleocr import PaddleOCR
            # use_angle_cls off: screen text is upright; faster.
            _engine = PaddleOCR(use_angle_cls=False, lang=lang, show_log=False)
            _kind = "paddle"
            print("[ocr] backend: PaddleOCR", flush=True)
            return _kind, _engine
        except Exception as e:  # noqa: BLE001 - not installed / init error
            print(f"[ocr] PaddleOCR unavailable: {e}", flush=True)

    if backend in ("auto", "easyocr"):
        try:
            import easyocr
            langs = [lang, "en"] if lang != "en" else ["en"]
            _engine = easyocr.Reader(langs, gpu=False)
            _kind = "easyocr"
            print(f"[ocr] backend: EasyOCR {langs}", flush=True)
            return _kind, _engine
        except Exception as e:  # noqa: BLE001
            print(f"[ocr] EasyOCR unavailable: {e}", flush=True)

    _engine, _kind = None, None
    return _kind, _engine


def _item(box, text, conf):
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    x0, y0, x1, y1 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
    return {"text": text.strip(), "conf": float(conf),
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "cx": (x0 + x1) // 2, "cy": (y0 + y1) // 2}


def run_ocr(image_bytes: bytes, lang: str = "vi"):
    """Return a list of {text, conf, x0,y0,x1,y1, cx,cy} or None on failure."""
    with _lock:
        return _run_ocr(image_bytes, lang)


def _run_ocr(image_bytes: bytes, lang: str):
    kind, eng = _load(lang)
    if eng is None:
        return None
    try:
        from io import BytesIO
        import numpy as np
        from PIL import Image
        img = np.array(Image.open(BytesIO(image_bytes)).convert("RGB"))
    except Exception as e:  # noqa: BLE001
        print(f"[ocr] image decode failed: {e}", flush=True)
        return None

    items = []
    try:
        if kind == "paddle":
            res = eng.ocr(img, cls=False)
            # Expected: res = [ [ [box_4pts, (text, conf)], ... ] ]
            page = res[0] if isinstance(res, list) and res else None
            for line in page or []:
                try:
                    text, conf = line[1][0], line[1][1]
                    if text and text.strip():
                        items.append(_item(line[0], text, conf))
                except Exception:  # noqa: BLE001 - skip malformed lines
                    continue
        else:  # easyocr: [(box_4pts, text, conf), ...]
            for box, text, conf in eng.readtext(img):
                if text and text.strip():
                    items.append(_item(box, text, conf))
    except Exception as e:  # noqa: BLE001 - API shape differs across versions
        print(f"[ocr] {kind} failed: {e}", flush=True)
        return None
    return items
