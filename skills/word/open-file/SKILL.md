# [Word] Mở một file tài liệu

Word CHỈ được mở file bằng Windows Search (skill tìm và mở file). KHÔNG dùng `ctrl+f12`,
`win+r` hay hộp Mở file, và KHÔNG BAO GIỜ tự đặt ra đường dẫn.

## Quy trình
1. `screenshot` TRƯỚC TIÊN. Thanh tiêu đề Word đã ghi đúng tên file → xong, KHÔNG mở
   lại (ngữ cảnh nói đã mở trước đó thì vẫn phải thấy trên ảnh).
2. Mở bằng skill **tìm và mở file bằng Windows Search** (`win+s`):
   - Người dùng chỉ nói tên → tìm theo tên đó.
   - Người dùng đưa đường dẫn đầy đủ → chỉ lấy TÊN file (phần sau dấu `\` cuối, bỏ
     `.docx`) để tìm; thư mục trong đường dẫn dùng để chọn đúng file khi có nhiều kết quả.
3. `screenshot` xác minh thanh tiêu đề ghi đúng tên file. Word mở chậm thì chụp lại
   tối đa 2 lần.
4. Cần sửa mà thấy dải "Protected View": bấm "Cho phép Chỉnh sửa / Enable Editing".

## Khi có sự cố
- Hộp thoại báo lỗi (vd "không tìm thấy", "Path does not exist"): đọc nội dung, đóng bằng
  `key enter` hoặc `tap` nút ["OK", "Đóng", "Close"]; KHÔNG bấm vào dòng chữ thông báo.
  Rồi báo người dùng đúng nội dung lỗi.
- File đang khoá hoặc "read-only": báo người dùng, không tự lưu đè bản khác.
- Không chạy file `.exe`, `.bat`, `.ps1` hay macro; thấy cảnh báo macro thì KHÔNG bật.
