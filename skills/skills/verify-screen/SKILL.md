# Xác minh bằng ảnh màn hình

## Mục tiêu
Không báo hoàn thành khi chưa có bằng chứng nhìn thấy trên màn hình.

## Quy trình
1. Sau khi thực hiện một hành động (click/type/key), gọi `screenshot`.
2. Nhìn ảnh trả về và tự hỏi: trạng thái mong muốn đã xuất hiện chưa?
   - Ví dụ "mở Chrome": trên ảnh phải thấy cửa sổ Chrome.
   - Ví dụ "gõ văn bản": trên ảnh phải thấy đúng văn bản trong ô nhập.
3. Nếu đúng: tóm tắt kết quả và kết thúc (trả lời văn bản, không gọi thêm tool).
4. Nếu chưa đúng: thử một bước sửa (tối đa theo giới hạn retry), rồi chụp lại.

## Quy tắc
- "Lệnh được nhận (ok)" không đồng nghĩa với "đạt trạng thái mong muốn".
- Nếu `screenshot` không trả về ảnh, nói rõ là chưa xác minh được, đừng phỏng đoán.
- Dừng khi hết số bước hoặc hết thời gian; báo phần đã làm và phần chưa chắc chắn.
