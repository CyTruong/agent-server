# UI-TARS grounding (phương án B)

qwen3-vl:8b-instruct lo **instruction / hội thoại / quyết định**; UI-TARS (chuyên
GUI) lo **"bấm vào đâu"**. Chỉ 1 model nằm trong VRAM 1 lúc — Ollama tự swap
UI-TARS vào khi `tap`, rồi swap qwen lại cho lượt lý luận sau (3060 12GB không
chứa nổi cả hai).

## 1. Nạp UI-TARS vào Ollama

**Cách A — pull thẳng repo GGUF trên HuggingFace (khuyên dùng).** Bản GGUF multimodal
đã kèm sẵn mmproj (bộ mã hoá ảnh), Ollama 0.5+ tự nhận:

    ollama pull hf.co/Mungert/UI-TARS-1.5-7B-GGUF:Q4_K_M
    # hoặc: hf.co/Lucy-in-the-Sky/UI-TARS-1.5-7B-Q6_K-GGUF:Q6_K

Rồi trong `.env`:

    UITARS_ENABLE=true
    UITARS_MODEL=hf.co/Mungert/UI-TARS-1.5-7B-GGUF:Q4_K_M   # đúng y chuỗi đã pull

Kiểm tra model nhận ảnh (phải trả toạ độ, không nói "không thấy ảnh"):

    ollama run hf.co/Mungert/UI-TARS-1.5-7B-GGUF:Q4_K_M "mô tả ảnh này" <đường_dẫn_ảnh>

> Nếu bản Ollama cũ báo thiếu projector / không nhận ảnh: nâng cấp Ollama, hoặc
> dùng Cách B. (Việc auto-nhận mmproj từ repo HF phụ thuộc phiên bản Ollama —
> cần thử trên máy bạn.)

**Cách B — Modelfile tải tay (nếu Cách A không nhận ảnh).** Tải 2 file: model GGUF
và `*mmproj*.gguf` (F16) cùng repo, rồi:

    # Modelfile
    FROM ./UI-TARS-1.5-7B-Q4_K_M.gguf
    FROM ./UI-TARS-1.5-7B-mmproj-f16.gguf

    ollama create ui-tars -f Modelfile

Đặt `UITARS_MODEL=ui-tars`. (Cú pháp 2 dòng FROM cho vision tuỳ phiên bản Ollama;
nếu không chạy thì dùng llama.cpp `--mmproj` để kiểm model trước khi đưa vào agent.)

## 2. Hiệu chỉnh toạ độ (1 lần)

Chạy 1 lệnh `tap` thử (vd bấm ô tìm kiếm) rồi xem log:

    tars raw='click(start_box=...)' pt=(x,y) sent=WxH conv=... -> screen(sx,sy)

UI-TARS-1.5 trả **pixel tuyệt đối** → mặc định nên đặt `UITARS_COORD=image`.
Mở ảnh debug `..._tap_....png` xem **chấm đỏ** có nằm đúng phần tử không:
- Chấm **đúng** → giữ `image`.
- Chấm **lệch** (model trả 0..1000) → đổi `UITARS_COORD=norm1000`.
- `auto` chỉ nên dùng để dò: toạ độ nhỏ (cả x,y ≤ ảnh) dễ bị đoán nhầm, nên sau
  khi biết thì GHI CỐ ĐỊNH `image` hoặc `norm1000`.

## 3. Luồng & thứ tự trong `_tap`

1. Kiểm tra **tên người** bằng OCR (UI-TARS chỉ trả toạ độ, không phân biệt được tên).
2. `_uia_find` — UIA theo tên/loại (toạ độ native, chính xác tuyệt đối).
3. Phần tử **không có keywords**: `_tars_ground` — UI-TARS nhìn ảnh → toạ độ.
4. Phần tử **có keywords**: **OCR-exact** (>= `OCR_EXACT_MIN`, mặc định 0.9), không khớp
   thì mới hỏi UI-TARS. Điểm UI-TARS nằm ngoài vùng của mốc bị bỏ.
5. Fallback: UIA-marks → OCR (>=0.8) → marks → lưới 3x3 (qwen).

Log: `tap '...' [OCR-exact]` (OCR lo) hoặc `tap '...' [ui-tars]` (UI-TARS lo).

## 4. Lưu ý

- **Thrashing:** mỗi `tap` swap UI-TARS vào, mỗi lượt lý luận swap qwen lại → task
  nhiều bước sẽ nạp model đi-lại nhiều lần (vài giây/lần). Đây là cái giá của
  phương án B trên 12GB. Giảm bằng `OLLAMA_KEEP_ALIVE` hợp lý và gom thao tác.
- UI-TARS train chủ yếu UI tiếng Anh/Trung — nhãn **tiếng Việt** grounding có thể
  yếu hơn; OCR-vi vẫn là lớp fallback.
- `UITARS_ENABLE=false` → agent chạy y như cũ (vision), không gọi UI-TARS.
