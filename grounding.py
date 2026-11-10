"""Where to click, and what the screen says. There is no OCR:

  * UI-TARS points at the element for every tap.
  * The main vision model (qwen3-vl) reads text - people's names, window titles,
    search results - from captures at native resolution.

People's names are the risky part: UI-TARS returns only a point, so after it
points at a person, the vision model reads that spot back and the name is
compared accent by accent before anything is clicked.

TaskRunner inherits this mixin; it relies on the runner's relay, frame state,
_screenshot, _screen_cache_fresh, _invalidate_screen_cache, _capture_region,
_ask_confirm and _ask_choice.
"""
import asyncio
import os
import re
import time

import llm
from agent_log import log
from config import (
    DEVICE_TIMEOUT,
    IMAGE_DIR,
    MODEL,
    NUM_CTX,
    REFINE_CLICK,
    SAVE_IMAGES,
    THINK,
    UITARS_COORD,
    UITARS_FULLRES,
    UITARS_MAX_SIDE,
    UITARS_MODEL,
    UITARS_NUM_CTX,
    UITARS_TIMEOUT,
)
from imaging import upscale
from matching import (
    CERTAIN_MATCHES,
    _REL_VI,
    _keywords_from,
    is_proper_name,
    is_ui_label,
    name_match,
    name_matches_request,
)
from regions import TAP_AREAS, fraction_box

# Zalo's chat header (name of the open conversation), as screen fractions.
ZALO_HEADER = (0.22, 0.0, 0.80, 0.12)
# Around the point UI-TARS picked, read back this many screen pixels (w, h).
NAME_READ_BOX = (460, 80)


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
            device_id = getattr(self, "device_id", "")
            folder = os.path.join(IMAGE_DIR, self._safe_tag(device_id)) if device_id else IMAGE_DIR
            os.makedirs(folder, exist_ok=True)
            fn = os.path.join(folder,
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

    # --- reading (vision model) --------------------------------------------------

    async def _read_image(self, task_id: str, image: bytes, purpose: str = "") -> str | None:
        """The vision model copies the text of an image, accents kept. It is never
        told what text is expected, so it cannot simply agree. None on failure."""
        prompt = ("Chép lại NGUYÊN VĂN mọi chữ nhìn thấy trong ảnh, đúng dấu tiếng Việt, theo "
                  "thứ tự từ trên xuống, mỗi dòng một dòng. Không giải thích, không thêm chữ."
                  + (f" Ảnh là {purpose}." if purpose else "") + " /no_think")
        try:
            resp = await llm.chat(model=MODEL, think=False, options={"num_ctx": NUM_CTX},
                                  messages=[{"role": "user", "content": prompt, "images": [image]}])
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] model đọc chữ lỗi: {e}")
            return None
        text = (resp["message"].get("content") or "").strip()
        log(f"[{task_id}] model đọc ({purpose or 'vùng'}): {text[:80]!r}")
        return text

    async def _read_region(self, task_id: str, x: int, y: int, w: int, h: int,
                           purpose: str = "") -> str | None:
        """Capture a screen rectangle (native resolution when the device can) and
        let the vision model read it."""
        image, _scale, native = await self._capture_region(task_id, x, y, w, h)
        if image is None:
            return None
        self._save(task_id, f"read_{purpose or 'region'}", image,
                   "jpg" if image[:2] == b"\xff\xd8" else "png")
        return await self._read_image(task_id, image if native else upscale(image), purpose)

    async def _read_around(self, task_id: str, sx: int, sy: int) -> str | None:
        """Text around a screen point (the row or label UI-TARS picked)."""
        w, h = NAME_READ_BOX
        x = max(0, min(self._scr_w - w, sx - w // 2))
        y = max(0, min(self._scr_h - h, sy - h // 2))
        return await self._read_region(task_id, x, y, w, h, "một dòng/nhãn trên giao diện")

    async def _read_zalo_header(self, task_id: str) -> str | None:
        """Name at the top of Zalo's open conversation."""
        if not self._scr_w:
            return None
        x, y, w, h = fraction_box(ZALO_HEADER, self._scr_w, self._scr_h)
        return await self._read_region(task_id, x, y, w, h, "đầu khung trò chuyện Zalo")

    @staticmethod
    def _name_in(names: list, text: str | None) -> bool:
        """Does any line of `text` show one of `names` exactly (accents included)?"""
        for line in (text or "").splitlines():
            if any(name_match(n, line.strip()) in CERTAIN_MATCHES for n in names):
                return True
        return False

    # --- pointing (UI-TARS) ----------------------------------------------------

    async def _tars_ground(self, task_id: str, target: str, area: str = "any",
                           anchor_kws: list = None, relation: str = "",
                           keywords: list = None):
        """UI-TARS looks at the screenshot and returns the click point for
        `target` in SCREEN pixels, or None. Ollama swaps UI-TARS in for this call
        (the main model is evicted), so it costs a model reload each way."""
        if not (self._tars_ok and self._raw_frame and self._scr_w):
            return None
        try:
            from io import BytesIO
            from PIL import Image
            im = Image.open(BytesIO(self._raw_frame)).convert("RGB")
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] tars: ảnh lỗi ({e})")
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

        hints = []
        if keywords:
            hints.append("its visible text: " + ", ".join(f"'{k}'" for k in keywords))
        if anchor_kws:
            where = _REL_VI.get(relation, "near") if relation and relation != "on" else "on"
            hints.append(f"position: {where} the text '{anchor_kws[0]}'")
        if area and area != "any":
            hints.append(f"in the {area} part of the screen")
        prompt = (
            "You are a precise GUI grounding model. Look at the screenshot and find "
            "the single UI element the user wants to interact with. Respond with ONLY "
            "a click action and nothing else.\n\n"
            "Output format: click(start_box='(x,y)') where x and y are pixel "
            "coordinates in the image.\n\n"
            f"Element to click: {target}" + (f" ({'; '.join(hints)})" if hints else ""))
        try:
            resp = await llm.chat(
                timeout=UITARS_TIMEOUT,
                model=UITARS_MODEL, think=False,
                options={"num_ctx": UITARS_NUM_CTX, "temperature": 0},
                messages=[{"role": "user", "content": prompt, "images": [sent]}])
            txt = (resp["message"].get("content") or "").strip()
        except asyncio.TimeoutError:
            log(f"[{task_id}] tars: timeout")
            return None
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            low = msg.lower()
            if "not found" in low or "no such model" in low or "try pulling" in low:
                self._tars_ok = False
                log(f"[{task_id}] tars: model '{UITARS_MODEL}' chưa có trong Ollama -> "
                    "không bấm được bằng tap trong phiên này")
            else:
                log(f"[{task_id}] tars: lỗi ({msg})")
            return None

        nums = re.findall(r"-?\d+\.?\d*", txt)
        if len(nums) < 2:
            log(f"[{task_id}] tars: không đọc được toạ độ từ {txt[:60]!r}")
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

    # --- clicking ----------------------------------------------------------------

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
            where = ""
            if self._img_w and self._scr_w:   # the units the model reads off the grid
                where = (f" tại điểm ({round(sx * self._img_w / self._scr_w)},"
                         f"{round(sy * self._img_h / self._scr_h)}) trên ảnh")
            self._invalidate_screen_cache()
            return f"ok: đã click '{target}' ({how}){where} — screenshot để xác minh"
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

    def _is_zalo_task(self) -> bool:
        return "zalo" in self._task_text.lower()

    async def _tap(self, task_id: str, args: dict) -> str:
        target = (args.get("target") or "").strip()
        if not target:
            return "error: thiếu 'target'"
        if not self._screen_cache_fresh():
            shot = await self._screenshot(task_id, force=False, attach=False)
            if shot.startswith("error") or not self._raw_frame:
                return shot

        kws = _keywords_from(target, args.get("keywords"))
        area = (args.get("area") or "any").lower()
        anchor_kws = _keywords_from("", args.get("anchor"))
        relation = (args.get("relation") or ("near" if anchor_kws else "on")).lower()
        if relation not in _REL_VI and relation != "on":
            relation = "near"

        # People's names: UI-TARS cannot tell "Trường" from "Tường", so every name is
        # checked by reading the screen back. In Zalo the anchor of the message input
        # is by definition the recipient.
        names = [k for k in kws if is_proper_name(k, self._task_text, target)]
        zalo_input = relation == "bottom_of" and self._is_zalo_task()
        anchor_names = [k for k in anchor_kws if is_proper_name(k, self._task_text, target)
                        or (zalo_input and not is_ui_label(k))]
        suspect = [k for k in names + anchor_names if k not in self._approved_names
                   and not name_matches_request(k, self._task_text)]
        if suspect:
            ok = await self._ask_confirm(
                task_id, "tap", {"target": target, "names": suspect},
                message=f"Yêu cầu là '{self._task_text}' nhưng sắp thao tác với {suspect}. "
                        "Có đúng người không?")
            if not ok:
                return (f"error: người dùng không xác nhận {suspect} là người được yêu cầu. "
                        "ĐỪNG bấm hay gửi; hãy hỏi lại đúng tên.")
            self._approved_names.update(suspect)

        if self._is_zalo_task() and ((names and area == "left") or anchor_names):
            header = await self._read_zalo_header(task_id)
            # Opening a conversation that is already open: nothing to click.
            if names and area == "left" and self._name_in(names, header):
                log(f"[{task_id}] khung '{names[0]}' đã mở sẵn -> không bấm")
                return (f"ok: khung trò chuyện với '{names[0]}' đã mở sẵn (tên ở đầu khung "
                        "khớp), không cần bấm. Làm tiếp bước sau.")
            # The input under a chat header must belong to the right person.
            if anchor_names and not self._name_in(anchor_names, header):
                ok = await self._ask_confirm(
                    task_id, "tap", {"target": target, "header": header},
                    message=(f"Đầu khung trò chuyện đang là '{(header or '?')[:60]}', không "
                             f"đọc được {anchor_names}. Vẫn bấm '{target}'?"))
                if not ok:
                    return (f"error: khung đang mở không phải của {anchor_names}. Mở đúng "
                            "cuộc trò chuyện trước (skill mở cuộc trò chuyện).")
                self._approved_names.update(anchor_names)

        point = await self._tars_ground(task_id, target, area, anchor_kws, relation, kws)
        if not point:
            return (f"error: UI-TARS không tìm thấy '{target}'. screenshot lại, mô tả rõ hơn "
                    "(loại phần tử + vị trí + chữ trên nó) hoặc dùng cách khác.")
        sx, sy = point
        if area == "top" and sy > TAP_AREAS["top"][3] * self._scr_h:
            return (f"error: UI-TARS chỉ vào ({sx},{sy}), không nằm ở vùng trên cùng như yêu cầu "
                    "(ribbon/thanh công cụ). zoom area \"top_bar\" để đọc đúng nhãn rồi thử lại.")

        # A person picked by UI-TARS: read the spot back and compare the name.
        if names and not all(n in self._approved_names for n in names):
            seen = await self._read_around(task_id, sx, sy)
            if not self._name_in(names, seen):
                shown = " / ".join(line.strip() for line in (seen or "").splitlines()[:2])
                ok = await self._ask_confirm(
                    task_id, "tap", {"target": target, "seen": shown},
                    message=(f"UI-TARS chọn chỗ có chữ '{shown or '?'}' cho '{target}', không "
                             f"chắc là {names}. Bấm vào đó?"))
                if not ok:
                    return (f"error: chỗ UI-TARS chọn ghi '{shown}', không phải {names}. ĐỪNG "
                            "bấm hay gửi cho người khác; tìm đúng tên (vd ô tìm kiếm) hoặc "
                            "ask_user.")
                self._approved_names.update(names)
            else:
                log(f"[{task_id}] tên {names} khớp chỗ UI-TARS chọn")
        return await self._finish_tap(task_id, target, sx, sy, "ui-tars")

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
