# [Windows] Điền biểu mẫu web

## Quy trình
1. `screenshot` để thấy các ô cần điền; đọc nhãn của từng ô.
2. Từng ô: `tap` (keywords = nhãn hoặc chữ gợi ý của ô; ô trống thì anchor = nhãn,
   relation `right_of` hoặc `below`) → `type` giá trị → `key tab` sang ô kế.
3. Dropdown / checkbox: `tap` đúng lựa chọn theo chữ hiển thị.
4. `screenshot` rà lại toàn bộ giá trị.
5. Trước khi bấm Gửi/Submit: `ask_user` với lựa chọn ["Gửi", "Chưa gửi"].
6. `screenshot` xem trang xác nhận hoặc thông báo lỗi.

## Quy tắc
- Không tự điền tài khoản, mật khẩu, OTP, số thẻ/CCCD — để người dùng tự nhập.
- Chỉ dùng dữ liệu người dùng cung cấp; ô bắt buộc chưa rõ → `ask_user`.
