# GPT review fixes — fix/gpt-review-fixes (2026-11-09)

Branch sửa các lỗi review ngoài (GPT) đã kiểm chứng lại bằng probe trực
tiếp trên main @ `d7822d4` trước khi sửa. Thứ tự sửa theo đề xuất của
review, đã được Phước duyệt: (1) send + contact permission, (2) context +
answer quality, (3) real integration. Song song: shadow score
(`intent_score_llm`). **Không merge vào main khi chưa có quyết định của
Phước.**

## Phase 1 — Send + contact permission

### 1a. Lỗi validator chia blocking / soft

- **Blocking** (`app/engine/validator.py::BLOCKING_ERROR_PREFIXES`): sai
  nguồn, sai xe, dữ liệu cá nhân, opt-out, chốt giao dịch, claim không có
  nguồn, giá trị sai, fact hết hạn/không active, story/rag/company detail
  chưa lưu. Lượt còn lỗi blocking sau vòng repair -> `status: "review"`,
  message giữ ở `review`, KHÔNG gửi, không kích hoạt pending gì.
- **Soft** (giọng điệu, câu hỏi chung, ...): sửa tối đa 1 vòng, rồi gửi
  kèm lỗi trong `validator_errors` + trace.
- Trước đây: mọi lỗi đều gửi kèm `sent` sau vòng repair.

### 1b. Validator

- **Story/RAG/company claim** khớp theo thuộc tính có cấu trúc (xe/nơi/
  sự kiện/thời gian), không chỉ token overlap. Hai phía đều có proper noun
  mà mỗi bên có một từ phía kia không có -> mismatch (bắt được Vũng Tàu vs
  Đà Lạt, Creta vs Tucson).
- **Negation guard** nới cửa sổ: 5 token cho `chưa`/`chẳng` và `không` +
  `có` ("chưa có dịp nào lái…", "không có dịp nào lái…"); 2 token còn lại.
- **Color echo theo chủ thể**: "Xe mình cũng màu trắng" (chủ thể là xe
  persona) vẫn cần claim; "Màu trắng đẹp đó bạn" (đồng tình, không chủ
  thể persona) vẫn được miễn. Chỉ bắt màu trong `_KNOWN_COLORS`.
- **§4** quét cả text hiển thị để bắt hỏi lại key khách đã từ chối
  (`REFUSED_KEY_PHRASES`: phone/budget/area/like_point), không chỉ
  `followup_key`.
- **Giá trị customer tự nêu** (tiền/%/km/ngày trong text của khách) không
  bị bắt là uncovered.
- **Repair được BỎ chi tiết không có nguồn** (prompt sửa đã bỏ "Không xóa
  claim"); claim sai giá trị vẫn sửa thành giá trị chuẩn.
- **`exploration_cap` đã bỏ**: hết trần khai thác thì dừng hỏi, trả lời tự
  nhiên — KHÔNG tự đẩy sale + SĐT.

### 1c. Contact state (mới: `app/engine/contact_state.py`, bảng `contact_states`)

Ba trạng thái độc lập, không suy diễn lẫn nhau:

| Trạng thái | Giá trị | Ý nghĩa |
|---|---|---|
| `purchase_interest` | `interested` / `not_interested` | Khách có ý mua không |
| `contact_permission` | `allowed` / `refused` | Có được liên hệ lại không |
| `conversation_status` | `active` / `stopped` | Cuộc trò chuyện còn tiếp không |

- Detector deterministic (khớp chuỗi token, không substring) chạy ngay
  đầu lượt, trước mọi LLM call, commit ngay — kể cả khi provider lỗi sau
  đó thì quyền liên hệ vẫn được ghi nhận.
- Khách yêu cầu dừng -> ack xác nhận ngắn (`STOP_ACK_TEXT`), KHÔNG gọi LLM,
  không hỏi thêm, không đẩy sale; planner không thấy lượt dừng nên không
  viết `LeadState`.
- Khách nhắn lại sau khi dừng -> `conversation_status` mở lại `active`,
  `contact_permission` vẫn giữ `refused` cho tới khi cho phép rõ ràng.
- Tín hiệu mua mới mở lại `purchase_interest` ngay ở đầu lượt (detector)
  và cuối lượt (§5b, cho trường hợp planner thấy mà detector trượt);
  `contact_permission='refused'` là dính — sale vẫn bị chặn.
- "Tôi cũng muốn mua một chiếc như vậy" = `share_need`, KHÔNG phải
  handoff: resolve xe từ nhánh trả lời, hoặc hỏi đúng 1 câu phân biệt;
  chỉ vào transaction flow khi khách yêu cầu rõ đặt cọc/giữ chỗ/kết nối
  đại lý. Prompt `plan_turn` + `LEAD_CAPTURE` đã sửa theo hướng này
  (thay đổi hành vi so với lead script 2026-11-02 — cần Phước xác nhận).

## Phase 2 — Context + answer quality

### 2a. Branch-based context

- `TurnRequest.parent_message_id` + `Message.parent_message_id`
  (persona reply có parent = user message nó trả lời).
- `_branch_history`: có parent -> chỉ nhánh đó (chuỗi cha của parent +
  lượt của actor hiện tại + reply đã hiển thị `sent`/`review` trong
  nhánh); draft/error không vào. Không parent -> giữ hành vi cũ (20
  message gần nhất).
- Gate: "ok"/👍 chỉ đưa vào pipeline khi trả lời được một pending question
  dạng có/không (`looks_yes_no`: kết thúc "không"/"chưa" hoặc chứa
  "có … không"); câu hỏi mở thì bỏ qua. Đây là heuristic, sẽ đọc sai một
  số câu hỏi.

### 2b. Attribute-based persona fact lookup

- `get_persona_facts`: `fact_key` trước; key không có (sau synonym map)
  trả `NOT_FOUND` + gợi ý registry — không còn fallback dump toàn bộ canon.

### 2c. Không tạo canon trong lượt

- Đã gỡ `propose_persona_fact`, `propose_story`, `propose_addendum` khỏi
  tool của lượt chat (cả `tool_schemas.py` + `tools.py` + FakeProvider).
  `propose_customer_fact` giữ lại (dữ liệu của khách, không phải canon).
- SYSTEM_PROMPT: bỏ hướng dẫn "chưa chắc thì chọn màu trắng mặc định" và
  bỏ "choosing a value is not fabrication". Khi canon thiếu, model trả lời
  tự nhiên là chưa chắc/không nhớ rõ — không bịa.
- Bổ sung canon chỉ qua luồng admin có sẵn: `find_gaps` -> `validate_draft`
  -> `publish_draft` (xem `app/engine/enrichment.py`). Logic nhất quán
  story (`check_story`/`check_addendum`) vẫn sống ở đó.
- Test cũ dựa vào propose trong lượt đã viết lại theo luồng admin hoặc
  gọi trực tiếp `check_story`/`check_addendum`; 2 test về story+addendum
  tạo trong lượt đã xóa vì không còn producer path.

### 2d. FAQ hẹp lại

- Comment chứa thêm intent ngoài FAQ đã khớp (vd xăng + giá) -> đi vào
  pipeline, không trả một nửa đáp án FAQ. Gate kiểm tra `_has_other_intent`
  (price/dealer/deposit token sequences).

## Phase 3 — Real integration

### 3a. Idempotency race

- User message + `TurnIdempotency` claim commit trong CÙNG một transaction
  (`flush()` lấy `user_msg.id`, một `commit()`; `IntegrityError` ->
  rollback xóa luôn user message mồ côi).

### 3b. ready_for_delivery + confirmation endpoint

- Partner mode bật khi cấu hình `PARTNER_API_KEY` (xem 3c). Ở partner mode,
  `_send_or_stage` KHÔNG gọi sender: message stage ở
  `ready_for_delivery` + `Delivery(sender="partner", status="unknown")`;
  snapshot phục hồi đã lưu sẵn trong `trace.delivery_recovery`.
- `POST /threads/{thread_id}/messages/{message_id}/delivery-confirmation`
  (body `status: sent|failed|unknown`, kèm `channel_message_id`/`error`):
  - `sent` -> `finish_delivery` với snapshot (executor mới +
    `recovered=True`, như đường idempotent-replay); message -> `sent`,
    Delivery -> `sent`, idempotency -> `sent`.
  - `failed` -> reject pending candidates; message -> `error`, Delivery ->
    `failed`.
  - `unknown` -> giữ `ready_for_delivery`, ghi lại báo cáo, chờ báo lại.
  - Idempotent: báo lại cùng status -> `idempotent_replay: true`, không
    kích hoạt lần hai.
  - Mâu thuẫn (đã `sent` rồi báo `failed` hoặc ngược lại) -> HTTP `409` +
    ghi `trace.delivery_conflict`, trạng thái không đổi.
  - Nếu canon đổi sau khi stage (`can_recover` False): message vẫn `sent`
    (đã gửi thật trên kênh) nhưng pending candidates bị reject, ghi dấu
    `trace.delivery_recovery_blocked`.
- Chi tiết hợp đồng: `docs/api-integration.md`.

### 3c. Auth / tenant

- `PARTNER_API_KEY` + `PARTNER_TENANT` (mặc định `"default"`) trong env
  server; `SETTINGS.partner_mode` = True khi có key.
- Endpoint partner (`POST /threads`, `POST /turns`,
  `POST /threads/{id}/turns`, confirmation) yêu cầu header `X-API-Key`
  đúng key khi partner mode bật (thiếu/sai -> `401`); sandbox (chưa cấu
  hình key) không yêu cầu.
- Thread access giới hạn theo tenant: thread phải có
  `thread.tenant == PARTNER_TENANT`, nếu không -> `403` (kể cả thread
  tenant NULL — thread sandbox không thuộc partner nào). `POST /threads`
  ở partner mode ép tenant theo key, bỏ qua body `tenant`.
- Seed/admin/trace (`/seed`, `/admin/*`, `/personas/*`,
  `/threads/{id}/messages`) tách riêng khỏi API partner — không dùng key
  partner, không expose cho đối tác.

### 3d. Multi-turn Vietnamese conversation tests

`tests/test_multiturn_vn.py`: đổi mẫu xe Creta<->Tucson (mỗi thread giữ
đúng giá của mẫu mình, canon xe persona không đổi), nhiều người bình luận
(pending question không lây sang actor khác), khách đính chính (fact cũ
-> `corrected`, fact mới -> `current`), giá thiếu/hết hạn (không bịa số,
không hiện số cũ), gửi lỗi rồi lượt mới (draft lỗi giữ nguyên, lượt mới
gửi bình thường).

## Shadow score (intent_score_llm)

- `plan_turn` thêm field optional `intent_score_llm` (0-100) với rubric
  trong tool description: **mức ưu tiên chú ý, cùng ý nghĩa với rules_v1,
  KHÔNG phải confidence/xác suất**.
- Engine kẹp 0-100 (`planner_normalize._intent_score_llm`), `None` khi
  model không trả / trả không phải số. Lưu trong trace
  (`trace_base["intent_score_llm"]`, cạnh `intent_score` rules_v1) và
  hiển thị trong UI (panel gate + planner, ghi rõ "shadow, tham khảo").
- Không ảnh hưởng gate hay bất kỳ quyết định nào (test khóa hành vi này).
- FakeProvider chấm theo cùng rubric (stop 90, transaction/share_need 75,
  ai_identity 70, ask_persona/ask_product 65, correction 60, còn lại 20).

## Migration 008

`migrations/postgres/008_gpt_review_fixes.sql` +
`migrations/sqlite/008_gpt_review_fixes.sql` (theo quy ước 007):

- Bảng mới `contact_states` (unique `thread_id` + `actor_id`).
- `threads.tenant VARCHAR(64)` (nullable).
- `messages.parent_message_id VARCHAR(32)` (FK tới `messages.id` trên
  Postgres; SQLite thêm cột không FK — SQLite mặc định không enforce FK
  nên hành vi tương đương).

DB mới đã có schema qua `Base.metadata.create_all`. **DB Neon của Phước
đang thiếu cả migration 007** (lỗi `rag_chunks.source_sha does not exist`
từ 2026-11-09) — chạy 007 rồi 008 sau khi pull code mới, trước khi
restart server. Thứ tự: 007 trước, 008 sau.

## Partner-mode vận hành

```bash
PARTNER_API_KEY=<key> PARTNER_TENANT=acme python -m uvicorn app.api:app
```

- Không cấu hình key = sandbox (FakeSender, không auth). Cấu hình key =
  partner mode (stage `ready_for_delivery`, yêu cầu `X-API-Key`).
- Đối tác gửi reply ra kênh thật rồi xác nhận qua endpoint (xem
  `docs/api-integration.md`). Không dùng `/seed` trong luồng comment.

## Giới hạn còn lại (không làm trong branch này)

- Stale RAG chunk vẫn được phục vụ (chỉ báo `rag_status` trong trace);
  chunk SHA mới chỉ phát hiện version drift, chưa chặn dùng chunk cũ.
- Negation guard vẫn là heuristic (cửa sổ token), có thể trượt với mệnh
  đề phức tạp hoặc phủ định xa động từ.
- `looks_yes_no` là heuristic cho ack-matching.
- Không có `ready_for_delivery` cho lượt FAQ/stop? Có — stop và FAQ cũng
  qua `_send_or_stage` nên cũng stage ở partner mode; confirmation của
  chúng chỉ đổi trạng thái (không có snapshot pending để kích hoạt).
- `can_recover` ở confirmation: nếu khách đã nhắn thêm sau khi stage
  (`has_later_messages`) thì pending bị reject thay vì activate — thận
  trọng nhưng có thể gây ngạc nhiên; xem `api._confirm_sent`.
- Chưa có auth cho endpoint admin/seed/trace (mặt sandbox, không expose).
- `human_gold` vẫn trống; hệ thống vẫn CHƯA auto-ready để gửi Facebook.
- Sample product facts hết hạn 2026-12-31.

## Quyết định cần Phước chốt (coordinator đã chọn tạm, ghi lại để duyệt)

- Blocking vs soft: `ai_identity` và `stop_on_generic_refusal` xếp blocking
  theo phán đoán của coordinator (review không nêu rõ hai lỗi này).
- Partner-mode trigger = sự hiện diện của `PARTNER_API_KEY` (không phải
  header/setting riêng).
- LEAD_CAPTURE: prompt mới chỉ cho sale intro/phone ask khi khách yêu cầu
  rõ hoặc đồng ý rõ — đảo ngược lead script 2026-11-02 (khai thác 1-2 câu
  rồi giới thiệu sale + SĐT). Scope đã duyệt việc bỏ validator push, nhưng
  prompt gate này là thay đổi hành vi cần Phước xác nhận.
- "Bác mua xe ở đâu thì rẻ?" hiện trả `has_lead_signal` = False (đọc là hỏi
  nơi persona đã mua). Cách hỏi lead thật ("Em muốn mua xe, mua ở đâu thì
  tốt?") vẫn True.
- `_KNOWN_COLORS` là danh sách cố định; màu lạ chỉ bị bắt khi câu khẳng
  định màu xe của chính persona.
- Lượt dừng (stop) không viết `LeadState` — chỉ `ContactState`.
