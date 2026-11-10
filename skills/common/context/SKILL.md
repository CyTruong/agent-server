# [Chung] Xác định ngữ cảnh hiện tại khi yêu cầu chưa rõ nền tảng

Dùng khi yêu cầu KHÔNG nói rõ ứng dụng/nền tảng (vd "gửi tin nhắn cho Trường",
"trả lời giúp tôi", "tóm tắt tin"). ĐỪNG mặc định một nền tảng — hãy xác định cái
đang mở rồi mới hành động.

## Quy trình
1. `screenshot` TRƯỚC KHI làm gì khác.
2. Nhận diện ứng dụng đang ở TIỀN CẢNH từ ảnh:
   - **Thanh tiêu đề** cửa sổ trên cùng (vd "Zalo - <tên>"), hoặc tiêu đề tab trình
     duyệt ("... Facebook"). Chữ nhỏ → `zoom` area "title".
   - **Giao diện đặc trưng:** Zalo (danh sách trò chuyện trái + ô "Tìm kiếm" + ô
     "Nhập @, tin nhắn tới"); Facebook (thanh xanh "f", "Tìm kiếm trên Facebook",
     khung chat nổi góc phải dưới); hoặc app khác.
   - **Taskbar** dưới cùng: icon nào đang sáng/được chọn.
3. Khớp ngữ cảnh với yêu cầu:
   - "gửi/nhắn/trả lời/đọc tin" + đang mở một app chat (Zalo / Messenger / Facebook)
     → làm NGAY trên app đang mở đó.
   - Đang mở app KHÔNG liên quan → xem bước 4.
4. Khi không chắc / không có app phù hợp:
   - Có app chat đang mở → ưu tiên app ĐANG mở.
   - Không có app chat nào mở, hoặc nhiều cửa sổ chat → `ask_user` "Dùng nền tảng nào?"
     với options ["Zalo", "Facebook"]. Không tự đoán.

## Trước khi gửi/nhắn/đăng cho một "người" (áp dụng mọi nền tảng)
- **Không bịa người nhận.** Người/nhóm nhận phải là một liên hệ CÓ THẬT, xác minh
  được trên màn hình. Nếu tên nghe không giống người (vd "My Documents", "Downloads",
  tên thư mục/file/ứng dụng) hoặc bạn không chắc đó là ai → `ask_user`, đừng coi
  một mục trùng tên là người nhận.
- **Không gõ/gửi vào khung đang mở sẵn** chỉ vì tiện. Phải mở/đã mở ĐÚNG khung của
  người cần nhắn (tên khớp) rồi mới gõ.
- Tìm không ra người đó → `ask_user` "Không tìm thấy <tên>, ý bạn là ai?" (options là
  các tên gần giống đang thấy, nếu có); KHÔNG gửi đại cho ai khác.
- Người dùng không trả lời `ask_user` → kết thúc và nói rõ đang chờ họ chọn.

## Lưu ý
- Hướng dẫn chi tiết theo nền tảng chỉ được nạp khi yêu cầu có nhắc tên app. Nếu màn
  hình đang mở app mà yêu cầu không nhắc tên (vd đang mở Zalo nhưng không nói "zalo"):
  vẫn thao tác được dựa trên những gì NHÌN THẤY; nếu cần chính xác hơn, nhờ người dùng
  nêu rõ tên nền tảng để nạp đúng bộ hướng dẫn.
- Không tự mở app mới hay đổi tài khoản vì phỏng đoán. Yêu cầu nêu đích danh app (vd
  "zalo") mà app chưa mở thì được mở app đó.
