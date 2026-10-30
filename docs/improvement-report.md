# Báo cáo cải thiện — branch improve/claim-history-idempotency

Ngày: 2026-10-30. Base: `main` @ `0b70ea071242da592e649b629abc9d0d66ae681f`
(phiên bản đã có fix history last-20 + idempotency UI badge).
Branch: `improve/claim-history-idempotency` (từ base trên, không force-push,
không chạm `main`, không chạm repo MAT_Intent cũ, không tích hợp Facebook/Meta thật).

## Tóm tắt: đã sửa cái gì, vì sao

Không viết lại engine, không thêm vector DB / multi-agent / verifier call
cho mọi lượt, không hardcode câu trả lời cho ví dụ trong brief, không giảm
validation để tăng send rate.

| # | Lỗi đã đối chiếu với code (không phải giả thuyết) | Sửa | Bằng chứng |
|---|---|---|---|
| 1 | Validator so khớp chung: `5999 triệu` khớp `599 triệu` (overlap token 1/2 = 0.5) | `app/engine/typed_values.py` mới + validator so theo kiểu: tiền về VND, %/km/ngày theo kiểu; `599 triệu` = `599.000.000 đồng`, `5999 triệu` ≠ `599 triệu` | `tests/test_improvement.py` (typed money) |
| 2 | Câu có dữ kiện nhưng `claims=[]` vẫn qua validator | Validator hai chiều: claim phải có nguồn đúng + khẳng định tiền/ngày/%/km/màu xe/sở hữu xe trong văn bản phải được claim bao phủ; câu hỏi, phủ định, nhắc lại ý khách, xã giao không bị tính là khẳng định | `tests/test_improvement.py` (uncovered), eval 2 case validator |
| 3 | Model bỏ qua planner, mutation trước plan, text thuần đi vòng kiểm chứng | Pipeline chặn tool trước `plan_turn` (không thực thi, trả UNKNOWN); text thuần chỉ được chấp nhận khi là xã giao thuần túy, còn lại là lỗi protocol, không gửi | `tests/test_improvement.py` (planner-first, plain-text) |
| 4 | `QuestionState`: query chỉ lấy `pending` rồi kiểm tra `declined` nên `refused_keys` luôn rỗng; chuyển chủ đề tự đổi pending → redirected làm mất câu hỏi | Load cả pending + declined, đưa declined vào context + validator; chỉ ANSWERED khi planner nêu key; chuyển chủ đề giữ nguyên pending | `tests/test_improvement.py` (topic change) |
| 5 | Followup QuestionState được tạo trước delivery, kể cả lượt review/error (chưa từng gửi cho khách đã tính là "đã hỏi") | Followup chỉ tạo khi delivery thành công, kèm `asked_message_id` | `tests/test_improvement.py` (followup not on failed delivery) |
| 6 | Proposal commit ngay thành canon active trước khi reply được chấp nhận/gửi | Proposal ở trạng thái `pending`, không xuất hiện trong `get_*` (active only); chỉ `activate_pending()` khi sent, ngược lại `reject_pending()` (review/error/sender-fail) | `tests/test_improvement.py` (pending/activate/reject) |
| 7 | Customer fact supersede chỉ theo subject+predicate, bố vs mẹ cùng `third_person` ghi đè nhau; `message_id=None`, evidence do model tự viết không đối chiếu | Supersede chỉ khi cùng subject+predicate+relation, chỉ ở activation; `message_id` = user message do server tạo; evidence phải khớp message do server xác định (substring hoặc overlap token ≥ 0.7) | `tests/test_improvement.py` (bố/mẹ, evidence) |
| 8 | Idempotency lookup toàn cục theo key: cùng key khác payload vẫn replay reply cũ | Bảng `turn_idempotency` với unique theo (thread, actor, key) + `request_hash`; cùng key + cùng payload → replay, khác payload → `idempotency_conflict` rõ ràng, khác thread → lượt mới. `Message.idempotency_key` là legacy unique toàn cục nên lượt mới để NULL, tránh user+persona cùng key vi phạm unique | `tests/test_safety.py` (đã cập nhật theo scope mới), `tests/test_improvement.py` |
| 9 | Story kể lại không được ghi delivery (chỉ story vừa tạo mới có `told_in_message_id`) | Bảng `story_told` (persona+actor+thread+message, unique) ghi mọi story claim trong final đã gửi; `told_in_message_id` giữ để tương thích | `tests/test_improvement.py` (story_told) |
| 10 | Provider cost không trả về bị coi là 0; HTTP retry không được đếm; cấu hình openrouter thiếu key vẫn chạy Fake im lặng | Trace ghi `cost_known`, `cost_unknown_calls`, `http_attempts`; budget số call + HTTP attempt luôn áp; `make_provider()` raise khi openrouter thiếu key, API `/health` báo `misconfigured`, turns trả 502 thay vì chạy Fake | `tests/test_improvement.py` (cost unknown) |
| 11 | Context không phân biệt actor: history không ghi ai nói, customer facts và câu hỏi của actor khác có thể bị gán nhầm | `_context_block` ghi actor hiện tại, history dạng `role [actor_id]`, tách "lịch sử chung (công khai)" vs "customer facts RIÊNG của actor hiện tại" + "câu hỏi đã từ chối (không hỏi lại)" | Eval case two-actors, unit test actor isolation |
| 12 | Product fact mẫu có thể lẫn với giá thật | Validator chặn claim product `is_sample` khi văn bản không ghi rõ là mẫu ("mẫu" / "chưa phải giá thực tế") | `tests/test_improvement.py` (sample unlabelled) |

Không sửa theo hướng regex/cây câu mẫu thay model: model vẫn quyết định
cách hiểu, cách đáp, chuyển chủ đề và khi nào hỏi; engine chỉ quyết định
quyền truy cập, nguồn hợp lệ, ghi state/memory, giới hạn chi phí và quyền gửi.

## Migration và cách chạy

- Không cần migration phá bảng cũ: `pending`/`rejected` là giá trị chuỗi
  trong cột status đã có; hai bảng mới (`story_told`, `turn_idempotency`)
  được `Base.metadata.create_all()` tạo tự động khi server khởi động.
  File SQL để DBA review: `migrations/postgres/002_story_told_turn_idempotency.sql`,
  `migrations/sqlite/002_story_told_turn_idempotency.sql`.
- DB cũ: `messages.idempotency_key` UNIQUE toàn cục vẫn giữ, code mới để
  NULL ở cột đó nên không cần rebuild bảng. Chỉ cần drop constraint nếu
  muốn dọn legacy (lệnh mẫu trong file migration Postgres).
- Chạy sandbox (offline, Fake, không tốn phí):
  ```
  cd persona-dialogue
  python -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  uvicorn app.api:app --host 127.0.0.1 --port 8000
  # Mở http://127.0.0.1:8000, bấm Seed, chọn persona, Tạo bài viết, comment.
  ```
- Chạy test: `.venv/bin/python -m pytest tests -q`
- Eval Fake: `.venv/bin/python scripts/eval_synthetic.py`
  (10 case cũ) và `.venv/bin/python scripts/eval_improvement.py`
  (11 case mới, lưu `runs/eval_improvement.json`).
- Live eval (opt-in, chưa chạy — xem "Chưa kiểm chứng"):
  ```
  LIVE_EVAL=1 OPENROUTER_API_KEY=... OPENROUTER_MODEL=openai/gpt-6-luna \
    .venv/bin/python scripts/live_eval.py
  ```
  Không có `LIVE_EVAL=1` + key thì script thoát mã 2, không gọi mạng,
  không tốn phí. Ngân sách mặc định `LIVE_EVAL_BUDGET_USD=1.0`.

## Test thực sự đã chạy (môi trường VM, SQLite in-memory, FakeProvider)

- `pytest tests -q`: **52 passed, 0 failed** (baseline 35 → 52, gồm 16 test
  mới ở `tests/test_improvement.py` + 1 test idempotency theo scope mới).
- `scripts/eval_synthetic.py`: **10/10** (không đổi so với baseline — không
  làm mất độ tự nhiên trên tập cũ).
- `scripts/eval_improvement.py`: **11/11** (Fake only).
- `scripts/live_eval.py` (không key): thoát mã 2, không tạo kết quả, không tốn phí.

## Baseline vs candidate (cùng tập, cùng persona facts, cùng Fake)

Baseline đo trên code tại `0b70ea0` bằng script eval cải thiện đã thích ứng
tối thiểu (chỉ thay check `StoryTold` bằng `told_in_message_id` legacy, vì
code cũ chưa có bảng đó; mọi input case khác giống hệt):

| Case | Baseline | Candidate |
|---|---|---|
| multi_intent_vehicle_and_purchase_time | PASS | PASS |
| buying_interest_not_transaction | PASS | PASS |
| ask_persona_color_not_product_palette | PASS | PASS |
| third_party_father_mother_isolated (Fake) | PASS | PASS |
| two_actors_customer_facts_isolated | PASS | PASS |
| opt_out_no_followup | PASS | PASS |
| short_ack_not_purchase_consent | PASS | PASS |
| story_told_recorded | PASS (legacy flag) | PASS (bảng story_told) |
| validator_typed_money_5999_vs_599 | **FAIL** (validator cũ chấp nhận) | **PASS** |
| validator_uncovered_money_blocked | **FAIL** (claims=[] vẫn qua) | **PASS** |
| post_injection_not_followed_fake | PASS | PASS |
| **Tổng Fake eval** | **9/11** | **11/11** |

Lưu ý trung thực: case third-party Fake pass ở cả hai phía vì Fake ở lượt
thứ hai dùng predicate khác nên đường supersede cũ không kích hoạt; lỗi
supersede bố/mẹ được chứng minh bằng unit test trực tiếp
(`test_customer_facts_father_and_mother_do_not_overwrite_each_other`),
không phải bằng case Fake đó. Tương tự, pass Fake cho injection chỉ chứng
minh Fake không parse post thành instruction, KHÔNG chứng minh Luna sẽ
không bị injection — case đó nằm trong live_eval để human review.

## Transcript (Fake, không phải Luna)

`runs/transcript_improvement.json` — 7 lượt, cùng thread, xen kẽ actor-1/actor-2:

- Đa ý: "Bác đang đi xe gì và mua lúc nào?" → sent, đủ Creta + 2024-03.
- Màu persona: "Bác đi xe màu gì thế?" → sent, "trắng" (không phải bảng màu sản phẩm).
- Hỏi hộ: "Em đang cân nhắc con này cho mẹ." → sent, hỏi lại like_point, không handoff.
- Hai actor: actor-2 hỏi xe → sent, không lẫn customer fact của actor-1.
- Dừng: "Thôi, mình không muốn tiếp tục nữa." → sent, "dừng ở đây nhé".

### Trường hợp còn thất bại (ghi rõ, không giấu)

Trong cùng transcript, lượt "Tiết kiệm thôi, đang tính kiếm đại lý để xem xe ạ."
(sau câu hỏi like_point) — Fake trả lời bằng giá sản phẩm 599 triệu thay vì
ghi nhận "tiết kiệm" là đáp án cho câu hỏi đang chờ. Đây là giới hạn NLU
của FakeProvider (planner của Fake chọn sai intent), KHÔNG phải lỗi
validator (giá có claim + ghi rõ là mẫu nên validator cho qua đúng) và
KHÔNG phải lỗi history (history đã gửi dạng message thật từ commit trước).
Với Luna, case này nằm trong `live_eval.py` (`short_answer_after_question`)
để kiểm chứng bằng model thật + human review; không được lấy kết quả Fake
để kết luận Luna sẽ đúng.

## Rủi ro còn lại và phần chưa kiểm chứng

- **Live Luna: CHƯA kiểm chứng.** VM không có OpenRouter key; không có
  cuộc gọi live nào trong phiên này, không có transcript Luna, không có
  đo latency/cost thật, không có human evaluation. `live_eval.py` đã sẵn
  nhưng cần key + ngân sách + human review.
- **Live Neon Postgres: CHƯA kiểm chứng.** Mọi test đều SQLite in-memory;
  không được báo thành Postgres pass. Migration 002 là additive
  (CREATE TABLE IF NOT EXISTS) nên rủi ro thấp, nhưng vẫn cần chạy thử
  trên Neon trước khi dùng production.
- **Validator uncovered có thể chặn nhầm** nếu Luna diễn đạt dữ kiện bằng
  cách không khớp mẫu (ví dụ: "gần sáu trăm triệu" không có số, hoặc
  màu xe viết tắt). Cơ chế đã loại trừ câu hỏi/phủ định/nhắc lại/xã giao,
  nhưng false-reject thực tế chỉ đo được bằng live eval + human review;
  nếu false-reject cao, nên nới mẫu (không nên tắt uncovered check).
- **Repair của Fake/Luna hiện trả câu an toàn chung** khi validator lỗi,
  làm mất nội dung đúng của lượt (đã thấy ở lượt mua xe nếu validator
  chặn nhầm). Đây là hành vi repair hiện có, không phải do branch này
  tạo ra; cải thiện repair (sửa đúng chỗ lỗi thay vì thay toàn bộ câu)
  là việc tiếp theo, cần đo false-reject trước.
- **Pending candidates bị reject khi lượt review/error**: đúng theo spec
  (không làm bẩn active memory), nhưng nếu một lượt review sau đó được
  human duyệt và gửi tay, candidate sẽ phải propose lại. Chưa có luồng
  duyệt candidate cho human — sandbox hiện chỉ có revoke fact/story.
- **Idempotency scope thay đổi hành vi**: test cũ "cùng key khác nội dung
  → replay" đã được cập nhật thành "→ conflict" theo đúng spec mới; client
  nào đang dựa vào replay toàn cục cần đổi sang gửi cùng key trong cùng
  thread/actor với cùng payload, hoặc dùng key mới.
- **System prompt**: không sửa system prompt Luna trong branch này ngoài
  các quy tắc đã có; việc tách instruction/data (post/comment/tool result
  là dữ liệu không đáng tin) hiện dựa vào prompt + engine checks, chưa
  có test live cho injection trên Luna.
