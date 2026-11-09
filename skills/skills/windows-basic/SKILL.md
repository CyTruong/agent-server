# Thao tác Windows cơ bản

## Mục tiêu
Mở ứng dụng, tìm và bấm phần tử trên màn hình Windows một cách đáng tin cậy.

## Quy trình
1. Gọi `screenshot` để nhìn màn hình hiện tại trước khi làm bất cứ điều gì.
2. Mở ứng dụng: `key` tên `win`, chờ một nhịp, `type` tên ứng dụng (ví dụ "chrome"),
   rồi `key` `enter`. Sau đó `screenshot` để xác nhận đã mở.
3. Muốn bấm một nút/biểu tượng: nhìn ảnh screenshot, cho toạ độ tâm của phần tử
   theo **pixel trên chính ảnh đó** (hệ thống tự quy đổi ra màn hình thật), rồi
   `click x y 1`.
4. Sau mỗi lần mở/bấm, `screenshot` lại để kiểm tra kết quả đã đúng chưa.

## Quy tắc
- Luôn dựa vào ảnh screenshot mới nhất để lấy toạ độ; không đoán khi chưa nhìn.
- Toạ độ là pixel màn hình thật; nếu bấm trượt, chụp lại và chỉnh, đừng lặp mù.
- `key win` là hành động cần xác nhận của người dùng — chờ được cho phép.
- Phân biệt rõ "đã gửi lệnh" và "đã thấy kết quả đúng trên ảnh".
