# [Zalo] Mở cuộc trò chuyện với một người/nhóm

## Quy trình
1. `screenshot`. Nếu khung chính đang mở đúng tên người/nhóm cần → xong.
2. Tìm trong **DANH SÁCH bên trái trước** (ưu tiên, nhanh hơn tìm kiếm):
   a. Thấy tên trong danh sách → `tap` target "cuộc trò chuyện với <X> trong danh
      sách bên trái", keywords ["<tên X>"], area "left". → sang bước 4.
   b. CHƯA thấy → **lăn danh sách để tìm**: `move` con trỏ vào GIỮA cột danh sách
      bên trái (khoảng 1/6 chiều rộng màn, giữa chiều cao — vd màn 1920×1080 ~
      (200, 500)), rồi `scroll` amount **ÂM** (vd -3) để cuộn XUỐNG danh sách,
      rồi `screenshot` và dò lại tên.
   c. Lặp (b) **tối đa 2–3 lần**. Hễ thấy tên là `tap` ngay (như 2a).
3. Sau 2–3 lần cuộn **vẫn không thấy** → MỚI dùng tìm kiếm:
   - `tap` ô tìm kiếm: target "ô Tìm kiếm trên cùng cột trái",
     keywords ["Tìm kiếm"], area "left".
   - `type` "<tên X>" → `screenshot`.
   - `tap` đúng kết quả: target "kết quả tìm kiếm <X>", keywords ["<tên X>"],
     area "left".
4. `screenshot` xác minh: **đầu khung trò chuyện (khu vực chính) ghi đúng TÊN X**.
5. **Tìm kiếm không ra kết quả nào khớp** (danh sách lẫn ô tìm kiếm đều không có) →
   **DỪNG và báo người dùng** "không tìm thấy <X> trong Zalo", hỏi lại tên đúng.
   KHÔNG mở đại một cuộc trò chuyện đang hiển thị sẵn và coi như xong.

## Lưu ý định vị
- Anchor cho các bước sau là TÊN ở **ĐẦU khung trò chuyện** (khu vực chính), không
  phải tên trong danh sách bên trái.
- keywords là **tên nhìn thấy**; ô tìm kiếm khớp bằng chính chữ **"Tìm kiếm"**.
- Dùng `move` (KHÔNG phải `tap`) để đưa chuột vào danh sách rồi mới `scroll` — `move`
  chỉ di chuyển, không click nên không mở nhầm hội thoại. `scroll` tác động lên vùng
  dưới con trỏ, nên phải `move` vào cột trái trước.
- Nhiều người/nhóm trùng tên: nếu không chắc, **dừng và hỏi** người dùng chọn ai
  (mô tả ảnh đại diện / tên gợi nhớ), không đoán bừa.
