# [Zalo] Mở cuộc trò chuyện với một người/nhóm

## Quy trình
1. `screenshot`. Nếu đầu khung chính đang ghi đúng tên người/nhóm cần → xong.
   Chưa thấy cửa sổ Zalo: `hotkey win+s` → `type Zalo` → `key enter` → `screenshot`.
2. Thấy tên trong danh sách bên trái → `tap` target "cuộc trò chuyện với <X> trong
   danh sách bên trái", keywords ["<tên X>"], area "left". → sang bước 4.
3. Không thấy → dùng tìm kiếm, ĐỪNG cuộn danh sách:
   - `tap` target "ô Tìm kiếm trên cùng cột trái", keywords ["Tìm kiếm"], area "left".
   - `hotkey ctrl+a` (xoá chữ tìm cũ nếu có) → `type <tên X>` → `screenshot`. Kết quả
     chưa hiện thì `screenshot` thêm một lần.
   - `tap` target "kết quả tìm kiếm <X>", keywords ["<tên X>"], area "left".
4. Xác minh: `zoom` area "top_bar" đọc **tên ở đầu khung trò chuyện** (khu vực chính),
   phải đúng TÊN X.
5. Tìm kiếm không có kết quả khớp → `ask_user` "Không tìm thấy <X> trong Zalo, ý bạn là
   ai?", options là các tên gần giống đang thấy (nếu có). KHÔNG dùng một cuộc trò chuyện
   đang hiển thị sẵn.

## Lưu ý định vị
- Anchor cho các bước sau là TÊN ở **ĐẦU khung trò chuyện**, không phải tên trong
  danh sách bên trái.
- keywords là **tên nhìn thấy**, chỉ gồm tên. Dòng chỉ nhắc tên trong đoạn tin cuối
  không phải cuộc trò chuyện với người đó.
- Nhiều người/nhóm trùng hoặc gần giống tên → `ask_user`, mỗi người một lựa chọn (tên
  + đoạn tin cuối), rồi mở người được chọn.
