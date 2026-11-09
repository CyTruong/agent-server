# [Chung] Định vị phần tử để bấm

## Công cụ `tap` (ưu tiên hơn click toạ độ)
`tap(target, keywords, area, anchor, relation)` — hệ thống tự tìm vị trí:
1. **OCR**: tìm chữ khớp `keywords` (không phân biệt dấu). Khớp rõ thì bấm ngay.
2. **Lưới 3×3 lồng nhau**: không có chữ khớp thì chia ảnh (hoặc vùng tìm) thành 9 ô,
   hỏi mục tiêu ở ô nào, phóng to và lặp tới khi đủ nhỏ.

## Điền tham số cho chính xác
- `target`: mô tả ngắn, cụ thể ("ô nhập tin nhắn khung chat Trường").
- `keywords`: chữ NHÌN THẤY trên hoặc sát phần tử, đúng chính tả như ảnh: tên người,
  nhãn nút, chữ gợi ý trong ô. Nhiều biến thể càng tốt (["Gửi", "Send"]).
- `area`: left / right / top / bottom / center nếu biết phần tử ở đâu.
- `anchor` + `relation`: khi mục tiêu **không có chữ** (ô trống, icon) hoặc **chữ bị trùng**
  nhiều nơi. Anchor = chữ của phần tử mốc gần đó; relation:
  - `below` / `above` / `left_of` / `right_of`: nằm phía đó so với mốc;
  - `bottom_of`: phần dưới cùng của khung nằm dưới mốc (ô nhập ở đáy khung chat);
  - `near`: quanh mốc; `on`: bấm chính mốc.
- Khi một chữ xuất hiện nhiều nơi (tên người ở danh sách và ở đầu khung chat), luôn
  thêm `area` hoặc `anchor` để tách đúng chỗ.

## Khi không trúng
- Kết quả `tap` có ghi cách định vị ([OCR] / [locate]). `screenshot` để kiểm tra.
- Sai: đổi keywords (biến thể khác, chữ ngắn hơn), thêm/đổi anchor, thu hẹp area.
- Chỉ dùng `click x y` khi đã chắc chắn toạ độ (đọc từ lưới trên ảnh mới nhất).
