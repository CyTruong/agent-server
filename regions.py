"""Screen regions for native-resolution captures (zoom, tap fallback, name re-read).

The full screenshot the model sees is scaled down (1920 -> 1280 wide), which makes
small text - window titles, ribbon buttons, search results - hard to read. A region
capture asks the PC for one rectangle at its real resolution instead.
"""

# Named areas the model can zoom into, as fractions (x0, y0, x1, y1) of the screen.
ZOOM_AREAS = {
    "title":        (0.0, 0.0, 1.0, 0.05),   # window title bar
    "top_bar":      (0.0, 0.0, 1.0, 0.20),   # title + ribbon / toolbar / tabs
    "top":          (0.0, 0.0, 1.0, 0.50),
    "bottom":       (0.0, 0.5, 1.0, 1.0),
    "left":         (0.0, 0.0, 0.5, 1.0),
    "right":        (0.5, 0.0, 1.0, 1.0),
    "center":       (0.25, 0.25, 0.75, 0.75),
    "top_left":     (0.0, 0.0, 0.5, 0.5),
    "top_right":    (0.5, 0.0, 1.0, 0.5),
    "bottom_left":  (0.0, 0.5, 0.5, 1.0),
    "bottom_right": (0.5, 0.5, 1.0, 1.0),
    "taskbar":      (0.0, 0.93, 1.0, 1.0),
}

# The tap tool's `area` values, matching matching._in_area.
TAP_AREAS = {
    "top":    (0.0, 0.0, 1.0, 0.45),
    "bottom": (0.0, 0.55, 1.0, 1.0),
    "left":   (0.0, 0.0, 0.45, 1.0),
    "right":  (0.55, 0.0, 1.0, 1.0),
    "center": (0.25, 0.25, 0.75, 0.75),
}

MIN_SIDE = 16   # the firmware refuses smaller regions


def fraction_box(fractions, screen_w: int, screen_h: int) -> tuple:
    """(x, y, w, h) in screen pixels for an (x0, y0, x1, y1) fraction box."""
    x0, y0, x1, y1 = fractions
    x, y = round(x0 * screen_w), round(y0 * screen_h)
    return x, y, max(MIN_SIDE, round(x1 * screen_w) - x), max(MIN_SIDE, round(y1 * screen_h) - y)


def image_box_to_screen(box, image_w: int, image_h: int, screen_w: int, screen_h: int,
                        pad: int = 0) -> tuple:
    """(x, y, w, h) in screen pixels for an (x0, y0, x1, y1) box drawn on the
    scaled screenshot, grown by `pad` screen pixels and kept on screen."""
    sx, sy = screen_w / image_w, screen_h / image_h
    x0, y0, x1, y1 = (float(v) for v in box)
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    x = max(0, round(x0 * sx) - pad)
    y = max(0, round(y0 * sy) - pad)
    w = min(screen_w, round(x1 * sx) + pad) - x
    h = min(screen_h, round(y1 * sy) + pad) - y
    return x, y, max(MIN_SIDE, w), max(MIN_SIDE, h)

