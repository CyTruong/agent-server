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

## Mẹo bấm chính xác (UI Automation) — gợi ý phụ, KHÔNG thay vision
UI Automation đôi khi bắt được nút theo nhãn/loại, nhưng nhãn Facebook hay ĐỔI theo
trạng thái (vd "Mở đoạn chat với X" có thể thành "Lựa chọn") nên KHÔNG đáng tin. Luôn
đặt keyword là tên X nhìn thấy; có thể thêm gợi ý phụ nếu muốn:
- Mở Messenger: keyword "Messenger", control "button", area "top".
- Chọn người trong danh sách/kết quả: control "listitem" + keyword tên X.
Nếu UIA không khớp, hệ thống tự dùng vision (OCR/lưới) theo keywords — nên keyword
tên X là BẮT BUỘC, `control` chỉ là tuỳ chọn.

## Lưu ý định vị
- Tên X có thể xuất hiện nhiều nơi (Người liên hệ, danh sách Messenger, bài đăng):
  luôn kèm area/anchor như trên để bấm đúng chỗ.
- Có nhiều người trùng tên → `ask_user`, mỗi người một lựa chọn (tên đầy đủ + thông tin
  phụ), rồi mở người được chọn.
