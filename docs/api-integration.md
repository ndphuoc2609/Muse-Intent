# Tích hợp API hội thoại

Tài liệu này mô tả API hiện có để một hệ thống đối tác tạo hội thoại, gửi
comment vào pipeline và nhận `reply_text`. Ví dụ dùng địa chỉ local; khi triển
khai, thay bằng địa chỉ gateway do bên vận hành cung cấp.

**Ranh giới hiện tại:** ứng dụng chỉ dùng `FakeSender`. `status: "sent"` nghĩa
là pipeline đã hoàn tất bước gửi *mô phỏng*, không có nghĩa Facebook, Zalo hay
kênh đối tác đã nhận tin. Đối tác phải tự gửi `reply_text` tới kênh của mình.
API chưa có callback/endpoint xác nhận kênh ngoài đã gửi thành công: trạng thái
persona, dữ kiện và câu hỏi trong hệ thống được cập nhật khi FakeSender thành
công, ngay cả khi đối tác gửi ra kênh thật thất bại. Đây là giới hạn quan trọng
cần tính đến trước khi vận hành thực tế.

Phương án khuyến nghị khi hai bên thống nhất được hợp đồng: API trả bản trả lời
cùng `message_id`, bên đối tác gửi ra kênh và gọi lại một endpoint xác nhận
thành công/thất bại với cùng ID; dữ kiện chỉ được kích hoạt sau xác nhận thành
công. Endpoint xác nhận đó **chưa được triển khai**, nên đối tác hiện tại không
thể gọi nó. Trước khi dùng với khách thật cần thống nhất thêm timeout, retry và
cách chống gửi trùng của kênh đối tác.

Trong phương án này, API dự kiến trả trạng thái `ready_for_delivery`; đối tác
lưu `message_id` trước khi gửi và dùng lại ID đó khi báo `sent`, `failed` hoặc
`unknown`. Xác nhận lặp cùng kết quả phải không tạo thêm dữ kiện; xác nhận trái
ngược cần được đối soát. Nếu kết quả gửi không rõ, không tự gửi lại cho tới khi
đối tác hoặc kênh gửi xác minh. Lượt đang chờ xác nhận cần được cô lập theo
hội thoại để không làm lẫn dữ kiện của các lượt khác. Đây là đề xuất hợp đồng,
không phải các trạng thái hay endpoint có thể sử dụng ở phiên bản hiện tại.

## Luồng tích hợp

1. Lấy danh sách persona bằng `GET /personas`; persona phải tồn tại trong DB.
2. Gọi `POST /threads` **một lần cho mỗi hội thoại/bài viết** và lưu `thread_id`
   để dùng lại. Tự đảm bảo hai request tạo thread cho cùng bài không chạy lặp:
   endpoint tạo thread không có idempotency key.
3. Mỗi comment gọi `POST /turns` với `thread_id`, `persona_id`, `actor_id`,
   `text` và một `Idempotency-Key` ổn định cho comment đó. Đợi kết quả và đọc
   `status`, `reply_text`, `message_id`.
4. Với `sent` và `reply_text` có nội dung, đối tác gửi văn bản qua kênh của
   mình. Lưu `message_id` và trạng thái gửi của **kênh đối tác** để chống gửi
   trùng, kể cả khi API replay cùng câu trả lời. Với `skipped`, không gửi gì.
   Với `error`, không mặc định gửi `reply_text`: xử lý lỗi và xác minh trạng
   thái trước khi thử lại.

Nên xử lý tuần tự các comment trong cùng thread để bảo toàn ngữ cảnh. Không
dùng `POST /seed` trong luồng comment: seed đồng bộ dữ liệu nguồn và có thể
xóa dữ liệu của persona không còn trong nguồn.

## Tạo thread

```bash
curl -X POST 'http://127.0.0.1:8000/threads' \
  -H 'Content-Type: application/json' \
  -d '{"persona_id":"AUTO_029","post_id":"post-123","post_text":"Bài viết về Hyundai Creta","post_vehicle_models":["Hyundai Creta"]}'
```

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `persona_id` | string, bắt buộc | ID persona đã tồn tại. |
| `post_id` | string | ID bài viết phía đối tác; mặc định `post-1`. |
| `post_text` | string | Nội dung bài làm ngữ cảnh; mặc định chuỗi rỗng. |
| `post_vehicle_models` | array của string | Các mẫu xe trong bài; mặc định `[]`. |

Ví dụ response rút gọn:

```json
{"thread_id":"thread-id-do-server-tao","persona_id":"AUTO_029","post_id":"post-123","post_text":"Bài viết về Hyundai Creta","post_vehicle_models":["Hyundai Creta"]}
```

Lưu `thread_id` trả về, không tự dùng `post_id` thay cho `thread_id`. Persona
không tồn tại trả HTTP `404`.

## Gửi comment

```bash
curl -X POST 'http://127.0.0.1:8000/turns' \
  -H 'Content-Type: application/json' \
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
| `message_id` | string hoặc null | Được schema chấp nhận nhưng hiện không được dùng để chống trùng; dùng header `Idempotency-Key`. |

`Idempotency-Key` là header tùy chọn nhưng nên truyền. Giữ key ổn định cho cùng
comment, tối đa 128 ký tự; dùng key mới cho comment mới. `actor_id` và
`persona_id` nên không quá 64 ký tự, `post_id` không quá 128 ký tự để phù hợp
chiều dài cột DB. Mỗi tenant cần quản lý namespace ID riêng tại gateway.

Ví dụ response rút gọn khi gửi mô phỏng thành công:

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

Đây chỉ là ví dụ hình dạng; nội dung và trace thực tế phụ thuộc lượt chạy.
Response còn có `planner`, `tool_calls`, `saved_customer_facts`, `saved_stories`
và `flow`; các trường này có thể chứa dữ liệu khách hoặc chi tiết kỹ thuật,
không nên hiển thị công khai cùng `reply_text`.

Pipeline vẫn chạy validator và vòng sửa theo cấu hình, nhưng câu trả lời cuối
được tự gửi qua FakeSender kể cả khi validator còn lỗi hoặc goal là `handoff`.
Khi đó `status` vẫn là `sent` và `validator_errors` có thể không rỗng; lỗi cũng
được ghi trong trace. Đừng hiểu `sent` là dữ kiện đã được xác minh hoàn toàn.

Mỗi lượt mới có `trace.intent_score` gồm `score` (0–100), `method: "rules_v1"`
và `factors` (nhãn, số điểm). Đây là điểm ưu tiên tham khảo theo luật, không
phải xác suất và không thay đổi quyết định trả lời. Quyết định thực tế nằm ở
`trace.gate.action` (`bypass`, `faq`, `pipeline`) cùng `reason`. Điểm được lưu
trong trace của message khách và giữ nguyên khi replay; lượt cũ có thể không có
điểm. `planner.intents[].confidence` là độ tin cậy nhận diện từng intent, không
phải điểm này và có thể dùng giá trị mặc định.

`POST /threads/{thread_id}/turns` là endpoint tương đương cho thread có sẵn;
nếu body có `thread_id` thì phải trùng URL, nếu không trả HTTP `400`.

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
| `sent` | Đã qua FakeSender; đối tác xử lý gửi kênh ngoài một lần theo `message_id`. Kiểm tra thêm `validator_errors` nếu cần audit. |
| `skipped` | Gate quyết định không trả lời; `reply_text` thường là null. |
| `error` | Lượt không hoàn tất; xem `validator_errors` và `trace`, không tự gửi reply chỉ vì có text. |
| `review`, `draft`, `blocked` | Có trong schema/lịch sử; không coi là đã gửi. Luồng hiện tại tự gửi reply thay vì tạo review mới chỉ vì validator hoặc handoff. |

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
- API hiện **không có xác thực/ủy quyền hoặc phân quyền tenant**. Không expose
  trực tiếp ra Internet. Đặt sau gateway có xác thực, TLS, kiểm soát truy cập,
  phân tách tenant và giới hạn tốc độ; chỉ mở các endpoint thực sự cần thiết.
  Bảo vệ endpoint quản trị, seed và lịch sử/trace vì có thể thay đổi hoặc lộ
  dữ liệu hội thoại. Cấu hình logging phù hợp để không làm lộ key hay dữ liệu
  khách.
