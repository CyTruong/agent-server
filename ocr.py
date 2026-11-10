"""OCR wrapper (CPU) for Set-of-Mark locating.

Runs entirely on the Linux agent, on a screenshot already received. Returns each
detected text region with its recognised string and bounding box, so the agent
can label them and let the VLM pick one by number (coordinates come from OCR, not
from the model guessing pixels).

Backends (OCR_BACKEND env): "auto" (default: EasyOCR, else PaddleOCR), "easyocr"
or "paddle". EasyOCR comes first because it keeps Vietnamese accents; PaddleOCR
2.x reads Vietnamese with its latin model and drops most of them ("Trường" ->
"Trudng"), which makes every name check ask the user. Install:
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu  # a matched pair
    pip install easyocr
    pip install "paddleocr>=2.7,<3.0" "paddlepaddle>=2.6"   # optional fallback
Both run on CPU (gpu off) so they never compete with Ollama for VRAM. If no
backend loads, run_ocr returns None and the caller falls back to the grid locate.
The engine is loaded once and reused.
"""
import os
import threading
import time

_engine = None
_kind = None
_tried = False
_tried_at = 0.0
_error = ""          # why the last load failed, shown in the task log
# A failed load is retried after this long, so installing a package (or getting
# the model download through) works without restarting the agent.
RETRY_SECONDS = 300
# The engines are not thread-safe; a cancelled task's OCR can still be running.
_lock = threading.Lock()


def load_error() -> str:
    """Why no OCR engine is available ("" if one is, or none was tried yet)."""
    return _error


def _load(lang: str):
    global _engine, _kind, _tried, _tried_at, _error
    if _engine is not None:
        return _kind, _engine
    if _tried and time.monotonic() - _tried_at < RETRY_SECONDS:
        return None, None
    _tried, _tried_at = True, time.monotonic()
    backend = os.environ.get("OCR_BACKEND", "auto").lower()
    order = {"easyocr": ("easyocr",), "paddle": ("paddle",)}.get(backend, ("easyocr", "paddle"))
    errors = []

    for kind in order:
        try:
            if kind == "easyocr":
                import easyocr
                langs = [lang, "en"] if lang != "en" else ["en"]
                # First use downloads the models (~100 MB) into ~/.EasyOCR.
                _engine = easyocr.Reader(langs, gpu=False)
                print(f"[ocr] backend: EasyOCR {langs}", flush=True)
            else:
                from paddleocr import PaddleOCR
                # use_angle_cls off: screen text is upright; faster.
                _engine = PaddleOCR(use_angle_cls=False, lang=lang, show_log=False)
                print("[ocr] backend: PaddleOCR", flush=True)
            _kind, _error = kind, ""
            return _kind, _engine
        except Exception as e:  # noqa: BLE001 - not installed / init error
            errors.append(f"{kind}: {type(e).__name__}: {e}")
            print(f"[ocr] {kind} unavailable: {type(e).__name__}: {e}", flush=True)

    _engine, _kind = None, None
    _error = "; ".join(errors)
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
