# [Facebook] Bình luận & thả cảm xúc

## Xác định đúng bài
1. `screenshot`. Xác định bài người dùng muốn (theo người đăng / nội dung). Chưa thấy
   thì `scroll` và chụp lại. Không chắc là bài nào → `ask_user`, mỗi bài một lựa chọn (người đăng + vài chữ đầu).
2. Ghi nhớ **anchor của bài**: tên người đăng hoặc vài chữ đầu nội dung.

## Thả cảm xúc
- Thích: `tap` target "nút Thích của bài <...>", keywords ["Thích"],
  anchor [anchor của bài], relation "below".
- Cảm xúc khác (Yêu thích, Haha...): `move` chuột lên nút Thích, chờ một nhịp,
  `screenshot` để thấy dải biểu tượng, rồi `tap` biểu tượng (area quanh nút Thích).

## Bình luận
1. `tap` keywords ["Bình luận"], anchor [anchor của bài], relation "below".
2. Ô "Viết bình luận..." hiện dưới bài: `tap` keywords ["Viết bình luận",
   "Bình luận dưới tên"], anchor [anchor của bài], relation "below".
3. `type` nội dung → `screenshot`.
4. `key enter` → `screenshot` xác minh.

## Quy tắc
- Mỗi hàng nút Thích/Bình luận thuộc bài NGAY TRÊN nó — anchor phải là bài đúng,
  không bấm theo nút gần nhất trên màn hình.
- Chỉ tương tác với bài người dùng chỉ định; không thích/bình luận hàng loạt.
