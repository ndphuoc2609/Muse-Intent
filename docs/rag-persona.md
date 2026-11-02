# RAG persona-only (chạy trước, chưa có dữ liệu công ty)

Trạng thái: đã build + kiểm chứng live bằng Luna thật (2026-11-02).
Dữ liệu công ty CHƯA có — khi Phước xin được tài liệu, RAG mở rộng
thêm domain company theo đúng mẫu dưới đây, không sửa kiến trúc.

## 1. Persona -> RAG: chunking như thế nào

Nguồn duy nhất hiện tại: `data/personas.json` (200 persona). Một persona
thành nhiều chunk, mỗi chunk một loại dữ kiện, id ổn định để build lại
là idempotent. Tổng: **2059 chunk** (memory 851, perspective 200,
profile 200, post 808).

| Loại chunk | id | text |
|---|---|---|
| memory | `{code}_rag_mem_{i}` | memory nguyên văn |
| perspective | `{code}_rag_perspective` | perspective nguyên văn |
| profile | `{code}_rag_profile` | câu ghép có nhãn từ field profile: `Nghề nghiệp: … Nhịp sinh hoạt: … Gia đình: … Sở thích: … Điểm đau: …` (chỉ field có giá trị, không bịa thêm) |
| post | `{code}_rag_post_{post_id}` | `Chủ đề: {topic}. {hook}` |

Id dùng gạch dưới, KHÔNG dùng tiền tố `rag:` có dấu hai chấm, và KHÁC
story event_id (`{code}_mem_{i}`): live test Luna cho thấy model tự lược
tiền tố `rag:` khi claim, và dùng lẫn id story với kind rag khi cùng
một text xuất hiện ở cả hai kênh (xem §4).

KHÔNG chunk: `role`, `personality_traits`, hashtag tags, category nguồn
(5 nhóm), `multi_angle_ready`. Role/traits là nhãn archetype, không phải
lời persona nói — đưa vào prompt sẽ khiến model diễn nhãn thay vì nói
chuyện tự nhiên, trái quyết định no-role. Hashtag/category là metadata
phân loại, không phải nội dung kể được.

Memory VẪN có chunk RAG riêng dù story path đã có nó: story path chấm
điểm keyword/entity/tag, RAG chấm điểm vector — hai kênh bổ sung nhau.
Dedupe ở §4 xử lý trùng lặp.

## 2. Embedding: TF-IDF v1 (và vì sao chưa phải neural)

Provider mặc định: **TF-IDF local** (`app/engine/embeddings.py`).

- Term: từ (đã bỏ dấu, lowercase) + char 4-gram (tiền tố `c:`) để truy
  vấn không dấu / gõ sai một ký tự vẫn khớp (`ngap` gặp `ngập`).
- Trọng số: nhị phân (có mặt = 1) × IDF `log((N+1)/(df+1)) + 1`,
  L2-chuẩn hoá. Từ lạ không có trong IDF thì bị bỏ qua.
- Vocab đo được trên corpus này: **3012 term** (corpus rất templated:
  chỉ 7 topic chính nên vocab nhỏ). Build toàn bộ 2059 chunk: **0.7s,
  0 phí, không mạng**.

Vì sao chưa neural (nói rõ, không giấu):

- OpenRouter embeddings: probe ngày 2026-11-02 trả HTTP 404
  `model-ignored-by-guardrail` — workspace guardrail của key hiện tại
  chặn endpoint embeddings. Muốn dùng neural qua API thì mở guardrail
  cho embedding model rồi thêm provider thứ ba theo interface
  `embed(texts) -> list[vector]` — không sửa retrieval.
- Neural local (sentence-transformers, paraphrase-multilingual): code
  đã có (`SentenceTransformerEmbedder`, lazy-load để server/test không
  trả phí import torch), cài đặt trong `requirements-rag.txt` (không nằm
  trong requirements chính vì nặng). Cài trong VM ngày 2026-11-02 chưa
  xong trong giới hạn 280s (gói torch lớn) nên v1 chạy TF-IDF; việc cài
  đang chạy nền, xong sẽ build thử `--provider st` để so sánh.

Giới hạn thật của TF-IDF: truy vấn trùng từ khóa thì khớp mạnh, nhưng
diễn đạt khác từ hoàn toàn (paraphrase thuần túy, đồng nghĩa) thì
không bắt được. Đó là giới hạn của provider, không phải của kiến trúc:
đổi provider chỉ cần build lại index (query và chunk phải cùng một
không gian vector — meta ghi rõ provider, pipeline đọc meta để dùng
đúng embedder).

## 3. Lưu trữ và truy xuất

- Bảng `rag_chunks` (id, persona_id FK, chunk_type, source_ref, text,
  embedding JSON) + `rag_index_meta` (provider, model, threshold,
  chunk_count, built_at, source, state của embedder — vd idf của TF-IDF
  để lúc query không cần fit lại). Migration 004 (SQLite + Postgres).
- Truy xuất: brute-force cosine trên toàn bộ chunk của ĐÚNG persona đó
  (lọc `persona_id` trước — không bao giờ lấy chunk của persona khác),
  giữ score >= threshold, tối đa 3, xếp theo score giảm dần. Không dùng
  pgvector/sqlite-vec ở v1: 2059 chunk thì brute-force là đủ nhanh
  (vài ms); khi corpus lên ~10–50k chunk thì chuyển sang pgvector, giữ
  nguyên interface chunk/query.
- Build: `.venv/bin/python scripts/build_rag_index.py [--provider
  tfidf|st] [--threshold X] [--db ...]`. Script seed() trước (chunk có
  FK tới personas.id), rồi thay toàn bộ index. Build lại sau mỗi lần
  đổi seed/dữ liệu, nếu không index sẽ lệch khỏi canon.

## 4. Luồng trong một lượt chat

1. Model plan_turn như thường.
2. Engine chạy story retrieval (RELEVANT_MEMORIES) như cũ.
3. Engine chạy RAG retrieval: embed câu khách vừa nói (TF-IDF: nạp idf
   từ meta, rẻ; neural: model singleton, lượt đầu trả phí load),
   lấy top-3 chunk của đúng persona này >= threshold.
4. **Dedupe**: chunk memory nào mà story path đã lấy (so theo
   `source_ref` = story event_id) thì loại khỏi RAG_EVIDENCE. Lý do từ
   live test: cùng một text ở hai kênh với hai id làm model lẫn kind
   với ref_id (claim kind rag nhưng ref_id story) -> validator báo
   kind_mismatch -> review. RAG chỉ bổ sung chunk story path chưa lấy
   được + perspective/profile/post.
5. System message nhận thêm section RAG_EVIDENCE
   (`[chunk_id] text (score=…)` + dặn copy ref_id nguyên văn). Model
   muốn dùng dữ kiện nào thì claim **kind rag** với ref_id đó.
6. Validator là cửa cuối: kind rag phải có ref_id trong retrieved
   (`unsourced_claim`), kind phải khớp (`claim_kind_mismatch`), value
   phải khớp chunk theo token overlap >= 0.5 (`rag_unsaved_detail`).
   Lỗi RAG ở bước truy xuất (không embed được, không có index) không
   chặn lượt — ghi vào trace (`rag_error`) để thấy, không nuốt lặng.

## 5. Ngưỡng: đo thực tế, không đoán

Query mẫu trên AUTO_029 (TF-IDF): chunk liên quan đạt 0.118–0.276,
nhiễu ≤ 0.095. Ngưỡng mặc định TF-IDF = **0.10** (tách được hai nhóm
với biên mỏng — vì vậy pipeline chỉ lấy tối đa 3 chunk và validator
vẫn là cửa cuối). Neural có phân phối cosine cao hơn hẳn nên ngưỡng
riêng 0.35 (chưa hiệu chuẩn live — khi build `--provider st` phải đo
lại ngưỡng trước khi dùng).

## 6. Kiểm chứng live (Luna thật, FakeSender, 2026-11-02)

Câu: “Trời mưa to đường ngập thì bác xử lý sao?” (persona AUTO_029).

- Story path lấy `AUTO_029_mem_3`, `AUTO_029_mem_6`.
- RAG lấy post `AUTO_029_rag_post_POST_479481` (0.1844); mem_3 bị dedupe
  vì story đã lấy.
- Luna trả lời dùng dữ kiện RAG, claim kind rag + ref_id nguyên văn,
  validator 0 lỗi, **status sent**, cost $0.000846, 2 LLM calls.
- Trước đó, hai lần chạy đã bắt được hai bug thật và đã sửa: (1) id
  dạng `rag:…` bị model lược tiền tố -> đổi sang id gạch dưới;
  (2) trùng text hai kênh làm model lẫn kind/ref_id -> dedupe ở §4.

## 7. Chưa làm / giới hạn

- Dữ liệu công ty: chưa có tài liệu. Khi có, thêm domain company với
  chunk_type riêng + tách biệt persona/company trong retrieval (không
  trộn hai domain vào một prompt nếu không ghi rõ nguồn).
- Product facts KHÔNG nằm trong RAG: vẫn tra cứu exact-match như cũ
  (giá thì không được “gần đúng”).
- Persona data cho RAG vẫn là 200 persona import (xem seed-200-personas);
  nhãn là NGƯỜI THẬT theo quyết định của Phước 2026-11-02 (ghi đè giả
  định hư cấu ban đầu). Dấu vết template trong text (7 topic, 7 bộ
  lifestyle, post cùng ngày) chứng minh text sinh theo mẫu, không
  chứng minh người là giả — xem seed.py.
