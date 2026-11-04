# Gate luật + cache FAQ (miễn phí, không gọi model)

Vị trí: `app/engine/gate.py`, nối vào `Pipeline.run` ngay sau khi đã có
`pending_qs` + `_lead_capture`, trước khi build prompt và gọi provider.
Bank: `data/faq.json` (Phước sở hữu, sửa trực tiếp file này để thêm câu).

## Thứ tự quyết định (hợp đồng, đừng đảo)

1. Đang có câu hỏi chờ của actor → chạy đủ pipeline (bình luận có thể là đáp án).
2. Có tín hiệu lead (lượt này hoặc lượt trước, theo `has_lead_signal`) → pipeline.
3. Bình luận rỗng / chỉ emoji / chỉ dấu câu → bypass, không trả lời.
4. Khớp danh sách phản hồi xác định (`NO_REPLY_EXACT`: ok, ừ, dạ, vâng, haha...)
   → bypass.
5. Khớp bank FAQ → trả câu trả lời đã duyệt, lưu message persona + Delivery
   như lượt `sent` bình thường (không gọi LLM, không tạo fact/question).
6. Còn lại → pipeline.

Hai override (1, 2) luôn thắng luật tiết kiệm: ví dụ "ok" kèm ý định mua
("ok, em mua Creta") vẫn đi pipeline, và câu trả lời cho câu hỏi chờ của
persona không bao giờ bị bypass hay FAQ cướp mất.

## Trạng thái mới

- `TurnStatus` thêm `"skipped"`: lượt bypass không tạo message persona nào,
  chỉ lưu comment khách vào lịch sử. UI hiện một dòng ghi chú xám
  "Đã bỏ qua" (chỉ ở client, không lưu DB) để khỏi tưởng là lỗi im lặng.
- Lượt FAQ có `status="sent"` như bình thường, trace ghi
  `gate: {action: "faq", entry_id}` để audit được.

## Bank FAQ: định dạng và luật khớp

- `match: "exact"`: toàn bộ bình luận (sau chuẩn hóa) phải bằng đúng một
  pattern. Dùng cho chào/cảm ơn — tránh "Chào bác, em hỏi giá..." bị cướp
  bởi entry chào (trường hợp đó còn bị lead override chặn trước).
- `match: "contains"`: mọi token của pattern đều có trong bình luận
  (thứ tự tự do). Pattern nên chứa từ hỏi ("không") để câu khẳng định
  không khớp nhầm.
- Chuẩn hóa: bỏ dấu, lower, map riêng đ→d (ký tự đ không phân rã dưới NFD
  nên `_strip` của rag/embeddings giữ nguyên nó; gate map thêm để một
  pattern khớp cả "đi" và "di").
- Câu trả lời trong bank phải an toàn-khái-quát: không số liệu, không giá,
  không claim về persona (không "xe mình..."). Câu nào cần chuyện riêng
  của persona hoặc dữ kiện sản phẩm thì KHÔNG đưa vào bank — để Luna trả.

## Bank hiện tại (5 entry, Phước duyệt/sửa)

- `fuel-generic`: tốn/ăn xăng không → không phán con số, tùy đường + cách chạy.
- `city-driving`: đi/chạy phố ổn không → tùy tuyến đường + chỗ đỗ.
- `warranty`: bảo hành mấy năm/thế nào → không rõ, hỏi trực tiếp đại lý.
- `greeting` (exact): chào bác/anh/chị/bạn, hi, hello → "Chào bạn nhé!"
- `thanks` (exact): cảm ơn (bác/anh/chị), thanks → "Không có gì nhé!"

Không có entry giá: câu hỏi giá mang tín hiệu lead nên lead override sẽ
đưa về pipeline trước khi FAQ kịp khớp — entry giá sẽ chết, đừng thêm.

## Giới hạn đã biết (v1)

- FAQ khớp theo token, chưa kiểm tra persona có story liên quan không:
  nếu persona có chuyện riêng về chủ đề đó, câu generic vẫn thắng. Muốn
  ưu tiên story thì bước tiếp theo là chạy story/RAG retrieval trước gate
  và chỉ FAQ khi không có evidence — chưa làm ở v1, bank đang hẹp nên
  rủi ro thấp.
- Contains khớp cả câu khẳng định chứa đủ token ("xe này không tốn xăng
  đâu" vẫn khớp `fuel-generic`) — đáp án generic nên vô hại, chỉ hơi lệch.
- Idempotency: lượt bypass ghi `turn_idempotency.status="skipped"` (không
  có persona_message_id); gửi lại cùng key sẽ chạy lại gate thay vì replay
  message — kết quả như nhau, không trùng comment khách.
