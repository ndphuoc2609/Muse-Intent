# persona-dialogue — Hệ thống hội thoại AI theo persona (project mới)

**Không ghi đè / không sửa MAT_Intent cũ.** Repo cũ chỉ dùng để tham khảo
nghiệp vụ (stance `peer_user`, Luna qua OpenRouter, FakeSender, idempotency).
Kiến trúc, schema và planner ở đây viết lại từ đầu; các giới hạn cũ không
được sao chép mặc định (xem § Khác biệt với MAT_Intent).

Persona trò chuyện như **người dùng bình thường chia sẻ trải nghiệm**
(`peer_user`), không phải nhân viên bán hàng, không xác nhận giao dịch.
Khi khách hỏi trực tiếp có phải AI không, hệ thống trả lời minh bạch là
persona hư cấu, không phải người thật.

## 1. Kiến trúc tổng thể và sơ đồ luồng

```
Khách (sandbox UI / API)
  -> FastAPI (api.py): xác minh binding persona/post/thread/actor
  -> Pipeline (engine/pipeline.py)
       1. Idempotency-Key (scoped, bảng turn_idempotency): cùng thread +
          actor + payload -> replay; cùng key khác payload -> conflict rõ
          ràng, không replay im lặng; khác thread -> lượt mới. Trùng key +
          gửi lỗi -> gửi lại đúng draft cũ, không tạo story mới.
       2. Tool loop với provider (tối đa MAX_TOOL_ROUNDS, không giữ
          transaction DB khi chờ model):
            plan_turn  (planner là tool ĐẦU TIÊN trong cùng vòng tool
                        calling, không thêm 1 LLM call riêng — lý do:
                        giảm latency + chi phí, planner vẫn tường minh
                        trước mọi retrieval; tool trước plan bị chặn,
                        không thực thi)
            -> get_persona_facts / get_persona_stories / get_product_facts
               / get_customer_state   (scope do server xác định)
            -> propose_persona_fact / propose_customer_fact
               / propose_story / propose_addendum
               (engine kiểm chứng rồi lưu ở trạng thái PENDING, chưa phải
               canon; chỉ activate khi delivery thành công, ngược lại reject)
            -> submit_final_reply | stop_or_insufficient
            (text thuần không qua submit_final_reply chỉ được chấp nhận
            khi là xã giao thuần túy, còn lại là lỗi protocol)
       3. Validator (engine/validator.py): claim phải có ref_id từ tool
          result của lượt và khớp giá trị THEO KIỂU (tiền/ngày/%/km);
          khẳng định có dữ kiện trong văn bản phải được claim bao phủ;
          chặn xác nhận cọc, xin SĐT/địa chỉ nhà ở kênh public, hỏi lại
          khi opt-out, product sample không ghi rõ là mẫu; repair tối đa
          MAX_VALIDATOR_REPAIRS vòng, hết vòng -> status review (không tự
          thay câu chung chung).
       4. Cập nhật QuestionState: chỉ ANSWERED khi planner nêu đúng key;
          chuyển chủ đề giữ nguyên pending, không tự redirected; followup
          chỉ được tạo khi delivery thành công.
       5. FakeSender: chỉ khi sent mới activate proposals, ghi Delivery +
          told_in_message_id + story_told (kể cả story truy xuất lại).
          Gửi lỗi -> status error, proposals bị reject, story chưa bị
          đánh dấu đã kể.
       Chi tiết đầy đủ: docs/improvement-report.md (branch
       improve/claim-history-idempotency, 2026-10-30).
  -> Message (draft/sent/review/error) + trace đầy đủ cho sandbox
```

**MODEL làm gì:** hiểu tiếng Việt, nhận diện nhiều ý/lượt, giải quyết tham
chiếu (“xe này”, “như vậy”) từ context, quyết định mục tiêu, có hỏi thêm
hay không, sinh lời đáp tự nhiên, đề xuất story/fact mới theo contract.

**ENGINE làm gì:** binding, context đã kiểm chứng, tool, schema/evidence/
quyền/nhất quán, lưu state + history, transaction ngắn + version check,
idempotency, delivery, trace. Engine **không** chọn câu hỏi theo thứ tự
cố định, không ép câu mẫu, không đưa mọi câu có từ “mua” vào handoff,
không quyết định nội dung từ keyword đơn lẻ, không chạy SQL do model tạo.

## 2. Cấu trúc project

```
persona-dialogue/
  app/
    config.py            # env, budget, timeout, policy key khóa/tạo được
    db.py                # engine SQLite (mặc định) / Postgres (DATABASE_URL)
    models.py            # schema duy nhất cho cả 2 dialect
    schemas.py           # contract planner / tool result / final reply (v1)
    seed.py              # persona mẫu Hoàng Nam (HƯ CẤU)
    api.py / main.py     # FastAPI + sandbox UI
    static/index.html    # sandbox: chọn persona, comment nhiều lượt, trace
    providers/
      base.py            # Provider protocol (tool calling)
      fake.py            # FakeProvider offline cho test/sandbox (xem § Lưu ý)
      openrouter.py      # OpenRouter, mặc định openai/gpt-6-luna (Luna)
    engine/
      tool_schemas.py    # JSON Schema v1 cho 10 tool, validate bằng jsonschema
      tools.py           # thực thi tool, server-scoped, UNKNOWN != NOT_FOUND
      consistency.py     # trùng/mâu thuẫn story: SAME/NEW/CONFLICT/AMBIGUOUS
      validator.py       # claim, giao dịch, riêng tư, opt-out, AI identity
      pipeline.py        # luồng lượt, idempotency, delivery, repair loop
      fake_sender.py     # mô phỏng gửi (không Meta/Facebook)
  migrations/
    001_init.sql         # Postgres (bản đầy đủ, IF NOT EXISTS)
    postgres/001_init.sql
    sqlite/001_init.sql  # SQLite (tương đương)
  scripts/
    make_transcript.py   # transcript 8 lượt qua pipeline thật (Fake)
    eval_synthetic.py    # eval synthetic offline
  tests/                 # test theo §18 (xem § Kiểm thử)
  runs/transcript.json, runs/eval_synthetic.json  # output đã chạy
  Dockerfile, docker-compose.yml, .env.example, requirements.txt
```

## 3. Contract (v1, tóm tắt — schema đầy đủ ở app/schemas.py, app/engine/tool_schemas.py)

- **Planner:** `intents[] {type, subject, relation, normalized, evidence,
  source: stated|inferred_context, confidence}`, `pending_questions`,
  `answered_question_keys`, `known_customer`, `known_persona`,
  `data_needs[]`, `goal: answer_question|share_experience|clarify|
  explore_need|empathize|stop|handoff`, `response_direction
  {answer_first, ask_followup, followup_purpose, followup_key}`,
  `resolved_reference {text, resolved_to, source, confidence}`.
  Một lượt có thể chứa nhiều ý, nhiều câu hỏi — không ép một intent.
- **Tool result:** `status: FOUND|NOT_FOUND|CONFLICT|UNKNOWN|AMBIGUOUS`,
  `data`, `ref_id`, `source`, `version`, `valid_until`, `limits`, `reason`.
  **UNKNOWN (timeout/lỗi DB/lỗi tool) không bao giờ là NOT_FOUND.**
- **Final reply:** `text`, `claims[] {kind: persona|story|product|
  customer_echo, ref_id, value}`, `followup_question`, `followup_key`, `goal`.
  Claim persona/product/story bắt buộc có `ref_id` đã xuất hiện trong tool
  result của lượt và giá trị khớp dữ kiện (chuẩn hóa, không bắt exact string).

## 4. Dữ liệu persona và câu chuyện — và câu trả lời cho “Luna bịa story thì sao?”

Đúng như yêu cầu cuối: **khi hội thoại cần một câu chuyện/chỉ tiết mà DB
chưa có, model chỉ được ĐỀ XUẤT, engine kiểm chứng rồi lưu vào bảng sự
thật, và các lượt sau dùng lại canonical view đó** — không bịa trong
reply rồi bỏ đi:

1. Model gọi `propose_story` / `propose_addendum` / `propose_persona_fact`.
2. Engine kiểm tra: scope persona, key có được phép tạo không (key khóa như
   `vehicle_model`, `purchased_at` không được sửa để hợp thức hóa story),
   timeline (không tạo lái thử trước ngày mua, không thêm ngày giả),
   trùng/mâu thuẫn với candidate theo thực thể có cấu trúc + ngày theo độ
   chính xác (cùng nơi + cùng xe **chưa đủ** để kết luận cùng sự kiện).
3. Kết quả: `NEW_EVENT` -> lưu `event_id` ổn định, `fictional_generated=true`,
   version 1; `SAME_EVENT` -> tái sử dụng `event_id` cũ, không tạo trùng;
   `CONFLICT`/`AMBIGUOUS` -> không lưu, không khẳng định.
4. Model nhận `event_id` + canonical view rồi mới đưa chi tiết vào reply.
5. Addendum chỉ bổ sung qua `StoryRevision` (body gốc không bị ghi đè);
   correction có lịch sử + lý do; thu hồi đổi status, không xóa lịch sử.
6. Chỉ khi FakeSender báo sent, story mới được đánh dấu `told_in_message_id`.

**Cảnh báo trung thực:** cơ chế trên giảm trùng/mâu thuẫn có cấu trúc,
nhưng **không bảo đảm tuyệt đối** không có mâu thuẫn ngữ nghĩa — review
vẫn là lớp cuối, và story hư cấu vẫn gắn nhãn `fictional_generated`.

## 5. Cài đặt và chạy sandbox

```bash
cd ~/workspace/persona-dialogue
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
# Mở http://127.0.0.1:8000/ rồi đi đúng 3 bước trên giao diện:
#   Bước 1: bấm "Seed persona mẫu" -> chọn persona ở dropdown
#   Bước 2: nhập nội dung bài viết -> bấm "Tạo bài viết" (POST /threads)
#   Bước 3: chọn người bình luận -> nhập comment -> bấm "Gửi comment",
#           persona trả lời ngay dưới đó kèm trạng thái sent/review/error
#           và trace mở rộng (planner, tool, fact/story đã lưu, validator)
# API: POST /seed, POST /threads, POST /turns, GET /personas/hoang-nam,
#      GET /threads/{id}/messages, POST /personas/{id}/stories/{event_id}/revoke
# Gửi lặp: gửi lại cùng header Idempotency-Key để kiểm tra không gửi trùng.
# Lưu ý: server chạy trên máy này (127.0.0.1:8000); nếu mở từ máy khác,
# cần chạy lại project trên máy đó hoặc expose theo cách cậu vẫn dùng.
```

Docker Compose (app + Postgres 16):

```bash
docker compose up --build   # app ở http://127.0.0.1:8000/
```

## Khắc phục lỗi thường gặp

**`OpenRouter 400 Bad Request` ở lần gọi thứ 2 (sau plan_turn):**
Nguyên nhân gốc (trace 30/10): pipeline cũ append tool message trực
tiếp mà không có assistant message chứa `tool_calls` và không có
`tool_call_id`, nên OpenAI/OpenRouter reject history ở lần gọi sau.
Đồng thời Luna trả plan_turn sai schema chặt (goal là câu tự do,
`source="user"`, `type="question"`, `response_direction` có field lạ)
nên plan bị reject và planner rỗng. Đã sửa: pipeline luôn gửi
assistant `tool_calls` + tool message kèm `tool_call_id` khớp nhau (cả
vòng chính lẫn vòng repair), normalize plan của Luna về vocab Planner
trước khi validate, và OpenRouter không retry lỗi 400 mà trả kèm body
để chẩn đoán. Nếu vẫn gặp 400 sau bản sửa, xem body trong
`validator_errors` — thường là model không tồn tại, key hết hạn, hoặc
hết credit, không phải lỗi history nữa.

### Lỗi thiếu driver Postgres

**`ModuleNotFoundError: No module named 'psycopg'` khi dùng Neon:**
SQLAlchemy 2.0 với URL `postgresql://` mặc định dùng driver psycopg v3.
Lỗi này nghĩa là venv chưa có driver (requirements.txt cũ thiếu nó, đã
sửa). Chạy trong venv đang dùng, rồi khởi động lại server — không cần
tải lại toàn bộ project nếu chỉ sửa bước này:
```bash
pip install 'psycopg[binary]'
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
Lưu ý: dấu nháy quanh `psycopg[binary]` là bắt buộc trên zsh/macOS, nếu
không shell sẽ hiểu `[binary]` là glob. URL Neon dạng
`postgresql://USER:PASSWORD@HOST.neon.tech:5432/DB` — app tự thêm
`sslmode=require` nếu URL chưa có; không cần tự thêm tay, nhưng thêm tay
cũng không sao. Nếu vẫn lỗi, kiểm tra cậu đang ở đúng venv không (prompt
phải hiện `(venv)` và `which python` trỏ vào `persona-dialogue/venv`).

## 6. Cấu hình provider và budget

```bash
cp .env.example .env   # KHÔNG commit .env, KHÔNG dán key vào chat
# Sửa trong .env (app tự đọc .env ở thư mục project, không cần export tay):
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=<key qua env, không ghi vào repo>
OPENROUTER_MODEL=openai/gpt-6-luna
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DB?sslmode=require
# Sau khi sửa .env phải KHỞI ĐỘNG LẠI server (cấu hình đọc lúc khởi động).
# Kiểm tra đã sang Luna chưa: curl http://127.0.0.1:8000/health
#   -> provider phải là OpenRouterProvider, openrouter_configured: true
# Mặc định nếu không có .env/key: LLM_PROVIDER=fake (offline, không tốn phí).
```

Chạy thử Luna thật (opt-in, tốn phí thật, cap theo lượt ở §6):

```bash
.venv/bin/python scripts/live_smoke.py
# Thiếu key -> dừng ngay, exit 2, không gọi mạng, không in key.
# Đủ key -> chạy 3 lượt (màu xe, quan tâm mua, giá mẫu) trên DB in-memory.
```

Giới hạn (env): `LLM_TIMEOUT_S=30`, `LLM_MAX_RETRIES=2`,
`MAX_TOOL_ROUNDS=6`, `MAX_VALIDATOR_REPAIRS=2`,
`MAX_LLM_CALLS_PER_TURN=10`, `MAX_COST_USD_PER_TURN=0.50`.
Vượt budget -> lượt dừng với lý do rõ ràng, không tự gửi. Usage (tokens/
cost) từ provider được ghi vào trace khi provider cung cấp; Fake cost = 0.

**Live evaluation là opt-in riêng, chưa chạy trong phiên này** (chưa có key):
khi có key, chạy sandbox với Luna thật, đặt cap thấp, ghi transcript +
cost, và chỉ coi là tín hiệu sau khi có tập hội thoại thật đã được phép
sử dụng (xem § Hạn chế).

## 7. Kiểm thử

```bash
.venv/bin/python -m pytest tests -q
# Kết quả đã chạy: 31 passed
.venv/bin/python scripts/eval_synthetic.py    # 10/10 (synthetic + Fake)
.venv/bin/python scripts/make_transcript.py   # runs/transcript.json
```

Test bao phủ §18 theo nhóm: hội thoại (fact có sẵn/chưa có, DB lỗi ->
UNKNOWN, đa ý, đa câu hỏi, tham chiếu 1 xe vs nhiều xe, đã mua/chỉ xem/
hỏi hộ, đổi ý, dừng, phản hồi ngắn, không hỏi lặp), story (tạo, đọc lại,
diễn đạt khác tái dùng event_id, addendum + addendum mâu thuẫn, cô lập 2
persona, timeline trước ngày mua, thu hồi, gửi lỗi + recovery), product
(hợp lệ, hết hạn, sai mẫu, xung đột, không nguồn), an toàn (idempotency,
trùng ID khác nội dung, budget/timeout, opt-out, buying interest không
handoff nhầm, giao dịch thật không tự xác nhận), chất lượng (smalltalk
được chấp nhận, claim thiếu nguồn bị chặn, không tự thêm sự kiện chưa lưu).

## 8. Transcript nhiều lượt (pipeline thật, FakeProvider) — tóm tắt

File đầy đủ (input, planner, tool call, dữ kiện lưu, validator, reply)
ở `runs/transcript.json`; script ở `scripts/make_transcript.py`.
Một thread, actor-1, bài viết về Creta:

| Lượt | Khách | Trạng thái | Reply (rút gọn) |
|---|---|---|---|
| 1. Hỏi màu xe | Bác đi xe màu gì thế? | sent | Mình đang đi xe màu trắng đó bạn. (fact mới do propose -> lưu, rồi mới trả lời) |
| 2. Quan tâm mua | Tôi cũng muốn mua một chiếc như vậy. | sent | Mình đang đi Hyundai Creta đó bạn. Bạn thích mẫu này ở điểm nào nhất? (buying interest, KHÔNG handoff) |
| 3. Hỏi hộ người khác | Em đang cân nhắc con này cho bố. | sent | Mình đang đi Hyundai Creta đó bạn. (intended_user = bố, không gán cho self) |
| 4. Tạo story | Bác có kỷ niệm nào đáng nhớ với xe không? | sent | Đi Creta về Nam Định cuối tuần... (story mới, event_id evt_..., fictional_generated) |
| 5. Truy xuất story cũ | Kể lại chuyến đi đó đi bác. | sent | Cùng summary trên, không tạo story mới |
| 6. Bổ sung addendum | Đi cùng ai thế bác? | sent | Chuyến đó mình đi cùng cả nhà... (addendum, story lên version 2, body gốc giữ nguyên) |
| 7. Hỏi product fact | Giá Creta bản cao nhất bao nhiêu? | sent | 599 triệu đồng (giá mẫu, chưa phải giá thực tế) |
| 8. Khách muốn dừng | Thôi, mình không muốn tiếp tục nữa. | sent | Ok bạn, mình dừng ở đây nhé. (không hỏi thêm) |

Lưu ý: ví dụ 1 trong đề bài dùng Lexus RX 350; transcript dùng persona
Creta nên reply là Creta — logic tham chiếu (“như vậy” -> xe trong bài)
là giống nhau, đã có test riêng cho trường hợp nhiều xe -> ambiguous.

## 9. Giả định (đã chọn để không chặn tiến độ, đều điều chỉnh được)

- Persona mẫu Hoàng Nam đúng §15; màu xe và chuyến đi cụ thể KHÔNG seed
  sẵn, để đi qua luồng propose -> lưu (Fake chọn “trắng” và chuyến Nam
  Định làm giá trị đề xuất xác định; Luna thật sẽ tự chọn và vẫn phải qua
  engine kiểm chứng).
- Planner là tool đầu tiên trong vòng tool calling thay vì 1 LLM call
  riêng (lý do ở §1).
- `submit_final_reply` là một tool để ép structured JSON thay vì parse
  regex từ text tự do.
- Product facts seed là **mẫu** (`is_sample=true`), không phải giá thật.
- FakeProvider chỉ là mô phỏng cho test/sandbox; pattern của nó không
  phải logic của engine và không được dùng để kết luận về Luna thật.
- Giới hạn story/ngày/persona có trong config nhưng chưa hard-cap trong
  tool (tránh chặn demo); version/conflict mới là rào chắn đã test.

## 10. Khác biệt với MAT_Intent (để không mang giới hạn cũ sang)

- Không có `SEED_FACTS` mặc định làm giá thật: mọi product fact mẫu đều
  gắn nhãn sample và reply phải ghi rõ.
- Không có trạng thái PENDING 30 phút phức tạp: story/addendum được lưu
  ngay sau khi engine chấp nhận, trước khi gửi; chỉ “đã kể” mới chờ sent.
- Schema một nguồn sự thật (models.py) cho cả SQLite và Postgres, thay
  vì hai bộ migration lệch nhau.
- Validator phân biệt claim/smalltalk/handoff/opt-out thay vì chặn theo
  từ khóa đơn lẻ (vd: “mua” không tự thành handoff, “giá” không tự bị chặn).

## 11. Hạn chế, phần chưa kiểm chứng và điều kiện trước khi dùng thật

- **Chưa kiểm chứng:** gọi Luna thật qua OpenRouter (chưa có key trong
  phiên này — chỉ có adapter + smoke test cũ của connector ngày 23/10);
  chạy Postgres/Neon thật (chỉ chạy SQLite + review migration SQL);
  concurrency thực dưới Postgres (test hiện tại là logic version/conflict
  + idempotency, chưa load test); độ tự nhiên với khách thật.
- **Chưa làm:** tích hợp Facebook/Meta (cố ý ngoài phạm vi sandbox);
  live evaluation có budget cap (lệnh riêng, opt-in, cần key + cap);
  tập hội thoại thật đã được phép sử dụng + xử lý riêng tư cho đánh giá
  sau (§19); hard-cap story/ngày/persona; semantic checker bằng LLM
  (hiện là kiểm tra có cấu trúc + similarity — không tuyên bố tuyệt đối).
- **Trước khi dùng thật cần:** (1) cấu hình key Luna + Neon qua env như §6;
  (2) chạy live eval với cap thấp và đối chiếu transcript với Luna;
  (3) lập tập human-gold (hiện vẫn trống, giống MAT_Intent) và blind A/B;
  (4) rà soát product facts thật, thay toàn bộ fact mẫu; (5) bật review
  cho mọi story hư cấu trước khi persona đó được dùng ngoài sandbox.
- Test synthetic + unit pass **không phải** bằng chứng hệ thống tự nhiên
  với khách thật.
