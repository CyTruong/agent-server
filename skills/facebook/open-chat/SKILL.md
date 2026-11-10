# [Facebook] Mở khung chat với một người

## Quy trình
1. `screenshot`. Nếu đã có khung chat mà đầu khung ghi đúng tên người đó → xong.
2. Cách A — tên có trong cột **"Người liên hệ"** bên phải:
   `tap` target "tên <X> trong Người liên hệ", keywords [tên X, các biến thể],
   area "right", anchor ["Người liên hệ"], relation "below".
3. Cách B — không thấy tên: bấm icon **Messenger** trên thanh trên cùng:
   `tap` target "icon Messenger trên thanh trên cùng", keywords ["Messenger"],
   area "top". Danh sách chat thả xuống ở phía phải; `screenshot`, rồi:
   - thấy tên: `tap` keywords [tên X], anchor ["Đoạn chat"], relation "below";
   - không thấy: `tap` ô "Tìm kiếm trên Messenger" (keywords ["Tìm kiếm trên
     Messenger"]), `type` tên X, `screenshot`, rồi `tap` đúng kết quả.
4. `screenshot` xác minh: có khung chat mới ở góc phải dưới, **đầu khung ghi tên X**.

## Mẹo bấm chính xác
- Mở Messenger: target "icon Messenger trên thanh trên cùng", area "top".
- Chọn người: target ghi rõ "tên X trong danh sách ...", keywords [tên X] — hệ thống đọc
  lại tên ở chỗ được chọn trước khi bấm.

## Lưu ý định vị
- Tên X có thể xuất hiện nhiều nơi (Người liên hệ, danh sách Messenger, bài đăng):
  luôn kèm area/anchor như trên để bấm đúng chỗ.
- Có nhiều người trùng tên → `ask_user`, mỗi người một lựa chọn (tên đầy đủ + thông tin
  phụ), rồi mở người được chọn.
