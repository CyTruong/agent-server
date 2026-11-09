# [Facebook] Bản đồ giao diện facebook.com (Chrome, tiếng Việt)

Dùng để biết phần tử thường nằm ở đâu, rồi điền `area`/`anchor` cho `tap`. Chữ hiển
thị có thể khác theo phiên bản — luôn đọc lại trên ảnh và dùng đúng chữ đang thấy.

## Thanh trên cùng (area "top")
- Trái: logo "f" và ô **"Tìm kiếm trên Facebook"**.
- Giữa: các biểu tượng Trang chủ, Video, Marketplace, Nhóm (icon, thường không có chữ).
- Phải (theo thứ tự trái → phải): Menu, **Messenger** (icon bong bóng chat),
  **Thông báo** (icon chuông, có số đỏ nếu có thông báo mới), ảnh đại diện.
  Các icon này không có chữ: dùng `area "top"` + target mô tả rõ icon; có số đỏ thì
  thêm keywords là con số đó.

## Cột trái (area "left")
Tên người dùng, "Bạn bè", "Nhóm", "Kỷ niệm", "Đã lưu", "Video"...

## Cột giữa (area "center")
- Trên cùng: ô đăng bài **"<Tên>, bạn đang nghĩ gì thế?"**.
- Bên dưới: các bài đăng. Mỗi bài: tên người đăng + thời gian ở đầu, nội dung, rồi
  hàng nút **"Thích" – "Bình luận" – "Chia sẻ"** ngay dưới bài.

## Cột phải (area "right")
Mục **"Người liên hệ"**: danh sách tên bạn bè đang hoạt động; bấm một tên để mở chat.

## Khung chat nổi (popup, góc phải dưới, area "bottom"/"right")
- Đầu khung: **tên người** đang chat (đây là anchor tốt nhất cho cả khung).
- Giữa: các tin nhắn. Đáy khung: ô nhập có chữ gợi ý **"Aa"**, bên phải là icon gửi.
- Có thể có nhiều khung chat cạnh nhau: luôn xác định đúng khung qua tên ở đầu khung.

## Quy tắc chung trên Facebook
- Chỉ thao tác trên tài khoản của người dùng, theo đúng yêu cầu; không tương tác hàng
  loạt (nhắn/bình luận/thích liên tục cho nhiều người), không mạo danh. Yêu cầu có
  dấu hiệu spam hay lừa đảo thì từ chối.
- Gửi tin, đăng bài, bình luận, thả cảm xúc là hành động công khai, khó thu hồi:
  soạn xong thì xin xác nhận trước khi bấm nút cuối.
