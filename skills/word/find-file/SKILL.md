# [Word] Tìm và mở file bằng Windows Search

Dùng khi chỉ biết tên (hoặc một phần tên) file. Có đường dẫn đầy đủ thì xem skill mở file.

## Quy trình
1. `hotkey win+s` → `type <tên file>` (chỉ phần tên, không đuôi .docx, không dấu
   nháy) → `screenshot`. Kết quả chưa hiện thì chụp thêm một lần.
2. Đọc các dòng là **file Word**: icon Word, dòng phụ "Word · Last modified ..." (bản
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
- Windows Search chỉ thấy file đã được lập chỉ mục (Documents, Desktop...). Người dùng
  biết thư mục thì mở theo đường dẫn.
- Người dùng chọn "không file nào" → báo lại và dừng.
