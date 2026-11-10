# [Word] Tìm nội dung liên quan (vừa đọc vừa tìm)

Dùng khi tìm bằng chữ (`ctrl+f` và các từ khóa) không ra, hoặc người dùng tả nội dung
bằng ý ("đoạn nói về ngân sách", "mục cam kết") chứ không đúng nguyên văn.

## Quy trình
1. Xác định điều cần tìm: ý chính + vài cách gọi khác (vd "ký xác nhận" ~ "chữ ký",
   "người lập", "đại diện các bên").
2. Thử tiêu đề trước: `hotkey ctrl+f` → `tap` tab keywords ["Tiêu đề", "Headings"] →
   `screenshot` đọc danh sách tiêu đề. Có tiêu đề hợp ý → `tap` vào tiêu đề đó → sang
   bước 5. Không có tiêu đề nào hợp → `key esc`.
3. Đọc dần từ đầu: `hotkey ctrl+home` → `screenshot` → `move` con trỏ vào giữa trang
   (KHÔNG bấm) → lặp: `scroll` amount âm (vd -8) → `screenshot` → so từng đoạn trên ảnh
   với điều cần tìm.
4. Dừng NGAY khi thấy đoạn khớp. Ảnh không đổi sau khi cuộn = đã hết tài liệu.
5. Đặt con trỏ: chắc chắn đúng đoạn → `tap` keywords ["<vài chữ ĐẦU dòng đó, đúng như
   trên ảnh>"] → `key home` hoặc `key end` → quay lại skill sửa. Nhiều đoạn có thể đúng
   → `ask_user`, mỗi đoạn một lựa chọn (vài chữ đầu + trang).
6. Đọc hết mà không thấy → `ask_user`: báo không tìm thấy, hỏi người dùng tả rõ hơn
   hay muốn THÊM MỚI nội dung đó (vd vào cuối tài liệu).

## Giới hạn
- Tối đa khoảng 8 màn hình; tài liệu dài hơn thì báo đã đọc tới trang nào.
- Trang nằm cạnh nhau hoặc cuộn bị chạy ngang → skill điều chỉnh hiển thị trước.
- Skill này chỉ tìm và đặt con trỏ; việc sửa làm theo skill sửa.
