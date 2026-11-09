# Tích hợp API hội thoại

Tài liệu này mô tả API hiện có để một hệ thống đối tác tạo hội thoại, gửi
comment vào pipeline và nhận `reply_text`. Ví dụ dùng địa chỉ local; khi triển
khai, thay bằng địa chỉ gateway do bên vận hành cung cấp.

**Ranh giới hiện tại:** có hai chế độ gửi.

- **Sandbox** (chưa cấu hình `PARTNER_API_KEY`): ứng dụng dùng
  `FakeSender`. `status: "sent"` nghĩa là pipeline đã hoàn tất bước gửi
  *mô phỏng*, không có nghĩa Facebook, Zalo hay kênh đối tác đã nhận tin.
  Đối tác phải tự gửi `reply_text` tới kênh của mình.
- **Partner mode** (`PARTNER_API_KEY` đã cấu hình): pipeline KHÔNG tự gửi.
  Lượt sạch stage ở `status: "ready_for_delivery"` kèm `message_id`; đối
  tác gửi `reply_text` ra kênh thật rồi gọi endpoint xác nhận
  `POST /threads/{thread_id}/messages/{message_id}/delivery-confirmation`
  với `sent`, `failed` hoặc `unknown`. Chỉ khi xác nhận `sent` thì
  `status` mới thành `"sent"` và dữ kiện/story/câu hỏi pending của lượt
  đó mới được kích hoạt. `sent` ở partner mode vì vậy nghĩa là kênh ngoài
  đã nhận tin (theo báo cáo của đối tác), không phải gửi mô phỏng.

Trước khi dùng với khách thật cần thống nhất thêm timeout, retry và cách
chống gửi trùng của kênh đối tác. Nếu kết quả gửi không rõ, không tự gửi
lại cho tới khi đối tác hoặc kênh gửi xác minh. Lượt đang chờ xác nhận cần
được cô lập theo hội thoại để không làm lẫn dữ kiện của các lượt khác.

## Luồng tích hợp

1. Lấy danh sách persona bằng `GET /personas`; persona phải tồn tại trong DB.
2. Gọi `POST /threads` **một lần cho mỗi hội thoại/bài viết** và lưu `thread_id`
   để dùng lại. Tự đảm bảo hai request tạo thread cho cùng bài không chạy lặp:
   endpoint tạo thread không có idempotency key. Ở partner mode, gửi kèm
   header `X-API-Key`; thread được gán tenant của key đó.
3. Mỗi comment gọi `POST /turns` với `thread_id`, `persona_id`, `actor_id`,
   `text` và một `Idempotency-Key` ổn định cho comment đó. Đợi kết quả và đọc
   `status`, `reply_text`, `message_id`. Trả lời một comment cụ thể thì truyền
   thêm `parent_message_id` (id của user message cha) để history chỉ lấy
   nhánh đó.
4. Với `sent` (sandbox) và `reply_text` có nội dung, đối tác gửi văn bản qua
   kênh của mình. Với `ready_for_delivery` (partner mode), đối tác gửi văn
   bản qua kênh thật rồi gọi endpoint xác nhận (xem mục dưới). Lưu
   `message_id` và trạng thái gửi của **kênh đối tác** để chống gửi trùng, kể
   cả khi API replay cùng câu trả lời. Với `skipped`, không gửi gì. Với
   `error`, không mặc định gửi `reply_text`: xử lý lỗi và xác minh trạng thái
   trước khi thử lại. Với `review`, reply bị giữ lại vì lỗi validator
   blocking — không gửi, xem `validator_errors`.

Nên xử lý tuần tự các comment trong cùng thread để bảo toàn ngữ cảnh. Không
dùng `POST /seed` trong luồng comment: seed đồng bộ dữ liệu nguồn và có thể
xóa dữ liệu của persona không còn trong nguồn.

## Tạo thread

```bash
curl -X POST 'http://127.0.0.1:8000/threads' \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: <partner-api-key>' \
  -d '{"persona_id":"AUTO_029","post_id":"post-123","post_text":"Bài viết về Hyundai Creta","post_vehicle_models":["Hyundai Creta"]}'
```

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `persona_id` | string, bắt buộc | ID persona đã tồn tại. |
| `post_id` | string | ID bài viết phía đối tác; mặc định `post-1`. |
| `post_text` | string | Nội dung bài làm ngữ cảnh; mặc định chuỗi rỗng. |
| `post_vehicle_models` | array của string | Các mẫu xe trong bài; mặc định `[]`. |
| `tenant` | string | Chỉ có ý nghĩa ở sandbox: gán tenant cho thread. Ở partner mode, tenant luôn lấy từ API key, body `tenant` bị bỏ qua. |

Ví dụ response rút gọn:

```json
{"thread_id":"thread-id-do-server-tao","persona_id":"AUTO_029","post_id":"post-123","post_text":"Bài viết về Hyundai Creta","post_vehicle_models":["Hyundai Creta"],"tenant":"acme"}
```

Lưu `thread_id` trả về, không tự dùng `post_id` thay cho `thread_id`. Persona
không tồn tại trả HTTP `404`. Ở partner mode, thiếu/sai `X-API-Key` trả HTTP
`401`.

## Gửi comment

```bash
curl -X POST 'http://127.0.0.1:8000/turns' \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: <partner-api-key>' \
  -H 'Idempotency-Key: comment-123' \
  -d '{"thread_id":"thread-id-do-server-tao","persona_id":"AUTO_029","actor_id":"customer-123","text":"Bác đang đi xe gì?"}'
```

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `thread_id` | string hoặc null | ID từ `/threads`; nên luôn truyền khi tích hợp. Nếu bỏ trống, API tạo thread mới. |
| `persona_id` | string, bắt buộc | Persona sở hữu thread; phải khớp thread. |
| `actor_id` | string, bắt buộc | ID ổn định của khách phía đối tác trong hội thoại. |
| `text` | string, bắt buộc | Nội dung comment. |
| `actor_name` | string | Tên hiển thị khi actor được tạo lần đầu; mặc định `Khách`. |
| `post_id`, `post_text`, `post_vehicle_models` | string, string, array string | Dùng để tạo thread khi không truyền `thread_id`; mặc định lần lượt là `post-1`, `""`, `[]`. Với thread đã tồn tại, ngữ cảnh bài lấy từ thread đó. |
| `parent_message_id` | string hoặc null | ID user message cha mà comment này trả lời (branch). Có parent thì history chỉ lấy nhánh đó (chuỗi cha + lượt của actor + reply đã hiển thị); không có thì lấy 20 message gần nhất của thread. |
| `tenant` | string hoặc null | Ở partner mode, tenant luôn lấy từ API key, body `tenant` bị bỏ qua. Ở sandbox, tenant này gán cho thread mới tạo. |
| `message_id` | string hoặc null | Được schema chấp nhận nhưng hiện không được dùng để chống trùng; dùng header `Idempotency-Key`. |

`Idempotency-Key` là header tùy chọn nhưng nên truyền. Giữ key ổn định cho cùng
comment, tối đa 128 ký tự; dùng key mới cho comment mới. `actor_id` và
`persona_id` nên không quá 64 ký tự, `post_id` không quá 128 ký tự để phù hợp
chiều dài cột DB. Mỗi tenant cần quản lý namespace ID riêng tại gateway. Ở
partner mode, thread phải thuộc tenant của API key, nếu không trả HTTP `403`;
thiếu/sai key trả HTTP `401`.

Ví dụ response rút gọn khi gửi mô phỏng thành công (sandbox):

```json
{
  "status": "sent",
  "thread_id": "thread-id-do-server-tao",
  "message_id": "message-id-do-server-tao",
  "reply_text": "Mình đang đi Hyundai Creta đó bạn.",
  "validator_errors": [],
  "trace": {"provider": "OpenRouterProvider", "cost_usd": 0.001, "cost_known": true}
}
```

Ví dụ response rút gọn ở partner mode (chưa gửi ra kênh thật):

```json
{
  "status": "ready_for_delivery",
  "thread_id": "thread-id-do-server-tao",
  "message_id": "message-id-do-server-tao",
  "reply_text": "Mình đang đi Hyundai Creta đó bạn.",
  "validator_errors": [],
  "trace": {"provider": "OpenRouterProvider", "cost_usd": 0.001, "cost_known": true}
}
```

Đây chỉ là ví dụ hình dạng; nội dung và trace thực tế phụ thuộc lượt chạy.
Response còn có `planner`, `tool_calls`, `saved_customer_facts`, `saved_stories`
và `flow`; các trường này có thể chứa dữ liệu khách hoặc chi tiết kỹ thuật,
không nên hiển thị công khai cùng `reply_text`.

Pipeline vẫn chạy validator và vòng sửa theo cấu hình. Lỗi blocking (sai
nguồn/sai xe/dữ liệu cá nhân/opt-out/chốt giao dịch/không có nguồn) giữ lượt ở
`review` — `status` là `"review"`, không gửi, `validator_errors` nêu lý do.
Lỗi soft (giọng điệu/câu hỏi chung) được sửa tối đa 1 vòng rồi gửi kèm lỗi
trong `validator_errors` và trace. Đừng hiểu `sent` là dữ kiện đã được xác minh
hoàn toàn.

Mỗi lượt mới có `trace.intent_score` gồm `score` (0–100), `method: "rules_v1"`
và `factors` (nhãn, số điểm). Đây là điểm ưu tiên tham khảo theo luật, không
phải xác suất và không thay đổi quyết định trả lời. `trace.intent_score_llm`
là điểm shadow do model chấm theo cùng rubric (cùng ý nghĩa — mức ưu tiên
chú ý, không phải xác suất), chỉ để tham khảo/so sánh, không điều khiển gate
hay bất kỳ quyết định nào; `None` khi model không trả điểm. Quyết định thực tế
nằm ở `trace.gate.action` (`bypass`, `faq`, `pipeline`) cùng `reason`. Điểm
được lưu trong trace của message khách và giữ nguyên khi replay; lượt cũ có thể
không có điểm. `planner.intents[].confidence` là độ tin cậy nhận diện từng
intent, không phải điểm này và có thể dùng giá trị mặc định.

`POST /threads/{thread_id}/turns` là endpoint tương đương cho thread có sẵn;
nếu body có `thread_id` thì phải trùng URL, nếu không trả HTTP `400`.

## Xác nhận đã gửi trên kênh thật (partner mode)

Sau khi gửi `reply_text` ra kênh thật, đối tác báo kết quả về:

```bash
curl -X POST 'http://127.0.0.1:8000/threads/<thread_id>/messages/<message_id>/delivery-confirmation' \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: <partner-api-key>' \
  -d '{"status":"sent","channel_message_id":"fb-123"}'
```

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `status` | string, bắt buộc | `sent` \| `failed` \| `unknown`. |
| `channel_message_id` | string hoặc null | ID message phía kênh đối tác (để đối soát). |
| `error` | string hoặc null | Lý do thất bại (khi `failed`). |

- `sent`: message chuyển sang `sent`; dữ kiện/story/câu hỏi pending của lượt
  được kích hoạt từ snapshot đã lưu lúc stage. Nếu canon đã đổi sau khi
  stage (fact/story liên quan bị sửa/thu hồi), pending candidates không được
  kích hoạt mà bị reject, ghi dấu `trace.delivery_recovery_blocked` — message
  thì vẫn `sent` vì đã gửi thật trên kênh.
- `failed`: message chuyển sang `error`; pending candidates bị reject, không
  kích hoạt. Đối tác xử lý gửi lại bằng lượt mới (key mới), không gửi lại
  cùng `message_id`.
- `unknown`: message giữ nguyên `ready_for_delivery`; đối tác xác minh rồi báo
  lại `sent` hoặc `failed`.
- **Idempotent:** báo lại cùng status trả kết quả cũ (`idempotent_replay:
  true`), không kích hoạt dữ kiện lần hai.
- **Mâu thuẫn:** báo khác status với trạng thái đã xác nhận (vd đã `sent`
  rồi báo `failed`, hoặc đã `failed` rồi báo `sent`) trả HTTP `409` và ghi
  `trace.delivery_conflict` để đối soát; trạng thái message không đổi.
- Message chưa ở `ready_for_delivery` (`draft`/`sending`/`review`) trả HTTP
  `409`. `status` lạ trả HTTP `400`, message không tồn tại trả HTTP `404`,
  thiếu/sai key trả HTTP `401`, thread khác tenant trả HTTP `403`.

## Idempotency và retry

- Key được giới hạn theo `(thread_id, actor_id, Idempotency-Key)`. Cùng scope,
  cùng nội dung (sau chuẩn hóa khoảng trắng) sẽ replay `message_id` và
  `reply_text` cũ; `trace.idempotent_replay` có thể cho biết đây là replay.
  Với lượt đã `skipped`, retry cùng key giữ nguyên `skipped` và không tạo
  thêm message, kể cả khi trạng thái hội thoại sau đó thay đổi.
  Cùng key nhưng nội dung khác trả `status: "error"` với
  `idempotency_conflict`. Key giống nhau ở thread hoặc actor khác là lượt riêng.
- Nếu gọi `/turns` mà không có `thread_id`, mỗi request tạo thread mới: gửi
  lại cùng key không đảm bảo replay. Tạo và lưu thread trước khi gửi lượt đầu.
- Khi lượt đang xử lý hoặc một retry đang gửi, response có thể báo
  `idempotency_in_progress` hoặc `recovery_in_progress`; tránh chạy song song.
- Lỗi sau khi có draft có thể được thử lại bằng cùng thread, actor, text và key.
  Nếu bối cảnh đã thay đổi, draft cũ thiếu dữ liệu phục hồi hoặc chưa có reply,
  API có thể trả `recovery_unavailable`. `delivery_unknown` nghĩa là kết quả
  gửi nội bộ chưa xác định; không tiếp tục gửi mù.
- Idempotency này chỉ bảo vệ lượt trong API và FakeSender, **không chống gửi
  trùng trên kênh ngoài**. Đối tác cần lưu mapping comment → `thread_id`, key,
  `message_id` và trạng thái gửi ngoài, cùng cơ chế retry/idempotency của kênh
  đó. Replay `sent` không tự chứng minh đối tác đã gửi cho khách.

## Trạng thái và lỗi HTTP

Kiểm tra cả HTTP status lẫn trường `status` trong JSON:

| Giá trị `status` | Xử lý tích hợp |
|---|---|
| `sent` | Sandbox: đã qua FakeSender. Partner mode: đối tác đã xác nhận kênh ngoài đã nhận tin. Kiểm tra thêm `validator_errors` nếu cần audit. |
| `ready_for_delivery` | Chỉ ở partner mode: reply đã stage, chưa gửi ra kênh thật. Đối tác gửi `reply_text` qua kênh rồi gọi endpoint xác nhận. Dữ kiện/story/câu hỏi pending chưa kích hoạt. |
| `review` | Lỗi validator blocking giữ lượt lại; `reply_text` có nội dung nhưng chưa gửi. Không tự gửi; xem `validator_errors`. |
| `skipped` | Gate quyết định không trả lời; `reply_text` thường là null. |
| `error` | Lượt không hoàn tất; xem `validator_errors` và `trace`, không tự gửi reply chỉ vì có text. |
| `draft`, `blocked` | Có trong schema/lịch sử; không coi là đã gửi. |

Với `POST /turns`, lỗi do pipeline trả `status: "error"` vẫn có thể đi cùng
HTTP `200`. Với `POST /threads/{thread_id}/turns`, cùng loại lỗi trả HTTP
`502`. Cấu hình provider không hợp lệ có thể trả HTTP `502` từ cả hai
endpoint; body lỗi FastAPI có dạng `detail` thay vì `TurnResponse`. Body không
hợp lệ trả `422`. Không suy ra thành công chỉ từ mã HTTP `200`.

## Cấu hình, chi phí và vận hành

- `GET /health` cho biết `provider`, `status`, loại DB và model; chỉ kiểm tra
  `provider` thực tế khi xác định Fake hay OpenRouter.
- Mặc định `LLM_PROVIDER=fake` và SQLite local. Đặt `LLM_PROVIDER=openrouter`,
  `OPENROUTER_MODEL` và `OPENROUTER_API_KEY` trong môi trường server để dùng
  model thật; không đưa key vào request đối tác hoặc source code. OpenRouter
  có thể tính phí; `trace.cost_usd` là chi phí provider báo được và
  `trace.cost_known` cho biết có thiếu dữ liệu chi phí hay không. Giới hạn
  `MAX_COST_USD_PER_TURN` được kiểm sau lời gọi, không phải trần chi phí cứng.
- Index RAG cần được build vào đúng `DATABASE_URL`; muốn có đại lý phải build
  kèm `--company data/dealers.json`. Kiểm tra `trace.rag_status` và evidence;
  trạng thái `stale` không tự chặn phục vụ dữ liệu cũ.
- API hiện **không có xác thực/ủy quyền hoặc phân quyền tenant** ở sandbox.
  Khi `PARTNER_API_KEY` được cấu hình (partner mode), các endpoint partner
  (`POST /threads`, `POST /turns`, `POST /threads/{id}/turns`, endpoint xác
  nhận) yêu cầu header `X-API-Key` đúng key, và thread chỉ được truy cập
  bởi đúng tenant của key (`PARTNER_TENANT`, mặc định `"default"`). Không
  expose trực tiếp ra Internet: đặt sau gateway có TLS, kiểm soát truy cập,
  phân tách tenant và giới hạn tốc độ; chỉ mở các endpoint thực sự cần thiết.
  Endpoint quản trị/seed/trace (`/seed`, `/admin/*`, `/personas/*`,
  `/threads/{id}/messages`) là mặt sandbox, tách riêng khỏi API partner —
  không dùng chung key partner, không expose cho đối tác. Bảo vệ các endpoint
  này vì có thể thay đổi hoặc lộ dữ liệu hội thoại. Cấu hình logging phù hợp
  để không làm lộ key hay dữ liệu khách.
