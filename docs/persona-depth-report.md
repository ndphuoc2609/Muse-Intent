# Persona-depth — báo cáo triển khai (branch improve/persona-depth)

Base: `main` tại `5a97ccc` (sau lần gộp improvement claim/history/idempotency).
Branch này: `improve/persona-depth`. Không chạm MAT_Intent, không gửi Facebook thật.

## Đã làm gì (theo brief, không chỉ sửa prompt)

**1. Schema + migration 003 (additive, Postgres + SQLite)**
- Bảng mới: `persona_profiles` (NULL = chưa biết, khác "không có"), `persona_perspectives`
  (aspect `values | tradeoffs | concerns | changed_view | unknowns | not_suitable_for`,
  `changed_view` bắt buộc có `prior_content` để phân biệt thay đổi quan điểm vs mâu thuẫn),
  `persona_style_guides` (style examples chỉ hướng dẫn giọng, không phải bằng chứng đời sống),
  `persona_categories`, `tag_definitions`, `tag_assignments`, `enrichment_drafts`,
  `story_links` (retell/addendum/correction/supersedes), `retrieval_logs`.
- Cột mới trên `persona_stories`: participants, context, concern_or_choice, action,
  outcome, feeling_or_lesson, canonical_facts, tellable_policy (always/on_relevance/never),
  related_need_topics, story_categories.
- Cột mới trên `customer_facts`: need_kind, certainty.
- Lưu ý Postgres: `ADD COLUMN IF NOT EXISTS` có trong file 003; SQLite: chạy từng ALTER,
  bỏ qua lỗi duplicate column nếu chạy lại. `create_all()` không thêm cột vào DB cũ.

**2. Taxonomy v1 (`app/engine/taxonomy.py`)**
- 7 categories, 22 tags có namespace, định nghĩa, applies-when, subject_scope.
- Tag lạ do model đề xuất KHÔNG tự thành taxonomy: bị chặn ở `propose_story`
  (category lạ) hoặc trả về trong `tags_unknown` (fact/story) để admin duyệt.
- Tag `family` không suy ra có con, cần 7 chỗ hay sắp mua xe — chỉ fact cụ thể mới kết luận.

**3. Story tags/categories — Luna → DB (yêu cầu cuối của brief)**
- `propose_story` giờ BẮT BUỘC `tags` + `categories` trong schema. Category lạ → CONFLICT,
  tellable_policy sai → CONFLICT. Tag đã biết tạo `TagAssignment` pending, chỉ active
  khi reply được gửi thành công (cùng lifecycle với fact/story); bị reject khi review/error.
- FakeProvider cũng tuân contract này (story Nam Định kèm 3 tags + 2 categories),
  nên test offline vẫn kiểm tra được lifecycle thật.

**4. Retrieval theo ngữ cảnh (`app/engine/retrieval.py`)**
- Exact/tag trước, không đổ toàn bộ persona vào prompt. Điểm: tag trùng +3, category +2,
  entity trùng +1.5, từ khóa +0.5; đã kể cho actor này bị trừ 2 trừ khi khách hỏi lại rõ ràng.
- Lọc: `tellable_policy=never` không bao giờ trả, story bị supersedes không trả bản cũ,
  chỉ trả story điểm > 0, tối đa 3. Không ưu tiên chỉ vì cùng tên xe.
- Pipeline chạy retrieval server-side ngay sau `plan_turn`, ghi `retrieval_logs`, đưa
  ref_id vào `executor.retrieved` (để claim hợp lệ ngay cả khi model không gọi
  get_persona_stories), và cập nhật system message đầu (không thêm system message mới
  làm Fake mất context cũ). Không thêm vector retrieval: chưa có bằng chứng cần.

**5. Validator persona (`app/engine/persona_validator.py`)**
- Bắt: stance không phải peer_user, timeline trước purchased_at, story conflict qua
  consistency (không dùng giống từ), xe bán trước khi mua, product fact lẫn vào canon,
  generated gắn nhãn verified, story never đã bị kể.
- Phân biệt: changed_view thiếu prior_content chỉ là warning; cùng nơi + cùng xe +
  khác ngày KHÔNG phải conflict; thiếu thông tin là warning/unknown, không tự kết luận.

**6. Enrichment có kiểm soát (`app/engine/enrichment.py`)**
- `find_gaps()` từ DB (fact/category/perspective/story chưa có tag/chủ đề chưa phủ).
- Draft phải có proposal, reason, anchor_refs, tags/categories đề xuất, conditions,
  conflicts/unknowns, source=generated, tellable_policy. Trường khóa không được tạo,
  không tự tạo số tiền/đại lý/lịch bảo dưỡng/thông số như dữ liệu thật.
- Publish chỉ khi `validate_draft` đạt; sau publish chạy lại validator và ghi lỗi vào draft.

**7. Customer need state**
- `propose_customer_fact` nhận `need_kind`/`certainty`; nếu không nêu, suy ra từ
  predicate + source (inferred_context → hypothesis/low). Hypothesis không nâng thành
  fact khi nhắc lại. "Đang tham khảo" không thành "mua ngay"; khả năng chi trả, mong
  muốn và ngân sách tự nêu là ba thứ khác nhau.

**8. Runtime prompt v1 (`app/engine/runtime_prompt.py`)**
- Sections: PERSONA_CORE, STYLE_GUIDE, RELEVANT_PERSONA_FACTS, RELEVANT_MEMORIES,
  PRODUCT_FACTS, CUSTOMER_STATE, VERIFIED_CONVERSATION_CONTEXT, CONVERSATION_POLICY.
  CURRENT_MESSAGE là message user riêng, không nhét vào system.
- Policy: 8 hướng đáp (answer_directly, acknowledge, reflect_need, share_relevant_experience,
  explain_tradeoff, ask_clarification, invite_optional_sharing, stop); trả lời trước khi
  hỏi, tối đa 1 câu hữu ích/lượt, không hỏi khi khách đã cung cấp/từ chối, không ép mua,
  không khan hiếm giả, không khai thác nỗi lo.

**9. Hai persona distinct + seed idempotent**
- Hoàng Nam giữ nguyên tắc cũ: màu xe và chuyến đi cụ thể KHÔNG seed, chỉ qua propose.
- Minh Anh (31, Quận 7, Tucson 2.0 Đặc biệt, 08/2022, đã đổi xe, gia đình có con nhỏ)
  là persona mẫu hoàn chỉnh: profile, 6 perspectives (kể cả changed_view có prior),
  style guide, 3 categories, 1 story Vũng Tàu có tags/categories/participants/context/
  concern/action/outcome/feeling, product fact mẫu riêng 769 (để test isolation).
- Seed upsert theo khóa riêng: gọi lại không tạo trùng, DB cũ được bổ sung phần mới.
- Hai test product cũ đã sửa hẹp theo seed mới (Tucson giờ có price riêng, nên model
  không có fact đổi thành Santa Fe; test hết hạn chỉ rõ model Creta).

**10. Admin sandbox**
- `GET /admin` → `app/static/admin.html` (link từ sandbox chính): profile, timeline,
  stories kèm tags/categories, validator + gaps, tạo draft, publish/reject draft,
  retrieval log 50 lượt gần nhất.
- API: `/admin/taxonomy`, `/admin/personas/{id}`, `/admin/personas/{id}/drafts`
  (POST/GET), `/admin/drafts/{id}/publish`, `/admin/drafts/{id}/reject`,
  `/admin/personas/{id}/retrieval`.

## Kết quả kiểm chứng (VM, FakeProvider, SQLite in-memory)

- pytest: **63 passed** (52 cũ + 11 test persona-depth mới trong `tests/test_persona_depth.py`).
- `scripts/eval_synthetic.py`: 10/10. `scripts/eval_improvement.py`: 11/11.
- `scripts/eval_persona_depth.py` (mới): **7/7** — validator cả hai persona, isolation
  Nam=Creta/Anh=Tucson, story có tags+categories, không gán 7 chỗ, không hỏi lặp,
  seed idempotent.
- HTTP smoke trên server test cổng 8001 (DB riêng /tmp): /seed, /admin/personas/minh-anh,
  /admin/taxonomy, /admin, tạo draft (validator_errors=[]) đều OK. Server test đã tắt;
  server VM cổng 8000 vẫn chạy code cũ, chưa restart với code mới.

## Phụ lục: lỗi live Luna 30/10 — model đoán sai tên key (đã sửa, commit sau 40644ac)

Triệu chứng do Phước gửi (OpenRouterProvider, 3 calls, $0.000606): hỏi "Bác đang đi
xe gì và mua lúc nào?" nhưng Luna trả "Mình chưa có thông tin xác nhận chính xác",
dù fact đã seed sẵn (vehicle_model=Hyundai Creta, purchased_at=2024-03).

Nguyên nhân gốc (đọc từ trace, không đoán): Luna gọi `get_persona_facts` với keys
`vehicle, car, current_vehicle, vehicle_purchase_date, purchase_date, car_model,
xe_dang_di, thoi_diem_mua_xe` — không key nào trùng registry thật. Engine cũ lọc
exact-match nên trả NOT_FOUND rỗng, Luna kết luận không có dữ kiện và gọi
`stop_or_insufficient`. Fake không bao giờ gặp lỗi này vì Fake gọi không truyền keys
(lấy toàn bộ canon), nên toàn bộ test offline vẫn xanh trong khi live fail. Đây
chính là khoảng cách live-vs-Fake đã cảnh báo ở bản báo cáo đầu.

Fix (engine-first, không chỉ sửa prompt):
- `tool_get_persona_facts`: synonym map (vehicle/car/car_model -> vehicle_model,
  purchase_date/vehicle_purchase_date -> purchased_at, mau_xe -> vehicle_color...).
- Fallback: nếu toàn bộ key yêu cầu đều lạ ngoài registry mà canon còn fact active
  thì trả toàn bộ canon kèm reason `keys_unmatched_fallback_all`, thay vì [].
- Giữ NOT_FOUND khi model hỏi đúng tên key trong registry nhưng fact chưa active
  (VD: vehicle_color chưa propose) — để đường propose và test pending-isolation
  không đổi ý nghĩa.
- Tool description + SYSTEM_PROMPT: liệt kê registry key thật, khuyến nghị gọi
  không truyền keys, và quy định: fact chưa có mà thuộc nhóm được tạo (màu xe,
  sở thích, chuyến đi) thì PHẢI propose trước rồi trả lời, chỉ dùng
  stop_or_insufficient khi thông tin bị khóa hoặc là giá/thông số chưa xác minh.
- Test mới `tests/test_fact_key_tolerance.py` (4 test): đúng key list Luna đã gửi
  -> trả đủ Creta + 2024-03; key lạ hoàn toàn -> fallback; key đúng tên nhưng
  chưa active -> NOT_FOUND; provider giả lập đúng hành vi Luna trong trace ->
  lượt đó giờ `sent` với "Creta 1.5 Cao cấp, mua từ tháng 03/2024".

Lượt 2 trong transcript (màu xe -> review/handoff): không có trace kèm theo nên
không khẳng định chắc chắn đường đi, nhưng cùng một gốc rễ giải thích được: claim
"đi Creta" không có ref_id do fact retrieval fail -> validator error -> review.
Sau fix, lượt màu xe phải đi đường propose (tạo vehicle_color rồi trả lời) thay
vì review; cần Phước chạy lại live để xác nhận, vì prompt Luna vẫn có thể chọn
đường khác.

## Phụ lục 2: lượt màu xe bị review — Luna từ chối propose vì prompt mâu thuẫn (đã sửa, lên main)

Triệu chứng 30/10 (sau khi đã gộp lên main): hỏi màu xe, Luna trả review/handoff
"Mình chưa có màu xe được xác nhận trong thông tin hiện có", không propose, không
ghi nhớ cho lượt sau.

Kiểm tra engine trước khi đổ lỗi cho model: `propose_persona_fact` với
`vehicle_color` hoàn toàn đi được — key thuộc nhóm được tạo, chưa có active/pending
thì tạo pending, pipeline active khi sent, lượt sau đọc canon. Validator cũng chấp
nhận claim từ fact pending trong cùng lượt. Nghĩa là đường "tự update DB nếu không
conflict, ghi nhớ cho lần sau" đã có sẵn, chỉ là Luna không đi vào nó.

Nguyên nhân là prompt tự mâu thuẫn: rule cũ cấm tuyệt đối "không bịa ... màu xe",
trong khi propose chính là luồng tạo chi tiết hư cấu. Model thận trọng chọn phía
an toàn là từ chối, rồi lượt đó rơi vào review (validator hoặc goal handoff).

Fix:
- SYSTEM_PROMPT viết lại rule 2/3: phân biệt dữ kiện thật cần xác minh (giá, thông
  số, đại lý — tuyệt đối không bịa) với chi tiết hư cấu của persona (màu xe, sở
  thích, chuyến đi — CHỌN một giá trị hợp lý và tạo qua propose tool, đó không
  phải bịa dữ kiện thật; engine kiểm tra conflict, chỉ lưu khi gửi thành công).
- Màu xe chưa có: mặc định trắng (nếu không có gợi ý khác), propose rồi mới trả
  lời kèm claim ref_id. Propose trả CONFLICT thì dùng màu trong canon, không ghi đè.
- Tool description của `propose_persona_fact` ghi rõ đây là luồng tạo bình thường.
- Test mới `tests/test_color_propose_flow.py`: propose trắng -> sent -> active trong
  DB -> lượt sau trả cùng màu từ canon không propose lại; propose đen khi đã có
  trắng -> CONFLICT, giá trị cũ giữ nguyên. Test prompt khóa câu chữ mới.
- Sandbox UI thêm cột Conversation Planner (`#plannerCol`, render từ
  `response.planner`): mục tiêu, định hướng tiếp theo (trả lời trước / hỏi thêm 1
  câu + mục đích + key), intents kèm intent source (Khách nói rõ / Suy luận từ
  context), dữ kiện cần, tham chiếu đã giải quyết, đã trả lời câu hỏi nào, câu hỏi
  đang chờ, và ghi chú khi lượt rơi vào review. Test guard trong
  `tests/test_sandbox_planner_ui.py`. JS đã qua `node --check`, UI đã serve thử
  và thấy đủ 3 điểm đánh dấu planner.

Chưa kiểm chứng live: prompt mới có làm Luna thực sự propose hay không vẫn cần
Phước chạy lại live (VM không có key). Nếu Luna vẫn từ chối propose sau fix này
thì cần trace lượt đó để xem nó gọi tool nào thay vì propose.

## Chưa kiểm chứng (nói rõ, không nhận vơ)

- Live Luna: VM không có OpenRouter key. `propose_story` bắt buộc tags/categories có
  thể làm Luna cũ trả sai schema — cần chạy `scripts/live_eval.py` trên máy có key và
  xem Luna có tuân tags/categories không trước khi coi là xong. Fix key-guess ở
  phụ lục trên mới chỉ kiểm chứng offline bằng provider giả lập đúng key list của
  Luna, chưa chạy lại live.
- Live Neon Postgres: migration 003 chưa chạy trên Postgres thật (chỉ review SQL).
- Human review: human_gold vẫn trống; eval Fake không chứng minh Luna nói tự nhiên,
  không chứng minh khách thật cảm thấy được lắng nghe.
- Semantic/vector retrieval: cố tình chưa thêm (chưa có bằng chứng cần).
- Admin UI chưa qua reviewer độc lập như sandbox chính (đã làm theo cùng tokens,
  JS đã qua `node --check`, nhưng chưa có vòng review udemi riêng).
