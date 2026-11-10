# [Word] Đóng tài liệu

## Quy trình
1. `screenshot`, kiểm tra thanh tiêu đề đúng file cần đóng. Không đúng thì `hotkey
   alt+tab` tìm cửa sổ của file; không có thì báo người dùng.
2. `hotkey ctrl+w` → `screenshot`.
3. Nếu Word hỏi lưu thay đổi:
   - Tác vụ này vừa sửa file, hoặc người dùng bảo lưu: `tap` keywords ["Lưu", "Save"].
   - Người dùng nói rõ không lưu: `tap` keywords ["Không Lưu", "Don't Save"].
   - Còn lại: `ask_user` "Có lưu thay đổi không?" với lựa chọn ["Lưu", "Không lưu",
     "Chưa đóng"]. Không tự bỏ thay đổi.
4. `screenshot` xác minh tài liệu đã đóng.

## Lưu ý
- `ctrl+w` chỉ đóng tài liệu. Chỉ khi người dùng muốn thoát hẳn Word mới dùng
  `hotkey alt+f4`.
- Tài liệu mới chưa từng lưu: làm theo phần lưu của skill tạo tài liệu.
