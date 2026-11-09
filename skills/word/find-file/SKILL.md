# [Word] Tìm một file tài liệu

Người dùng đã cho đường dẫn đầy đủ thì bỏ qua skill này, sang skill mở file.

## Quy trình
1. `screenshot`. Đang có cửa sổ Word: `hotkey ctrl+f12` để mở hộp Mở file. Không có
   Word: `hotkey win+e` để mở Explorer.
2. Nếu người dùng nêu thư mục, vào thư mục đó trước: `hotkey ctrl+l` → `type <đường
   dẫn thư mục>` → `key enter`.
3. `tap` ô tìm kiếm góc trên bên phải: target "ô tìm kiếm ở góc trên bên phải",
   keywords ["Tìm kiếm", "Search"], area "top".
4. `type <từ khoá trong tên file>` → `key enter` → `screenshot`. Chưa có kết quả thì
   `screenshot` thêm tối đa 2 lần.
5. Chỉ tính file Word (`.docx`, `.doc`, cột loại "Microsoft Word"). Đọc đúng tên và
   thư mục từ ảnh.

## Kết quả
- Yêu cầu chỉ là tìm: báo tên và thư mục các file thấy được.
- Một file khớp rõ và cần làm tiếp: sang skill mở file.
- Nhiều file giống nhau: **DỪNG và HỎI** chọn file nào, nêu tên và thư mục từng file.
- Không có kết quả: báo "không tìm thấy <tên>", không mở đại file khác.
