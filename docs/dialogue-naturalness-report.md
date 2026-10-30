# Dialogue naturalness — nhánh improve/dialogue-naturalness

Brief 30/10: ưu tiên cao nhất là persona hiểu đúng người dùng, trả lời
tự nhiên, nhớ điều đã nói, khơi gợi đúng lúc. Dùng pipeline và công
nghệ hiện có; sửa code/prompt/context/test thật, không chỉ đề xuất.
Không thêm vector DB, graph memory, framework agent, không viết lại hệ
thống. Nhánh riêng từ main `3f055f8`, không chạm code người khác.

## Chẩn đoán (§2) — nguyên nhân gốc, đã xác minh trong code/live

### RC1. Validator so khớp claim free-text quá chặt — LIVE-PROVEN
Live test VM 30/10 (Luna thật qua vault surrogate, $0.0047) cho thấy 3
lượt trả lời ĐÚNG vẫn bị đẩy sang review:
- "Mình đang đi Hyundai Creta bản 1.5 Cao cấp, mua hồi tháng 3/2024"
  → `persona_value_mismatch` vì claim value của Luna là câu tự nhiên
  ("Đang đi Hyundai Creta") còn fact chuẩn là "Hyundai Creta".
- Hỏi màu xe → Luna propose trắng, trả lời "Xe mình màu trắng nhé."
  → mismatch + uncovered màu, dù claim trỏ đúng fact vừa propose.
- Hệ quả dây chuyền: lượt review → proposal bị reject → lượt sau phải
  propose lại → người dùng thấy persona "không nhớ" màu vừa nói.
Nguyên nhân: `value_matches` ở dạng free-text chỉ chấp nhận chuỗi chính
xác; color uncovered check chỉ so chuỗi chính xác và bỏ qua liên kết
ref_id. Các kiểm tra typed (tiền/ngày/%/km) thì đúng và phải giữ.

### RC2. Model không được hướng dẫn claim value phải là giá trị chuẩn
Tool description của `submit_final_reply` không nói claim value phải
copy nguyên từ tool result, nên Luna viết value thành câu. Đây là lỗi
tool-usability, không phải lỗi model "không tuân thủ".

### RC3. Repair message mời gọi mất nội dung
Repair cũ: "Hãy sửa reply theo đúng lỗi trên, không thêm claim mới."
mà không đưa lại bản gốc → model/Fake trả câu chung chung an toàn,
làm mất câu trả lời đúng (đúng anti-pattern §9 nêu).

### RC4. Context giấu câu hỏi ĐÃ TRẢ LỜI
`_context_block` chỉ hiện pending + declined, không hiện answered.
Câu hỏi trả lời xong chỉ còn lại trong lịch sử dạng prose; model quên
liệt kê answered key là sẽ hỏi lại. Cùng họ lỗi: Fake parse context
bằng regex cũ "Câu hỏi đang chờ:" trong khi engine đã ghi "Câu hỏi
đang chờ của actor hiện tại:" — khiến answered keys của Fake luôn
rỗng (phát hiện khi test short-answer fail, đã sửa Fake cho khớp
format hiện tại).

### RC5. SYSTEM_PROMPT thiếu quy tắc từng phần / chỉ-xem / chủ thể
Runtime policy đã có (không nhảy sang ngân sách, tôn trọng chỉ xem,
family tag ≠ 7 chỗ...), nhưng SYSTEM_PROMPT — message rule Luna đọc
đầu tiên — chưa nêu: trả lời từng phần thay vì stop cả lượt; "chỉ xem
thôi" thì không hỏi ngân sách/thời gian mua; chủ thể thứ ba giữ nguyên
theo người đó; phản hồi ngắn là đáp án cho câu hỏi ngay trước, không
phải xác nhận mua/liên hệ.

### RC6. Sửa chủ thể: supersede cũ không xử lý đính chính
Supersede customer fact theo subject+predicate+relation là đúng cho
bổ sung ("mua cho bố và cho mẹ" cùng tồn tại — test cũ vẫn giữ), nhưng
không có đường nào cho đính chính ("mua cho bố" → "mua cho mình"):
fact cũ vẫn current, lượt sau thấy hai intended_user mâu thuẫn.

### Đã xác minh là KHÔNG hỏng, không sửa
Planner đa intent; history dạng message thật + 20 gần nhất; followup
chỉ tạo khi sent; chuyển chủ đề giữ pending (không redirected);
isolation theo actor; supersede theo relation (bố ≠ mẹ); story told
theo actor; typed money/date (5999 ≠ 599); câu hỏi/echo/phủ định được
miễn uncovered check; chặn transaction/private-request/opt-out/
refused-question. Sửa thêm vào những chỗ này sẽ là thừa.

## Thay đổi

- `app/engine/typed_values.py`: `_free_text_matches` — claim khớp fact
  khi fact bằng claim sau khi bỏ một tiền tố toàn từ đệm + hậu tố
  discourse, thử MỌI độ dài tiền tố (tránh strip tham lam: "Đây là xe
  đầu đời" khớp "xe đầu đời" ở tiền tố 2 từ, không strip luôn "xe").
  Policy chặt giữ nguyên: "trắng sữa" ≠ "trắng", "Creta" ≠ "Creta 1.5",
  "5999 triệu" ≠ "599 triệu" (typed path không đổi), "Tucson" ≠ "Creta".
- `app/engine/validator.py`: color uncovered chấp nhận claim trỏ ref_id
  tới fact đúng màu, hoặc claim value sau strip từ đệm bằng màu đó.
- `app/engine/tool_schemas.py` + `app/providers/openrouter.py`
  (SYSTEM_PROMPT): claim value = copy nguyên giá trị chuẩn từ tool
  result; trả lời từng phần, không stop cả lượt vì một ý thiếu; chủ
  thể thứ ba; chỉ xem thôi; phản hồi ngắn.
- `app/engine/pipeline.py`: context thêm mục "Câu hỏi ĐÃ TRẢ LỜI";
  repair message đưa lại bản gốc + yêu cầu chỉ sửa đúng lỗi, giữ
  nguyên ý, không xóa claim, không đổi chủ đề, không câu chung chung.
- `app/engine/tools.py` + tool schema `propose_customer_fact`: arg mới
  `correction=true` — fact đính chính supersede mọi fact current cùng
  predicate của actor đó dù khác subject/relation. Không tự suy
  correction từ keyword; model phải khai báo rõ.
- `app/providers/fake.py`: sửa regex parse câu hỏi đang chờ cho khớp
  format context hiện tại (không thêm keyword mới).
- `tests/test_dialogue_naturalness.py` (14 test): hai fact một lượt;
  quan tâm mua ≠ giao dịch; trả lời ngắn → answered + không hỏi lặp;
  chuyển chủ đề giữ pending nhưng không hỏi lại; hỏi hộ rồi sửa chủ
  thể → supersede; chỉ xem thôi → không followup; kể lại story cũ
  không tạo story mới; hai actor isolation; hội thoại dài vẫn nhớ
  canon; trả lời từng phần khi thiếu product fact; validator tolerance
  (+ các negative policy); color claim qua ref_id; repair giữ ý;
  context hiện answered questions.
- `scripts/live_eval_dialogue.py`: live eval opt-in (LIVE_EVAL_DIALOGUE=1
  + OPENROUTER_API_KEY, budget mặc định 1.0 USD, exit 2 khi thiếu),
  10 case / 17 lượt theo đúng §10, ghi `runs/live_eval_dialogue.json`
  để human review; không model judge, không tự kết luận naturalness.

## Kiểm thử đã chạy

- Full suite: 93 passed (79 cũ + 14 mới), 0 failed.
- Live eval dialogue trên VM bằng Luna thật qua vault surrogate
  (không dùng key dán chat, không ghi key vào log): xem mục dưới.

## Kết quả live eval dialogue (Luna thật)

Chạy 30/10 trên VM: Luna `openai/gpt-6-luna` qua vault surrogate
(không key dán chat), FakeSender, SQLite in-memory, 10 case / 17 lượt,
tổng cost đã biết **$0.0182** (~$0.001/lượt). **0 validator error
trên cả 17 lượt** (trước đó probe 5 lượt đã có 3 lượt review).

- two_facts_one_turn: ✅ sent, đủ cả hai fact (Creta + tháng 3/2024).
  Trước fix, đúng lượt này đã bị review.
- interest_not_transaction: ✅ không handoff, không xác nhận giao dịch;
  ⚠️ Luna vẫn nói "bạn cũng đang nhắm Hyundai Creta à?" — trong setup
  này bài viết và xe persona đều là Creta nên không sai, nhưng là gán
  mẫu xe hơi vội từ "một chiếc như vậy". Không sửa ép trung lập ở đây
  vì sẽ làm mất tự nhiên khi hai neo trùng nhau.
- short_answer_priority: ✅ lượt 2 ghi nhận "ưu tiên tiết kiệm" thành
  fact, không hỏi lại câu ưu tiên; chỉ hỏi lại câu làm rõ mẫu xe (vẫn
  chưa được trả lời) — đúng: câu chưa trả lời được giữ, câu đã trả
  lời không hỏi lại.
- topic_switch_keeps_pending: ✅ lượt 2 trả lời thẳng "Creta 1.5 Cao
  cấp", không lôi lại câu hỏi cũ, không ép quay lại chủ đề cũ.
- third_party_correction: ✅ lượt 1 xoay theo nhu cầu của bố; lượt 2
  nói rõ "bạn mua cho bản thân chứ không phải cho bố" rồi tiếp tục
  theo nhu cầu của khách. (Mức reply; supersede DB đã chứng minh
  bằng unit test, live JSON không soi DB.)
- browse_only: ✅ goal stop, "cứ xem thong thả nhé", không hỏi ngân
  sách/thời gian mua.
- partial_answer_missing_product: ✅ trả lời phần persona (Creta),
  hỏi làm rõ "con này" là Santa Fe hay Creta, nói rõ chưa có giá đã
  xác minh — không bịa giá, không từ chối cả lượt.
- color_propose_and_recall: ✅✅ lượt 1 sent "màu trắng", lượt 2 nhớ
  lại đúng "Creta trắng". Trước fix cả hai lượt đều review và proposal
  bị reject nên lượt sau phải propose lại.
- tucson_vague_buy: ✅ goal clarify, hỏi "SUV như Tucson nhà mình hay
  Creta trong bài" — không khẳng định khách cân nhắc Creta.
- long_thread_recall: ✅ sau 6 lượt chào hỏi, hỏi xe vẫn trả lời đúng
  Creta 1.5 Cao cấp (canon retrieval, không phụ thuộc history window).

Transcript đầy đủ: `runs/live_eval_dialogue.json` (kèm theo nhánh).

### Chạy lại live eval từ code trên nhánh (review theo yêu cầu)
Lần chạy thứ hai, từ fresh clone nhánh, cùng script và budget: 10
case / 17 lượt, **$0.0179, 15 sent / 2 review**. Hai lượt review đều
là validator bắt ĐÚNG, không phải chặn nhầm như trước fix:
- interest_not_transaction: Luna claim product "Hyundai Creta" nhưng
  lượt đó không có product tool result (`unsourced_claim`). Nội dung
  reply đúng, chỉ thiếu nguồn claim.
- color_propose_and_recall lượt 1: Luna nhắc "Hyundai Creta của mình"
  nhưng không kèm claim vehicle_model (`uncovered` ownership). Lượt 2
  sau đó sent với cùng nội dung (đủ claim).
Lần chạy trước 17/17 sent, lần này 15/17: độ biến thiên do Luna đôi
khi lược claim, không phải regression của nhánh (code đường Luna
không đổi giữa hai lần chạy). Transcript lần chạy lại:
`runs/live_eval_dialogue_rerun.json`.

### Sửa thêm trong quá trình review (commit sau a8e34dd)
Smoke test HTTP phát hiện Fake không nhận diện cách hỏi "Thế màu xe
bác là gì nhỉ?" (pattern chỉ có "màu gì"/"xe màu", thiếu "màu xe") nên
trả câu xã giao thay vì màu canon — lỗi ở test double, không phải
engine (canon trắng đã active đúng, Luna live nhớ đúng). Đã thêm
pattern "màu xe" vào Fake; suite vẫn 93 passed, smoke kiểm tra lại:
hỏi lại màu trả đúng "trắng".

## Trước / sau (Fake + live, tóm tắt)

- Trước (live 30/10): hỏi xe + thời điểm mua, hỏi màu xe (2 lượt) đều
  đúng nội dung nhưng status review vì validator; màu propose bị
  reject nên lượt sau phải propose lại.
- Sau (Fake suite + live eval trên): các lượt đó phải về sent, màu
  propose ở lượt 1 thành canon và lượt 2 dùng lại; xem transcript
  live ở mục trên để đối chiếu từng case.

## Trường hợp vẫn thất bại / chưa kiểm chứng

- Live Luna với prompt mới cho các case tinh tế (sửa chủ thể bằng
  correction flag, browse-only, topic-switch dài) cần human review
  transcript trong `runs/live_eval_dialogue.json` — script không tự
  chấm naturalness.
- Neon Postgres: VM không có Postgres nên migration 003 mới chỉ kiểm
  chứng trên SQLite thật (001→002→003 áp sạch, đủ 9 bảng mới + cột
  mới). Chạy pipeline trên Neon vẫn cần chạy ở máy có DATABASE_URL
  (Mac của Phước, `.env` sẵn có) — không nhận DB URL qua chat.
- Human gold vẫn trống; naturalness với khách thật chưa kiểm chứng.

## Chạy sandbox để thử ngay

```bash
cd ~/workspace/persona-dialogue
git fetch origin && git checkout improve/dialogue-naturalness
.venv/bin/python -m pytest -q
.venv/bin/python -m uvicorn app.main:app --port 8000
# Mở http://127.0.0.1:8000, Seed -> chọn persona -> tạo bài -> chat.
# Live eval (cần key trong .env của máy chạy, không dán vào chat):
LIVE_EVAL_DIALOGUE=1 OPENROUTER_API_KEY=... \
  .venv/bin/python scripts/live_eval_dialogue.py
```
