# [Chung] Định vị phần tử để bấm

## Công cụ `tap` (ưu tiên hơn click toạ độ)
`tap(target, keywords, area, anchor, relation)` — hệ thống tự tìm vị trí (UIA, OCR kể cả
chụp vùng ở độ phân giải thật, rồi các lớp dự phòng). **Tên người phải đúng dấu**; không
chắc thì hệ thống hỏi người dùng trước khi bấm.

## Điền tham số cho chính xác
- `target`: mô tả ngắn, cụ thể ("ô nhập tin nhắn khung chat Trường").
- `keywords`, `anchor`: CHỈ gồm chữ NHÌN THẤY trên hoặc sát phần tử, đúng chính tả
  như ảnh, không thêm lời mô tả: tên người,
  nhãn nút, chữ gợi ý trong ô. Nhiều biến thể càng tốt (["Gửi", "Send"]).
- `area`: left / right / top / bottom / center nếu biết phần tử ở đâu.
- `anchor` + `relation`: khi mục tiêu **không có chữ** (ô trống, icon) hoặc **chữ bị trùng**
  nhiều nơi. Anchor = chữ của phần tử mốc gần đó; relation:
  - `below` / `above` / `left_of` / `right_of`: nằm phía đó so với mốc;
  - `bottom_of`: phần dưới cùng của khung nằm dưới mốc (ô nhập ở đáy khung chat);
  - `near`: quanh mốc; `on`: bấm chính mốc.

## Khi không trúng
- Kết quả `tap` có ghi cách định vị trong ngoặc. `screenshot` để kiểm tra.
- Sai: đổi keywords (biến thể khác, chữ ngắn hơn), thêm/đổi anchor, thu hẹp area.
- Chỉ dùng `click x y` khi đã chắc chắn toạ độ (đọc từ lưới trên ảnh mới nhất).
