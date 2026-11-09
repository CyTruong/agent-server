# Điền biểu mẫu trên web

## Mục tiêu
Điền các ô của một biểu mẫu web (họ tên, email, nội dung...) rồi gửi khi được phép.

## Quy trình
1. `screenshot` để thấy toàn bộ biểu mẫu và các ô cần điền.
2. Với từng ô: `click` vào ô (toạ độ trên ảnh) → `type` giá trị → sang ô tiếp
   (`key tab` thường chuyển ô) hoặc `click` ô kế.
3. Chọn mục trong dropdown/checkbox: `click` đúng phần tử trên ảnh.
4. `screenshot` để rà lại toàn bộ giá trị đã điền.
5. **Trước khi bấm Gửi/Submit**: hành động hướng ra ngoài, không thu hồi được —
   hỏi xác nhận người dùng. Chỉ khi đồng ý mới `click` nút gửi.
6. `screenshot` xác minh kết quả (trang xác nhận / thông báo lỗi).

## Quy tắc
- **Không tự điền thông tin đăng nhập, mật khẩu, OTP, số thẻ/CCCD.** Nếu biểu mẫu
  yêu cầu các mục đó, dừng và để người dùng tự nhập.
- Chỉ dùng dữ liệu người dùng cung cấp; không bịa thông tin cá nhân.
- Nếu nhiều ô bắt buộc còn trống hoặc không rõ điền gì, hỏi lại trước khi gửi.
- Phân biệt rõ "đã điền xong" và "đã gửi thành công" (phải thấy xác nhận trên ảnh).
