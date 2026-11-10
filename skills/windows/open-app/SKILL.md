# [Windows] Mở ứng dụng / file / thư mục

## Quy trình
1. `screenshot`; app/file đã mở sẵn thì dùng luôn.
2. Mở theo tên (app, file, thư mục): `hotkey win+s` → `type <tên>` → `screenshot`.
   - Dòng đầu (Best match / Kết quả phù hợp nhất) đúng thứ cần mở → `key enter`.
   - Thứ cần mở ở dòng khác → `tap` keywords ["<tên đúng như trên ảnh>"].
   - Nhiều kết quả giống nhau → `ask_user`, mỗi kết quả một lựa chọn (tên + loại hoặc
     thư mục), rồi mở mục được chọn.
   - Không có kết quả phù hợp → `ask_user` hỏi tên hoặc đường dẫn khác.
3. Có đường dẫn đầy đủ: `hotkey win+r` (cần xác nhận) → `type <đường dẫn>` → `key enter`.
4. `screenshot` xác minh đã mở đúng.

## Quy tắc
- Dòng "'<chữ>' See more search results / Xem kết quả web" là tìm web, không phải
  app/file: không nhấn Enter khi nó đứng đầu.
- Chỉ mở đúng thứ người dùng yêu cầu; không chạy file lạ hay file thực thi đáng ngờ.
- Đường dẫn sai / báo lỗi trên ảnh: dừng và báo người dùng.
