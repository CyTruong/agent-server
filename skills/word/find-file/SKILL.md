# [Word] Tìm và mở file bằng Windows Search

Cách DUY NHẤT để mở file Word. Có đường dẫn thì chỉ lấy tên file để tìm (xem skill mở
file); không tự đặt đường dẫn.

## Quy trình
1. `hotkey win+s` → `type <tên file>` (KHÔNG gõ "Word": tìm thẳng tên file, không đuôi)
   → `screenshot`. KHÔNG nhấn Enter trước khi nhìn kết quả.
2. `zoom` box quanh danh sách kết quả (theo lưới) để đọc rõ tên. Đọc các dòng là **file
   Word**: icon Word, dòng phụ "Word · Last modified ..." (bản
   tiếng Việt: "Word · Sửa đổi lần cuối ..."). Bỏ qua thư mục (Folders), file Excel/PDF
   và dòng "'<chữ>' See more search results / Xem thêm kết quả" (đó là tìm web).
3. So tên: bỏ qua hoa/thường, dấu tiếng Việt, khoảng trắng, dấu '-' '_'. Tên dài bị cắt
   bằng "..." thì so phần đang thấy.
4. Quyết định:
   - **Đúng MỘT file Word khớp** → mở luôn, không hỏi:
     - Nó là dòng đầu ở mục "Best match / Kết quả phù hợp nhất" → `key enter`.
     - Nó ở dòng khác → `tap` target "dòng file Word <tên>", keywords ["<tên đúng như
       trên ảnh, kể cả dấu ...>"], area "left".
   - **Nhiều file khớp hoặc giống nhau** → `ask_user` question "Có nhiều file giống
     '<tên>', mở file nào?", options mỗi file một dòng "<tên như trên ảnh> — <ngày sửa>".
     Mở đúng file được chọn như trên.
   - **Không có file Word nào**: thử lại MỘT lần với 2–3 chữ đầu của tên (`hotkey ctrl+a`
     trong ô tìm rồi `type`). Vẫn không có → `ask_user` hỏi tên khác hoặc thư mục chứa
     file. KHÔNG mở một file khác.
   - Yêu cầu chỉ là tìm, không mở: báo danh sách file thấy được rồi `key esc`.
5. `screenshot` xác minh thanh tiêu đề Word ghi đúng tên file (Word mở chậm thì chụp lại
   tối đa 2 lần).

## Lưu ý
- Windows Search chỉ thấy file đã được lập chỉ mục (Documents, Desktop...). Không thấy
  thì `ask_user` hỏi tên khác; không mở bằng đường dẫn.
- Nhấn Enter khi chưa xem kết quả sẽ bị hệ thống chặn và chụp màn hình thay.
- Người dùng chọn "không file nào" → báo lại và dừng.
