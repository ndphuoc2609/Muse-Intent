# Review fixes — batch xác định (2026-11-05)

Sửa theo review ngoài (Phước gửi 2026-11-05, reviewer chưa kiểm chứng
code; Muse đã đối chiếu từng điểm với code main @ 3e43705 trước khi sửa).
Batch này chỉ gồm việc xác định (không đổi hành vi model ngoài hai field
planner mới); hai việc lớn còn lại xem cuối file.

## 1. Chunk versioning + seed purge + trạng thái index

- `RagChunk.source_sha`: SHA256 file nguồn của chunk (persona chunk ->
  `personas.json`, company chunk -> `dealers.json`). NULL = build trước
  khi có versioning.
- `scripts/build_rag_index.py` tính `persona_sha` / `company_sha`, đóng
  dấu từng chunk, ghi meta `persona_sha`, `company_sha`. Meta wipe trong
  build giữ lại `seed_persona_sha` (seed chạy ở đầu build đã ghi nó) —
  nếu không giữ, status sẽ kẹt ở `unknown` ngay sau build.
- `seed()` purge persona bị xóa nay xóa luôn `rag_chunks` của persona đó
  (trước `session.delete(persona)` để không vỡ FK Postgres); chunk
  company (`persona_id` NULL) không bị ảnh hưởng.
- `seed()` ghi meta `seed_persona_sha` = SHA `data/personas.json` lúc seed.
- `rag.index_status(session)` -> `ready` / `stale` / `unavailable` /
  `unknown` (+ `chunk_count`, hai SHA). Pipeline tính 1 lần mỗi lượt
  trong khối server retrieval và đưa vào trace (`rag_status`,
  `rag_chunk_count`, `rag_persona_sha`, `rag_seed_persona_sha`); lượt
  không chạy retrieval (gate skip, lỗi trước plan) giữ `not_run`.
- Trạng thái này chỉ bao phủ corpus persona: runtime không đọc
  `dealers.json` nên độ tươi của chunk đại lý không kiểm chứng được ở
  đây — chunk đại lý chỉ tươi lại sau mỗi lần build.
- Lọc chunk theo SHA lúc retrieval là không khả thi trước khi rebuild
  (mọi chunk đều mang SHA cũ, lọc sẽ trả về rỗng); v1 chọn phục vụ
  kèm cờ stale, không chặn lượt.

## 2. Declined writes

- Planner thêm `declined_question_keys` (qua `planner_normalize` +
  `plan_turn` schema + mô tả tool). `_update_questions` đánh dấu
  `DECLINED` khi planner nêu key; một key nằm cả hai danh sách thì
  ANSWERED thắng (đã có câu trả lời trong lượt).
- Validator §4 (`refused_question`) đã tồn tại nhưng chết từ trước vì
  `declined_keys` luôn rỗng; nay nó có dữ liệu thật. Lưu ý: luật này
  chỉ chặn hỏi lại qua `followup_key`, không chặn câu hỏi chôn trong
  text không gắn key.

## 3. Lead terminal state

- Bảng mới `lead_states` (unique theo `(thread, actor)`, status
  `refused` | `topic_changed` | `ended`). Không có row = chưa kết thúc.
- Latch lead (`_lead_capture`) nay tôn trọng trạng thái kết thúc: có row
  thì chỉ tín hiệu của lượt hiện tại mới tính, tín hiệu lịch sử bị khóa.
- Cuối lượt (sau `_update_questions`, chạy cả ở lượt review/handoff):
  opt-out -> `refused`; planner `lead_outcome` -> giá trị đó; tín hiệu
  mua mới mà planner không phán quyết terminal mới -> xóa row (mở lại).
  Phán quyết của planner thắng tín hiệu keyword trong cùng lượt.
- `lead_outcome` là phán quyết của planner, không phải keyword:
  `refused` = khách từ chối theo đuổi mua nữa; `topic_changed` = chuyển
  hẳn sang chuyện khác; `ended` = luồng lead đã kết thúc. Từ chối RIÊNG
  việc chia sẻ SĐT thì KHÔNG tính là `lead_outcome` mà là
  `declined_question_keys` của câu hỏi xin SĐT.
- Gate không đổi code: nó dùng `_lead_capture` của pipeline nên tự
  hưởng trạng thái kết thúc.

## 4. Negation guard cho claim story/rag/company

- `_story_matches` nay kiểm tra phủ định TRƯỚC cả `typed_match`:
  nguồn phủ định mà claim không phủ định (hoặc ngược lại) -> không khớp,
  bất kể trùng token cao đến đâu. Ví dụ review: nguồn "chưa từng chạy
  đường dài" vs claim "đã chạy đường dài" (trùng 6/7 token) nay bị chặn.
- Marker chỉ tính khi nó "đánh" vào nội dung của phía còn lại
  (`_negated_against`): một từ trong cửa sổ 2 token sau marker phải
  xuất hiện trong text kia. Nhờ vậy "đoạn ngập không rõ độ sâu" (từ sau
  `không` là `rõ`, không có trong claim) không bị tính là phủ định mệnh
  đề chính — bản guard đầu tiên chặn nhầm ca này và làm fail
  `test_storytold_dedupe` (claim mem_3), đã sửa + thêm test hồi quy với
  story thật từ seed.
- Ngoại lệ: `không chỉ` / `không những` là nhượng bộ; không dùng `đừng`
  vì sau khi bỏ dấu nó trùng `đúng`; phủ định kép không được giải quyết.
- Phạm vi: chỉ nhánh story/rag/company. Claim persona/product dùng
  `value_matches` (so theo kiểu) nên không chung lỗ này.

## 5. Evidence của CustomerFact phải đúng actor

- `_evidence_matches_server_message` nay lọc message theo
  `actor_id == actor hiện tại` (trước đây chỉ lọc thread + role user).
  Lời của Khách 2 trong cùng thread không còn làm evidence cho fact
  của Khách 1.

## Migration (DB đã có, vd Neon trên Mac của Phước)

- `migrations/postgres/007_review_fixes.sql` (idempotent) +
  `migrations/sqlite/007_review_fixes.sql` (chạy 1 lần; DB mới đã có
  schema qua `create_all`).
- Bảng `lead_states` tự tạo khi restart server (create_all); chỉ cột
  `rag_chunks.source_sha` là cần chạy file migration trên DB đã có.
- Sau khi pull code mới: chạy migration 007, rồi chạy lại
  `scripts/build_rag_index.py --provider tfidf --db "$DATABASE_URL"
  --company data/dealers.json` để chunk có SHA và status ra `ready`
  (thay vì `unknown`).

## Chưa làm (cần quyết định riêng)

- Phân tầng duyệt proposal: echo có evidence của khách thì tự kích hoạt;
  persona fact / story mới cần cửa duyệt riêng thay vì kích hoạt chỉ vì
  gửi thành công. (Hiện tại: proposal qua cửa kiểm tra cấu trúc /
  evidence / xung đột rồi mới chờ gửi — nhưng kích hoạt vẫn tự động.)
- Yêu cầu evidence cho đề xuất persona fact (hiện chỉ có policy key +
  chặn ghi đè, không yêu cầu evidence như CustomerFact).
- Retrieval bổ sung giữa lượt: model chưa có tool để gọi tìm RAG/company
  mới khi phát hiện thiếu dữ kiện (server retrieval chạy 1 lần/lượt;
  tool dữ liệu có cấu trúc thì gọi lại được).
- Phân rã claim story theo đối tượng/thuộc tính/giá trị/thời gian
  (thay cho guard phủ định + overlap token hiện tại).
