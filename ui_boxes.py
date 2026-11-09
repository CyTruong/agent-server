"""Classical-CV detection of rectangular UI elements (input boxes, buttons,
cards) for the Set-of-Mark locate step. Complements OCR: it finds *text-less*
clickable boxes (an empty "Aa" message field, an icon button) that keyword
matching can never catch.

detect_boxes(jpeg_bytes) -> [{x0,y0,x1,y1,cx,cy}] in full-image pixels, or [] if
OpenCV is missing or anything fails. Best-effort only - never raises.

OpenCV ships as a dependency of paddleocr, so it is usually already installed.
"""
from __future__ import annotations


def _iou(a, b) -> float:
    ax0, ay0, ax1, ay1 = a["x0"], a["y0"], a["x1"], a["y1"]
    bx0, by0, bx1, by1 = b["x0"], b["y0"], b["x1"], b["y1"]
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    if inter == 0:
        return 0.0
    ua = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / ua if ua > 0 else 0.0


def detect_boxes(jpeg_bytes: bytes, max_boxes: int = 120) -> list[dict]:
    try:
        import cv2
        import numpy as np
    except Exception:  # noqa: BLE001 - opencv not installed
        return []
    try:
        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return []
        H, W = img.shape[:2]
        # Edges + a light dilation closes box borders into continuous contours.
        edges = cv2.Canny(img, 50, 150)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        edges = cv2.dilate(edges, kernel, iterations=1)
        cnts, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    except Exception:  # noqa: BLE001
        return []

    min_w, min_h = 24, 12            # ignore specks
    max_w, max_h = W * 0.92, H * 0.7  # ignore page-sized frames
    area_total = float(W * H)
    out: list[dict] = []
    for c in cnts:
        try:
            x, y, w, h = cv2.boundingRect(c)
        except Exception:  # noqa: BLE001
            continue
        if w < min_w or h < min_h or w > max_w or h > max_h:
            continue
        ar = w / float(h)
        if ar > 25 or ar < 0.15:     # drop rules/separators and tall slivers
            continue
        if (w * h) / area_total > 0.5:
            continue
        box = {"x0": x, "y0": y, "x1": x + w, "y1": y + h,
               "cx": x + w // 2, "cy": y + h // 2}
        # Deduplicate against near-identical rects already kept.
        if any(_iou(box, k) > 0.8 for k in out):
            continue
        out.append(box)
    # Bigger boxes first (more likely to be the actual control), capped.
    out.sort(key=lambda b: (b["x1"] - b["x0"]) * (b["y1"] - b["y0"]), reverse=True)
    return out[:max_boxes]
