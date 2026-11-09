"""Finding WHERE to click: UIA, OCR, Set-of-Mark, UI-TARS and the 3x3 grid.

TaskRunner inherits this; it relies on the runner's relay, frame state,
_screenshot, _screen_cache_fresh, _invalidate_screen_cache and _ask_confirm.
"""
import asyncio
import os
import time

import llm
import ocr
import ui_boxes
from agent_log import log
from config import (
    DEVICE_TIMEOUT,
    IMAGE_DIR,
    MARKS_ENABLE,
    MARKS_MAX,
    MODEL,
    NUM_CTX,
    OCR_CACHE_TTL,
    OCR_ENABLE,
    OCR_EXACT_MIN,
    OCR_LANG,
    OCR_MATCH_MIN,
    OCR_PARALLEL_MIN_SPLITS,
    REFINE_CLICK,
    SAVE_IMAGES,
    THINK,
    UIA_ENABLE,
    UIA_MARKS_ENABLE,
    UIA_TIMEOUT,
    UITARS_COORD,
    UITARS_ENABLE,
    UITARS_FULLRES,
    UITARS_MAX_SIDE,
    UITARS_MODEL,
    UITARS_NUM_CTX,
    UITARS_TIMEOUT,
)
from matching import (
    CERTAIN_MATCHES,
    _REL_VI,
    _in_area,
    _in_region,
    _keywords_from,
    _relative_region,
    _sim,
    is_proper_name,
    name_match,
    name_rank,
    resolve_name,
)
from relay_client import new_request_id


class GroundingMixin:
    @staticmethod
    def _safe_tag(tag: str) -> str:
        """ASCII-only filename fragment: strip Vietnamese accents, keep [A-Za-z0-9_-]."""
        import unicodedata
        t = unicodedata.normalize("NFKD", tag).encode("ascii", "ignore").decode()
        t = "".join(c if (c.isalnum() or c in "_-") else "_" for c in t).strip("_")
        return t[:24] or "img"

    def _save(self, task_id: str, tag: str, data: bytes, ext: str = "png"):
        if not SAVE_IMAGES or not data:
            return
        try:
            os.makedirs(IMAGE_DIR, exist_ok=True)
            fn = os.path.join(IMAGE_DIR,
                              f"{time.strftime('%H%M%S')}_{task_id}_{self._safe_tag(tag)}.{ext}")
            with open(fn, "wb") as f:
                f.write(data)
            log(f"[{task_id}] saved {fn}")
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] save image failed: {e}")

    def _mark_click(self, task_id: str, ix: int, iy: int, tag: str):
        """Save the raw screenshot with a marker at the clicked image point, so
        it is obvious whether the click landed on the intended element."""
        if not SAVE_IMAGES or not self._raw_frame:
            return
        try:
            from io import BytesIO
            from PIL import Image, ImageDraw
            im = Image.open(BytesIO(self._raw_frame)).convert("RGB")
            d = ImageDraw.Draw(im)
            r = 14
            d.ellipse([ix - r, iy - r, ix + r, iy + r], outline=(0, 255, 0), width=3)
            d.line([ix - r, iy, ix + r, iy], fill=(0, 255, 0), width=2)
            d.line([ix, iy - r, ix, iy + r], fill=(0, 255, 0), width=2)
            out = BytesIO()
            im.save(out, format="PNG")
            self._save(task_id, tag, out.getvalue())
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] mark_click failed: {e}")

    async def _locate(self, task_id: str, target: str, kws: list = None, area: str = "any",
                      region=None, ctx: str = ""):
        """3x3 region descent (quad-tree style): recursively split the screenshot
        into 9 numbered cells and ask the model which cell holds `target`, zooming
        into it each level. The model only picks a digit 1-9 (far easier than
        reading pixels), so it stays accurate even on a weak VLM. Returns an
        (x, y) point in original image space, or None if not found."""
        if not (self._raw_frame and self._img_w):
            return None
        try:
            from io import BytesIO
            from PIL import Image, ImageDraw, ImageFont
        except Exception:  # noqa: BLE001
            return None
        try:
            base = Image.open(BytesIO(self._raw_frame)).convert("RGB")
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] locate open failed: {e}")
            return None

        W, H = float(self._img_w), float(self._img_h)
        if region:   # start inside the anchor-relative search box
            x0, y0 = float(region[0]), float(region[1])
            w, h = float(region[2] - region[0]), float(region[3] - region[1])
        else:
            x0, y0, w, h = 0.0, 0.0, W, H
        # Split until a cell is ~50 px (max 3 levels; fewer for a small region).
        levels = 1
        while levels < 3 and max(w, h) / (3 ** levels) > 50:
            levels += 1
        for level in range(levels):
            region = base.crop((int(x0), int(y0), int(x0 + w), int(y0 + h)))
            scale = max(1, int(900 / max(w, 1)))
            rw, rh = max(1, int(w * scale)), max(1, int(h * scale))
            im = region.resize((rw, rh))
            d = ImageDraw.Draw(im)
            try:
                font = ImageFont.truetype("DejaVuSans-Bold.ttf", max(20, rh // 7))
            except Exception:  # noqa: BLE001
                font = ImageFont.load_default()
            for i in (1, 2):
                d.line([(rw * i // 3, 0), (rw * i // 3, rh)], fill=(255, 40, 40), width=2)
                d.line([(0, rh * i // 3), (rw, rh * i // 3)], fill=(255, 40, 40), width=2)
            for n in range(9):
                cx = (n % 3) * rw // 3 + rw // 6
                cy = (n // 3) * rh // 3 + rh // 6
                d.text((cx, cy), str(n + 1), fill=(255, 255, 0), font=font,
                       anchor="mm", stroke_width=3, stroke_fill=(0, 0, 0))
            out = BytesIO()
            im.save(out, format="PNG")
            self._save(task_id, f"locate_L{level}", out.getvalue())
            hint = f" (chữ trên/cạnh nó: {', '.join(kws)})" if kws else ""
            if level == 0 and ctx:
                hint += f" {ctx}"
            elif level == 0 and area and area != "any":
                hint += f" (thường ở vùng {area} màn hình)"
            note = (f"Ảnh được chia 9 ô, đánh số 1-9 (màu vàng, theo thứ tự trái→phải, "
                    f"trên→dưới). Phần tử '{target}'{hint} nằm ở ô số nào? Chỉ trả về MỘT chữ "
                    "số 1-9. Nếu hoàn toàn không thấy, trả 0. /no_think")

            # Once the region is small (>= OCR_PARALLEL_MIN_SPLITS splits done),
            # OCR it in parallel with the grid LLM call (OCR on CPU, LLM on GPU).
            # OCR is fast and gives an exact point; on a match, cancel the LLM and
            # finish here.
            if OCR_ENABLE and kws and level >= OCR_PARALLEL_MIN_SPLITS:
                ocr_task = asyncio.create_task(self._ocr_region(task_id, x0, y0, w, h, kws))
                llm_task = asyncio.create_task(self._grid_pick(task_id, level, out.getvalue(), note))
                ocr_pt = await ocr_task
                if ocr_pt:
                    llm_task.cancel()
                    try:
                        await llm_task
                    except asyncio.CancelledError:
                        pass
                    log(f"[{task_id}] locate L{level}: OCR song song trúng -> dừng")
                    return ocr_pt
                cell = await llm_task
            else:
                cell = await self._grid_pick(task_id, level, out.getvalue(), note)
            if cell is None:
                return None
            log(f"[{task_id}] locate L{level}: '{target}' -> cell {cell} "
                f"(region {int(x0)},{int(y0)} {int(w)}x{int(h)})")
            if cell == 0:
                return None
            col, row = (cell - 1) % 3, (cell - 1) // 3
            x0 += col * w / 3
            y0 += row * h / 3
            w /= 3
            h /= 3
        return int(x0 + w / 2), int(y0 + h / 2)

    async def _ocr_items(self, task_id: str):
        """Return OCR boxes, reusing results for the same fresh frame."""
        if not self._raw_frame or not self._frame_hash:
            return None
        now = time.monotonic()
        if (
            self._ocr_cache_items is not None
            and self._ocr_cache_hash == self._frame_hash
            and now - self._ocr_cache_at <= OCR_CACHE_TTL
        ):
            log(f"[{task_id}] OCR cache hit: {len(self._ocr_cache_items)} vùng chữ")
            return self._ocr_cache_items

        loop = asyncio.get_running_loop()
        try:
            items = await loop.run_in_executor(None, ocr.run_ocr, self._raw_frame, OCR_LANG)
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] OCR error: {e}")
            return None
        if items is None:
            log(f"[{task_id}] OCR unavailable (chưa cài paddleocr?) -> fallback")
            return None
        self._ocr_cache_hash = self._frame_hash
        self._ocr_cache_at = time.monotonic()
        self._ocr_cache_items = items
        log(f"[{task_id}] OCR: {len(items)} vùng chữ")
        return items

    def _find_anchor(self, task_id: str, items: list, anchor_kws: list, area: str,
                     relation: str):
        """OCR boxes matching the anchor keywords (>= OCR_MATCH_MIN), best first.
        Returns a LIST of candidate boxes (the caller disambiguates ambiguous
        anchors with the target keywords). Order: in-area preferred, then score;
        for below/bottom_of a header name is assumed to sit above its panel, so
        among equally-scored boxes the top-most comes first."""
        W, H = self._img_w, self._img_h
        hits = []
        for it in items:
            s = max((_sim(k, it["text"]) for k in anchor_kws), default=0.0)
            if s >= OCR_MATCH_MIN:
                hits.append((s, it))
        if not hits:
            log(f"[{task_id}] anchor {anchor_kws}: không thấy trên OCR")
            return []
        use_area = area if area and area != "any" else ""
        in_area = [x for x in hits if use_area and _in_area(x[1], use_area, W, H)]
        hits = in_area or hits
        # Primary sort by score desc; tie-break top-most for below/bottom_of.
        hits.sort(key=lambda x: (-x[0], x[1]["cy"] if relation in ("below", "bottom_of") else 0))
        cands = [it for _s, it in hits]
        a = cands[0]
        log(f"[{task_id}] anchor '{a['text']}' ({hits[0][0]:.2f}) @({a['cx']},{a['cy']})"
            f"{' — ' + str(len(cands)) + ' ứng viên' if len(cands) > 1 else ''}")
        return cands

    def _save_region(self, task_id: str, a: dict, region):
        """Debug image: anchor box (blue) + search region (green)."""
        if not SAVE_IMAGES or not self._raw_frame:
            return
        try:
            from io import BytesIO
            from PIL import Image, ImageDraw
            im = Image.open(BytesIO(self._raw_frame)).convert("RGB")
            d = ImageDraw.Draw(im)
            d.rectangle([a["x0"], a["y0"], a["x1"], a["y1"]], outline=(40, 120, 255), width=3)
            if region:
                d.rectangle(list(region), outline=(0, 220, 0), width=3)
            out = BytesIO()
            im.save(out, format="PNG")
            self._save(task_id, "tap_region", out.getvalue())
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] save region failed: {e}")

    def _best_match(self, scored, region, strong_only=True, thr=None):
        """From [(score, box)], take matches >= `thr` (default OCR_MATCH_MIN); among
        the top (within 0.05) pick the box nearest the region/screen centre. Returns
        (x,y) or None."""
        W, H = self._img_w, self._img_h
        th = thr if thr is not None else OCR_MATCH_MIN
        strong = [(s, it) for s, it in scored if s >= th]
        if not strong:
            return None
        top = strong[0][0] if scored and scored[0][0] == strong[0][0] else max(s for s, _ in strong)
        best = [it for s, it in strong if top - s < 0.05]
        if len(best) > 1:
            if region:
                rx, ry = (region[0] + region[2]) / 2, (region[1] + region[3]) / 2
            else:
                rx, ry = W / 2, H / 2
            best.sort(key=lambda it: (it["cx"] - rx) ** 2 + (it["cy"] - ry) ** 2)
        it = best[0]
        return it["cx"], it["cy"]

    async def _tap_ocr(self, task_id: str, target: str, kws: list, area: str,
                       items: list, region=None, thr=None):
        """Direct OCR keyword match over the boxes inside `region` (whole screen
        if None). No LLM, no cap. Accent-insensitive fuzzy match; a hit >= `thr`
        (default OCR_MATCH_MIN) is clicked directly. Returns (x,y) image space, or
        None to fall back."""
        if not kws:
            return None                      # nothing to match text against
        items = [it for it in items if _in_region(it, region)]
        if not items:
            log(f"[{task_id}] OCR: không có chữ trong vùng tìm -> fallback")
            return None
        W, H = self._img_w, self._img_h
        use_area = area if area and area != "any" else ""
        scored = [(max((_sim(k, it["text"]) for k in kws), default=0.0), it) for it in items]
        if use_area:
            scored = [x for x in scored if _in_area(x[1], use_area, W, H)] or scored
        scored.sort(key=lambda x: x[0], reverse=True)
        log(f"[{task_id}] OCR match: {len(items)} vùng {'trong vùng tìm' if region else 'toàn màn'}, "
            f"keywords={kws}; top: "
            + ", ".join(f"'{it['text']}'={s:.2f}" for s, it in scored[:5]))
        th = thr if thr is not None else OCR_MATCH_MIN
        pt = self._best_match(scored, region, thr=th)
        if pt:
            log(f"[{task_id}] OCR match @({pt[0]},{pt[1]}) (>= {th}) — không cần LLM")
        else:
            log(f"[{task_id}] OCR: không vùng nào khớp >= {th} -> fallback")
        return pt

    async def _ocr_region(self, task_id: str, x0, y0, w, h, kws: list):
        """OCR just the cropped region (upscaled for small text), match keywords,
        map the hit back to image space. For the parallel short-circuit inside
        the grid descent. Returns (x,y) or None."""
        if not kws or not self._raw_frame:
            return None
        try:
            from io import BytesIO
            from PIL import Image
            base = Image.open(BytesIO(self._raw_frame)).convert("RGB")
        except Exception:  # noqa: BLE001
            return None
        x0i, y0i = int(x0), int(y0)
        x1i, y1i = int(x0 + w), int(y0 + h)
        if x1i - x0i < 8 or y1i - y0i < 8:
            return None
        Z = max(1, int(1100 / max(w, 1)))    # upscale a small crop so OCR reads it
        try:
            crop = base.crop((x0i, y0i, x1i, y1i))
            if Z > 1:
                crop = crop.resize((int((x1i - x0i) * Z), int((y1i - y0i) * Z)))
            buf = BytesIO()
            crop.save(buf, format="PNG")
        except Exception:  # noqa: BLE001
            return None
        loop = asyncio.get_running_loop()
        try:
            items = await loop.run_in_executor(None, ocr.run_ocr, buf.getvalue(), OCR_LANG)
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] region OCR error: {e}")
            return None
        if not items:
            return None
        # Map crop coords back to image space, then score.
        scored = []
        for it in items:
            m = dict(it)
            m["cx"] = x0i + int(it["cx"] / Z)
            m["cy"] = y0i + int(it["cy"] / Z)
            s = max((_sim(k, it["text"]) for k in kws), default=0.0)
            scored.append((s, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        pt = self._best_match(scored, (x0i, y0i, x1i, y1i))
        if pt:
            log(f"[{task_id}] region OCR match '{scored[0][1]['text']}' -> img({pt[0]},{pt[1]})")
        return pt

    async def _tap_marks(self, task_id: str, target: str, kws: list, area: str,
                         items, region=None):
        """Set-of-Mark locate (A+B): number the candidate boxes - OCR text boxes
        AND OpenCV-detected UI boxes (incl. text-less inputs/buttons) - inside the
        search area, draw them on a region-cropped, upscaled image, and let the
        model pick one by number. Returns (x,y) image space, or None to fall back
        to the 3x3 grid."""
        if not (MARKS_ENABLE and self._raw_frame and self._img_w):
            return None
        try:
            from io import BytesIO
            from PIL import Image, ImageDraw, ImageFont
        except Exception:  # noqa: BLE001
            return None
        W, H = self._img_w, self._img_h
        use_area = area if area and area != "any" else ""

        def _ok(b):
            return _in_region(b, region) and (not use_area or _in_area(b, use_area, W, H))

        cand = []
        for it in (items or []):
            if _ok(it):
                cand.append({"x0": it["x0"], "y0": it["y0"], "x1": it["x1"], "y1": it["y1"],
                             "cx": it["cx"], "cy": it["cy"], "label": (it.get("text") or "")[:22]})
        try:
            boxes = await asyncio.to_thread(ui_boxes.detect_boxes, self._raw_frame)
        except Exception:  # noqa: BLE001
            boxes = []
        for b in boxes:                       # text-less controls, skip OCR overlaps
            if _ok(b) and not any(ui_boxes._iou(b, c) > 0.6 for c in cand):
                cand.append({**b, "label": ""})
        if len(cand) < 2:
            return None

        rx, ry = ((region[0] + region[2]) / 2, (region[1] + region[3]) / 2) if region else (W / 2, H / 2)
        cand.sort(key=lambda c: (c["cx"] - rx) ** 2 + (c["cy"] - ry) ** 2)
        cand = cand[:MARKS_MAX]

        rx0, ry0, rx1, ry1 = map(int, region) if region else (0, 0, W, H)
        rx0, ry0 = max(0, rx0), max(0, ry0)
        rx1, ry1 = min(W, rx1), min(H, ry1)
        rw, rh = max(1, rx1 - rx0), max(1, ry1 - ry0)
        Z = max(1, min(3, int(1000 / rw)))
        try:
            base = Image.open(BytesIO(self._raw_frame)).convert("RGB")
            crop = base.crop((rx0, ry0, rx1, ry1))
            if Z > 1:
                crop = crop.resize((rw * Z, rh * Z))
            d = ImageDraw.Draw(crop)
            try:
                font = ImageFont.truetype("DejaVuSans-Bold.ttf", 15)
            except Exception:  # noqa: BLE001
                font = ImageFont.load_default()
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] marks draw failed: {e}")
            return None
        for i, c in enumerate(cand, 1):
            x0, y0 = (c["x0"] - rx0) * Z, (c["y0"] - ry0) * Z
            x1, y1 = (c["x1"] - rx0) * Z, (c["y1"] - ry0) * Z
            d.rectangle([x0, y0, x1, y1], outline=(255, 0, 0), width=2)
            tag = str(i)
            d.rectangle([x0, y0, x0 + 9 * len(tag) + 6, y0 + 17], fill=(255, 0, 0))
            d.text((x0 + 3, y0 + 1), tag, fill=(255, 255, 255), font=font)
        buf = BytesIO()
        crop.save(buf, format="PNG")
        self._save(task_id, "marks", buf.getvalue())

        listing = "; ".join(f"{i}:{c['label'] or 'ô'}" for i, c in enumerate(cand, 1))
        note = (f"Ảnh có các VÙNG BẤM ĐƯỢC đánh số (khung đỏ). Cần bấm vào: '{target}'. "
                f"Chữ gợi ý gần phần tử: {kws}. Số:nhãn = {listing}. "
                f"Phần tử cần bấm ở số mấy? (ô nhập trống thường KHÔNG có chữ). "
                f"Chỉ trả về MỘT con số, 0 nếu không có. /no_think")
        n = await self._marks_pick(task_id, buf.getvalue(), note)
        if n and 1 <= n <= len(cand):
            c = cand[n - 1]
            log(f"[{task_id}] marks: {len(cand)} vùng -> chọn #{n} "
                f"'{c['label'] or 'ô trống'}' @({c['cx']},{c['cy']})")
            return c["cx"], c["cy"]
        log(f"[{task_id}] marks: {len(cand)} vùng, model không chọn (n={n}) -> fallback 3x3")
        return None

    async def _marks_pick(self, task_id: str, img_bytes: bytes, note: str):
        """One Set-of-Mark LLM call: returns the chosen number (int) or None."""
        try:
            resp = await llm.chat(
                model=MODEL, think=THINK, options={"num_ctx": NUM_CTX},
                messages=[{"role": "user", "content": note, "images": [img_bytes]}])
            txt = (resp["message"].get("content") or "")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] marks LLM failed: {e}")
            return None
        import re
        mm = re.search(r"\d{1,3}", txt)
        if not mm:
            log(f"[{task_id}] marks: no number in {txt.strip()[:40]!r}")
            return None
        return int(mm.group(0))

    async def _grid_pick(self, task_id: str, level: int, img_bytes: bytes, note: str):
        """One grid LLM call: returns the chosen cell 1-9, 0 (not found), or None
        (error/no digit). Separate coroutine so it can run concurrently with OCR
        and be cancelled when OCR wins."""
        try:
            resp = await llm.chat(
                model=MODEL, think=THINK, options={"num_ctx": NUM_CTX},
                messages=[{"role": "user", "content": note, "images": [img_bytes]}])
            txt = (resp["message"].get("content") or "")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] locate L{level} LLM failed: {e}")
            return None
        import re
        mm = re.search(r"[0-9]", txt)
        if not mm:
            log(f"[{task_id}] locate L{level}: no digit in {txt.strip()[:40]!r}")
            return None
        return int(mm.group(0))

    async def _uia_find(self, task_id: str, kws: list, area: str = "any",
                        control: str = "any"):
        """(C) Ask the PC's UI Automation for a control; returns (sx,sy,name) in
        SCREEN pixels (name is "" if the PC did not report it), or None. The query the PC gets is "<ctype> <area> <name>": the
        model may pin a ControlType (e.g. edit) and/or a Name (keyword). Tries each
        keyword; if a control type is given it also tries a name-less query (any
        Edit in the area = the chat input, whose Name is dynamic). A hit outside
        the requested area / off-screen is rejected. Transport error -> fall back."""
        if not UIA_ENABLE:
            return None
        import re
        names = [k.strip() for k in (kws or []) if len((k or '').strip()) >= 2]
        if control != "any":
            names.append("")            # name-less: match any control of that type
        if not names:
            log(f"[{task_id}] uia: bỏ qua (không có keywords/control)")
            return None
        log(f"[{task_id}] uia: hỏi PC control={control} area={area} name={names} ...")
        use_area = area if area and area != "any" else ""
        for nm in names:
            query = f"{control} {area} {nm}".strip()
            try:
                res = await self.relay.call_device("uia", {"query": query}, task_id,
                                                   timeout=UIA_TIMEOUT)
            except asyncio.TimeoutError:
                log(f"[{task_id}] uia '{query}': timeout -> bỏ UIA, dùng vision")
                return None
            except Exception as e:  # noqa: BLE001
                log(f"[{task_id}] uia '{query}': {e} -> bỏ UIA, dùng vision")
                return None
            detail = (res.get("result") or {}).get("detail", "") or ""
            if not res.get("ok"):
                # e.g. ESP chưa flash lệnh 'uia' -> "tool not allowed"
                log(f"[{task_id}] uia '{query}': lỗi thiết bị ({res.get('error', detail)}) "
                    "-> bỏ UIA, dùng vision")
                return None
            # "x,y" or "x,y|<control name>" (the name is optional, see _tap).
            m = re.match(r"\s*(\d+)\s*,\s*(\d+)(?:\s*\|\s*(.*))?", detail)
            if m:
                sx, sy = int(m.group(1)), int(m.group(2))
                if self._scr_w and not (0 <= sx <= self._scr_w and 0 <= sy <= self._scr_h):
                    log(f"[{task_id}] uia '{query}' @({sx},{sy}) ngoài màn hình -> bỏ")
                    continue
                if use_area and self._scr_w and not _in_area({"cx": sx, "cy": sy},
                                                             use_area, self._scr_w, self._scr_h):
                    log(f"[{task_id}] uia '{query}' @({sx},{sy}) NGOÀI area '{area}' -> bỏ")
                    continue
                log(f"[{task_id}] uia '{query}' -> screen({sx},{sy})")
                return sx, sy, (m.group(3) or "").strip()
            log(f"[{task_id}] uia '{query}': {detail or 'none'}")
        log(f"[{task_id}] uia: không control hợp lệ -> dùng vision")
        return None

    async def _uia_marks(self, task_id: str, target: str, area: str = "any"):
        """UIA Set-of-Mark: PC numbers every interactive control on a screenshot
        (exact boxes from the a11y tree), uploads the annotated image, keeps the
        number->coords map. The LLM picks a number; the PC resolves it to real
        screen coords. Returns (sx,sy) in SCREEN pixels, or None to fall back."""
        if not (UIA_MARKS_ENABLE and self._scr_w):
            return None
        request_id = new_request_id()
        waiter = self.relay.arm_frame(request_id)
        try:
            res = await self.relay.call_device("uiamark", {"area": area}, task_id,
                                               timeout=max(UIA_TIMEOUT, 10),
                                               request_id=request_id)
        except asyncio.TimeoutError:
            log(f"[{task_id}] uiamark: timeout -> bỏ, dùng vision")
            return None
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] uiamark: {e} -> bỏ, dùng vision")
            return None
        detail = (res.get("result") or {}).get("detail", "") or ""
        if not res.get("ok"):
            log(f"[{task_id}] uiamark: lỗi thiết bị ({res.get('error', detail)}) -> vision")
            return None
        import re
        mm = re.search(r"(\d+)", detail)
        count = int(mm.group(1)) if mm else 0
        if count <= 0:
            log(f"[{task_id}] uiamark: 0 control (app không expose a11y?) -> vision")
            return None
        try:
            img = await asyncio.wait_for(waiter, 12)
        except asyncio.TimeoutError:
            img = None
        if not img:
            log(f"[{task_id}] uiamark: {count} control nhưng không nhận được ảnh -> vision")
            return None
        self._save(task_id, "uiamarks", img, "jpg")
        note = (f"Ảnh có {count} component TƯƠNG TÁC (nút/ô nhập/link) được đánh số trong "
                f"khung đỏ. Cần bấm vào: '{target}'. Component cần bấm ở số mấy? "
                f"Chỉ trả về MỘT con số, 0 nếu không có. /no_think")
        n = await self._marks_pick(task_id, img, note)
        if not n or not (1 <= n <= count):
            log(f"[{task_id}] uiamark: model không chọn (n={n}/{count}) -> vision")
            return None
        try:
            res2 = await self.relay.call_device("uiapt", {"n": n}, task_id, timeout=UIA_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] uiapt {n}: {e} -> vision")
            return None
        d2 = (res2.get("result") or {}).get("detail", "") or ""
        m2 = re.match(r"\s*(\d+)\s*,\s*(\d+)", d2)
        if not (res2.get("ok") and m2):
            log(f"[{task_id}] uiapt {n}: {d2 or 'none'} -> vision")
            return None
        sx, sy = int(m2.group(1)), int(m2.group(2))
        log(f"[{task_id}] uiamark: chọn #{n}/{count} -> screen({sx},{sy})")
        return sx, sy

    async def _tars_ground(self, task_id: str, target: str, area: str = "any",
                           anchor_kws: list = None):
        """(B) UI-TARS grounding: the GUI-specialist model looks at the screenshot
        and returns the click point for `target`. Returns (sx,sy) in SCREEN pixels,
        or None to fall back to the vision pipeline. Ollama swaps UI-TARS in for
        this one call (qwen is evicted), so it costs a model reload each way."""
        if not (UITARS_ENABLE and self._tars_ok and self._raw_frame and self._scr_w):
            return None
        try:
            from io import BytesIO
            from PIL import Image
        except Exception:  # noqa: BLE001
            return None
        # Scale the frame to a size we control so returned pixel coords map cleanly.
        try:
            im = Image.open(BytesIO(self._raw_frame)).convert("RGB")
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] tars: ảnh lỗi ({e}) -> vision")
            return None
        ow, oh = im.size
        if UITARS_FULLRES and self._scr_w and self._scr_h:
            # Send at real screen size: coords come back as screen pixels, so the
            # screen mapping below (coord*scr/sent) collapses to identity.
            sw, sh = self._scr_w, self._scr_h
            if (ow, oh) != (sw, sh):
                im = im.resize((sw, sh))
        else:
            scale = min(1.0, UITARS_MAX_SIDE / max(ow, oh)) if max(ow, oh) else 1.0
            sw, sh = max(1, round(ow * scale)), max(1, round(oh * scale))
            if scale < 1.0:
                im = im.resize((sw, sh))
        buf = BytesIO()
        im.save(buf, format="PNG")
        sent = buf.getvalue()

        hint = ""
        if area and area != "any":
            hint += f" (ở phía {area} màn hình)"
        if anchor_kws:
            hint += f" (gần chữ '{anchor_kws[0]}')"
        prompt = (
            "You are a precise GUI grounding model. Look at the screenshot and find "
            "the single UI element the user wants to interact with. Respond with ONLY "
            "a click action and nothing else.\n\n"
            "Output format: click(start_box='(x,y)') where x and y are pixel "
            "coordinates in the image.\n\n"
            f"Element to click: {target}{hint}")
        try:
            resp = await llm.chat(
                timeout=UITARS_TIMEOUT,
                model=UITARS_MODEL, think=False,
                options={"num_ctx": UITARS_NUM_CTX, "temperature": 0},
                messages=[{"role": "user", "content": prompt, "images": [sent]}])
            txt = (resp["message"].get("content") or "").strip()
        except asyncio.TimeoutError:
            log(f"[{task_id}] tars: timeout -> vision")
            return None
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            low = msg.lower()
            if "not found" in low or "no such model" in low or "try pulling" in low:
                self._tars_ok = False
                log(f"[{task_id}] tars: model '{UITARS_MODEL}' chưa có trong Ollama "
                    "-> tắt UI-TARS phiên này, dùng vision")
            else:
                log(f"[{task_id}] tars: lỗi ({msg}) -> vision")
            return None

        import re
        nums = re.findall(r"-?\d+\.?\d*", txt)
        if len(nums) < 2:
            log(f"[{task_id}] tars: không đọc được toạ độ từ {txt[:60]!r} -> vision")
            return None
        vals = [float(n) for n in nums]
        if len(vals) >= 4:            # a box -> click its centre
            cx, cy = (vals[0] + vals[2]) / 2, (vals[1] + vals[3]) / 2
        else:
            cx, cy = vals[0], vals[1]
        conv = UITARS_COORD
        if conv == "auto":
            # UI-TARS-1.5 emits ABSOLUTE pixel coords of the sent image (Qwen2.5-VL
            # convention). A value above the sent size can't be a pixel, so that
            # means the quant uses 0..1000 normalised (older UI-TARS). Note small
            # coords are ambiguous: pin UITARS_COORD explicitly after calibrating.
            conv = "norm1000" if (cx > sw * 1.02 or cy > sh * 1.02) else "image"
        if conv == "norm1000":
            sx = round(cx / 1000.0 * self._scr_w)
            sy = round(cy / 1000.0 * self._scr_h)
        else:                         # pixels of the sent frame
            sx = round(cx * self._scr_w / sw)
            sy = round(cy * self._scr_h / sh)
        sx = max(0, min(self._scr_w, sx))
        sy = max(0, min(self._scr_h, sy))
        log(f"[{task_id}] tars raw={txt[:50]!r} pt=({cx:.0f},{cy:.0f}) sent={sw}x{sh} "
            f"conv={conv} -> screen({sx},{sy})")
        return sx, sy

    async def _send_click(self, task_id: str, target: str, sx: int, sy: int, how: str) -> str:
        """Click a point already in SCREEN pixels and report."""
        try:
            res = await self.relay.call_device("click", {"x": sx, "y": sy, "button": 1},
                                               task_id, timeout=DEVICE_TIMEOUT)
        except asyncio.TimeoutError:
            return "error: thiết bị không phản hồi (timeout)"
        except Exception as e:  # noqa: BLE001
            return f"error: {e}"
        if res.get("ok"):
            self._invalidate_screen_cache()
            return f"ok: đã click '{target}' ({how}) tại ({sx},{sy}) — screenshot để xác minh"
        return f"error: {res.get('error', 'click thất bại')}"

    async def _finish_tap(self, task_id: str, target: str, sx: int, sy: int, how: str,
                          unverified: str = "") -> str:
        """Last step of every tap: ask first if a name could not be verified, mark
        the debug image, click."""
        if unverified:
            ok = await self._ask_confirm(
                task_id, "tap", {"target": target, "x": sx, "y": sy},
                message=f"Không xác minh được: {unverified}. Vẫn bấm '{target}' ({how})?")
            if not ok:
                return (f"error: người dùng không xác nhận bấm '{target}'. ĐỪNG gửi cho ai "
                        "khác; hãy hỏi lại người dùng.")
        if self._img_w and self._scr_w:
            self._mark_click(task_id, round(sx * self._img_w / self._scr_w),
                             round(sy * self._img_h / self._scr_h), f"tap_{target}")
        log(f"[{task_id}] tap '{target}' [{how}] -> screen({sx},{sy})")
        return await self._send_click(task_id, target, sx, sy, how)

    def _to_screen(self, ix: int, iy: int) -> tuple:
        if self._img_w and self._scr_w:
            return round(ix * self._scr_w / self._img_w), round(iy * self._scr_h / self._img_h)
        return ix, iy

    def _resolve_on_screen(self, names: list, items, area: str, relation: str = ""):
        """resolve_name over the OCR boxes, in-area and nearest-centre first. With
        below/bottom_of the box is an anchor heading a panel: top-most first."""
        W, H = self._img_w, self._img_h
        pool = list(items or [])
        if area and area != "any":
            pool = [it for it in pool if _in_area(it, area, W, H)] or pool
        if relation in ("below", "bottom_of"):
            pool.sort(key=lambda it: it["cy"])
        else:
            pool.sort(key=lambda it: (it["cx"] - W / 2) ** 2 + (it["cy"] - H / 2) ** 2)
        return resolve_name(names, pool)

    def _pick_name_anchor(self, res, names: list, kws: list, relation: str, items: list) -> dict:
        """A name can show in several places (feed post, chat list, chat header).
        Among the equally good matches take the one whose relative region holds
        the target's keywords; ties keep the _resolve_on_screen order."""
        top = name_rank(names, res.box["text"])
        cands = [c for c in res.candidates if name_rank(names, c["text"]) == top]
        if relation == "on" or len(cands) < 2 or not kws:
            return res.box
        best, best_score = res.box, -1.0
        for cand in cands:
            region = _relative_region(cand, relation, self._img_w, self._img_h)
            if region is None:
                continue
            score = max((max(_sim(k, it["text"]) for k in kws)
                         for it in items if _in_region(it, region)), default=0.0)
            if score > best_score:
                best, best_score = cand, score
        return best if best_score >= OCR_MATCH_MIN else res.box

    async def _confirm_name(self, task_id: str, target: str, res) -> str:
        """Ask the user about an uncertain name. Returns "" if accepted, else the
        tool error for the model."""
        best = res.box["text"]
        seen = ", ".join(f"'{b['text']}'" for b in res.candidates[:5])
        ok = await self._ask_confirm(
            task_id, "tap", {"target": target, "chosen": best},
            message=f"Không chắc tên cho '{target}'. Trên màn hình thấy: {seen}. Bấm vào '{best}'?")
        if ok:
            return ""
        return (f"error: người dùng không xác nhận '{best}' (thấy: {seen}). ĐỪNG bấm hay gửi "
                "cho người khác; hãy hỏi lại người dùng đúng tên.")

    def _anchor_search(self, task_id: str, a: dict, relation: str) -> tuple:
        """(pt, how, region, ctx) for a chosen anchor box."""
        if relation == "on":
            return (a["cx"], a["cy"]), "anchor", None, ""
        region = _relative_region(a, relation, self._img_w, self._img_h)
        log(f"[{task_id}] search region {relation} anchor: {region}")
        self._save_region(task_id, a, region)
        return None, "", region, f"(ảnh này là vùng {_REL_VI.get(relation, 'gần')} '{a['text']}')"

    async def _tap(self, task_id: str, args: dict) -> str:
        target = (args.get("target") or "").strip()
        if not target:
            return "error: thiếu 'target'"
        if not self._screen_cache_fresh():
            screenshot_result = await self._screenshot(task_id, force=False, attach=False)
            if screenshot_result.startswith("error") or not self._raw_frame:
                return screenshot_result

        kws = _keywords_from(target, args.get("keywords"))
        area = (args.get("area") or "any").lower()
        control = (args.get("control") or "any").lower()
        anchor_kws = _keywords_from("", args.get("anchor"))
        relation = (args.get("relation") or ("near" if anchor_kws else "on")).lower()
        if relation not in _REL_VI and relation != "on":
            relation = "near"

        # People's names are checked by OCR first, accent-aware: UIA and the vision
        # paths below cannot tell "Trường" from "Tường". Certain -> click; unsure
        # -> ask; not readable -> carry on, but ask before the final click.
        names = [k for k in kws if is_proper_name(k, self._task_text, target)]
        anchor_names = [k for k in anchor_kws if is_proper_name(k, self._task_text, target)]
        items, unverified = None, ""
        pt, how, region, ctx = None, "", None, ""
        if (names or anchor_names) and OCR_ENABLE:
            items = await self._ocr_items(task_id)
        if names:
            res = self._resolve_on_screen(names, items, area)
            log(f"[{task_id}] tên {names}: {res.status}"
                + (f" -> '{res.box['text']}'" if res.box else ""))
            if res.box:
                if res.status == "uncertain":
                    refused = await self._confirm_name(task_id, target, res)
                    if refused:
                        return refused
                sx, sy = self._to_screen(res.box["cx"], res.box["cy"])
                return await self._finish_tap(task_id, target, sx, sy, "OCR-name")
            unverified = f"không đọc được tên {names} trên màn hình"
        if anchor_names:
            # `area` describes the target; the anchor is usually elsewhere (a chat
            # header sits at the top while the input is at the bottom).
            res = self._resolve_on_screen(anchor_names, items, "any", relation)
            log(f"[{task_id}] tên mốc {anchor_names}: {res.status}"
                + (f" -> '{res.box['text']}'" if res.box else ""))
            if res.box:
                if res.status == "uncertain":
                    refused = await self._confirm_name(task_id, target, res)
                    if refused:
                        return refused
                anchor_box = self._pick_name_anchor(res, anchor_names, kws, relation, items)
                pt, how, region, ctx = self._anchor_search(task_id, anchor_box, relation)
            else:
                unverified = unverified or f"không đọc được tên mốc {anchor_names} trên màn hình"
                ctx = f"(phần tử nằm {_REL_VI.get(relation, 'gần')} chữ '{anchor_kws[0]}')"

        # (C) UIAutomation: the PC resolves the control's REAL screen coords by
        # ControlType and/or Name - exact and scale-free. Falls through on
        # canvas/no a11y. A named anchor restricts it to that anchor's region.
        if pt is None:
            uia = await self._uia_find(task_id, kws, area, control)
            if uia:
                sx, sy, uia_name = uia
                inside = region is None or not (self._img_w and self._scr_w) or _in_region(
                    {"cx": sx * self._img_w / self._scr_w, "cy": sy * self._img_h / self._scr_h},
                    region)
                if inside:
                    guard = unverified
                    if names and any(name_match(n, uia_name) in CERTAIN_MATCHES for n in names):
                        guard = ""
                    return await self._finish_tap(task_id, target, sx, sy, "uia", guard)
                log(f"[{task_id}] uia ({sx},{sy}) ngoài vùng của mốc -> bỏ")

        # Vision prep: OCR the screen once, then resolve the anchor + search region.
        if items is None and OCR_ENABLE:
            items = await self._ocr_items(task_id)

        # Anchor-relative search: find the anchor text by OCR, then search only
        # the box where the target should be (e.g. bottom of the chat panel
        # headed by a contact name). A small region makes both the OCR match and
        # the 3x3 locate far more accurate - even for a blank input box.
        if anchor_kws and not anchor_names:
            rel_vi = _REL_VI.get(relation, "gần")
            cands = self._find_anchor(task_id, items, anchor_kws, "any", relation) if items else []
            a = cands[0] if cands else None
            # Ambiguous anchor (e.g. a name shown both in a feed post and as a
            # chat-window header): pick the candidate whose relative region
            # actually contains the target keywords ('Aa' -> the chat input).
            if a and relation != "on" and len(cands) > 1 and kws:
                best_c, best_s = a, -1.0
                for c in cands:
                    reg = _relative_region(c, relation, self._img_w, self._img_h)
                    s = max((max(_sim(k, it["text"]) for k in kws)
                             for it in items if _in_region(it, reg)), default=0.0)
                    if s > best_s:
                        best_s, best_c = s, c
                if best_s >= OCR_MATCH_MIN and best_c is not a:
                    a = best_c
                    log(f"[{task_id}] anchor đa ứng viên -> chọn @({a['cx']},{a['cy']}) "
                        f"theo keyword khớp ({best_s:.2f})")
            if a:
                pt, how, region, ctx = self._anchor_search(task_id, a, relation)
            else:   # anchor not read by OCR: still tell the model where to look
                ctx = f"(phần tử nằm {rel_vi} chữ '{anchor_kws[0]}')"

        # Hybrid grounding: a VERY confident OCR keyword match wins BEFORE UI-TARS.
        # A text-labelled target ("Aa", "Gửi") is read more precisely by OCR than a
        # VLM points at it; UI-TARS then covers the text-less / icon targets.
        if pt is None and items:
            ex = await self._tap_ocr(task_id, target, kws, area, items, region,
                                     thr=OCR_EXACT_MIN)
            if ex:
                pt, how = ex, "OCR-exact"

        # (B) UI-TARS grounding: returns real screen coords. None -> vision.
        if pt is None:
            tg = await self._tars_ground(task_id, target, area, anchor_kws)
            if tg:
                return await self._finish_tap(task_id, target, tg[0], tg[1], "ui-tars", unverified)

        # UIA Set-of-Mark: let the LLM pick among numbered interactive controls.
        if pt is None:
            um = await self._uia_marks(task_id, target, area)
            if um:
                return await self._finish_tap(task_id, target, um[0], um[1], "uia-marks", unverified)

        # Full OCR keyword match (>= OCR_MATCH_MIN), then Set-of-Mark, then 3x3 grid.
        if pt is None and items:
            pt = await self._tap_ocr(task_id, target, kws, area, items, region)
            how = "OCR"
        if pt is None:
            mp = await self._tap_marks(task_id, target, kws, area, items, region)
            if mp:
                pt, how = mp, "marks"
        if pt is None:
            pt = await self._locate(task_id, target, kws, area, region, ctx)
            how = "locate" + ("+region" if region else "")
        if not pt:
            return f"error: không tìm thấy '{target}' trên màn hình (thử screenshot lại)"

        sx, sy = self._to_screen(*pt)
        return await self._finish_tap(task_id, target, sx, sy, how, unverified)

    async def _refine_point(self, task_id: str, x: int, y: int) -> tuple:
        """Coarse-to-fine: crop a generous box around the model's rough (x,y),
        zoom it, grid it with ORIGINAL-image coordinate labels, and ask the model
        for the exact point. Returns refined (x,y) in original image space, or the
        input unchanged on any failure. One extra LLM call per click."""
        if not (REFINE_CLICK and self._raw_frame and self._img_w):
            return x, y
        try:
            from io import BytesIO
            from PIL import Image, ImageDraw
        except Exception:  # noqa: BLE001
            return x, y
        W, H = self._img_w, self._img_h
        # Generous crop (half the image) so an off-target coarse guess still
        # contains the real element; centre it on (x,y), clamp to bounds.
        cw, ch = W // 2, H // 2
        x0 = max(0, min(W - cw, x - cw // 2))
        y0 = max(0, min(H - ch, y - ch // 2))
        try:
            im = Image.open(BytesIO(self._raw_frame)).convert("RGB").crop((x0, y0, x0 + cw, y0 + ch))
            Z = 2
            im = im.resize((cw * Z, ch * Z))
            d = ImageDraw.Draw(im)
            red = (255, 40, 40)
            stp = 50  # label every 50 original px
            gx = (x0 // stp + 1) * stp
            while gx < x0 + cw:
                px = (gx - x0) * Z
                d.line([(px, 0), (px, ch * Z)], fill=red, width=1)
                d.text((px + 1, 1), str(gx), fill=red)
                gx += stp
            gy = (y0 // stp + 1) * stp
            while gy < y0 + ch:
                py = (gy - y0) * Z
                d.line([(0, py), (cw * Z, py)], fill=red, width=1)
                d.text((1, py + 1), str(gy), fill=red)
                gy += stp
            out = BytesIO()
            im.save(out, format="PNG")
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] refine crop failed: {e}")
            return x, y

        note = (f"Nhiệm vụ: {self._task_text}. Bạn vừa chọn thô điểm ~({x},{y}). Đây là ảnh "
                f"PHÓNG TO vùng quanh đó; nhãn lưới đỏ = toạ độ PIXEL TRÊN ẢNH GỐC. Nhìn kỹ "
                "và trả về DUY NHẤT toạ độ chính xác của phần tử cần tương tác, dạng: x,y "
                "(pixel ảnh gốc). Không giải thích. /no_think")
        try:
            resp = await llm.chat(
                model=MODEL, think=THINK, options={"num_ctx": NUM_CTX},
                messages=[{"role": "user", "content": note, "images": [out.getvalue()]}])
            txt = (resp["message"].get("content") or "")
            import re
            m = re.search(r"(\d{1,4})\s*[,; ]\s*(\d{1,4})", txt)
            if m:
                rx = max(x0, min(x0 + cw, int(m.group(1))))
                ry = max(y0, min(y0 + ch, int(m.group(2))))
                log(f"[{task_id}] refine: ({x},{y}) -> ({rx},{ry}) [{txt.strip()[:40]}]")
                return rx, ry
            log(f"[{task_id}] refine: no coords parsed from {txt.strip()[:60]!r}")
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] refine LLM failed: {e}")
        return x, y
