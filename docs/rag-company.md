# RAG company (đại lý) + thu thập lead

## Nguồn

- Google Sheet "Phân bổ tỉ trọng data", tab "Danh sách đại lý + email +
  vị trí" (gid=0): 59 đại lý, cột Mã ĐL / Tên / Viết tắt / Khu vực /
  Tỉnh thành / Địa chỉ.
- `scripts/fetch_dealers.py` lấy sheet về `data/dealers.json`, đã làm
  sạch: KHÔNG lấy email nhân sự marketing, KHÔNG lấy SĐT cá nhân gắn
  trong ô địa chỉ. Persona không bao giờ được chia sẻ các thông tin đó.
- Tab còn lại ("Phân bổ tháng 10") chỉ là chỉ tiêu/số lượng data phân
  bổ theo đại lý, không có khuyến mãi — không đưa vào RAG.

## Giới hạn quan trọng: sheet KHÔNG có dữ liệu khuyến mãi

Vì vậy persona KHÔNG được nêu con số chiết khấu hay chương trình khuyến
mãi nào. Luồng lead chỉ được nói "nhờ sale tư vấn trực tiếp giá và
chương trình hiện tại". Muốn có câu chiết khấu thì phải có nguồn
khuyến mãi đưa vào chunk company trước.

## Chunking

- Một đại lý = một chunk, id `company_<Mã ĐL>`, `domain='company'`,
  `persona_id=NULL`, text = tên + mã + khu vực + tỉnh thành + địa chỉ.
- Build chung index với persona (`build_rag_index.py --company
  data/dealers.json`) để TF-IDF fit chung vocab, nhưng retrieval tách
  riêng: `retrieve_chunks` lọc `persona_id`, `retrieve_company_chunks`
  lọc `domain='company'` — không bao giờ trộn hai miền.
- Migration 005: thêm cột `domain`, `persona_id` nullable (SQLite
  rebuild bảng, Postgres ALTER).

## Cổng lead

- `has_lead_signal(text)`: true khi khách thể hiện quan tâm mua xe /
  hỏi đại lý / liên hệ. Cạm bẫy: bỏ dấu làm "mưa" thành "mua" nên
  "mua" chỉ khớp trên text gốc có dấu.
- Pipeline mở COMPANY_EVIDENCE khi lượt này HOẶC lượt khách trước
  trong thread có tín hiệu lead; lượt thường không đính kèm.
- Claim đại lý: kind `company`, validator kiểm tra khớp chunk như kind
  rag (`company_unsaved_detail` nếu sai).

## LEAD_CAPTURE (prompt)

Tiến trình từng bước, tối đa 1 câu hỏi/lượt: hỏi mẫu xe quan tâm →
nhắc sale tư vấn trực tiếp (không bịa khuyến mãi) → hỏi khu vực →
nêu đại lý khớp từ COMPANY_EVIDENCE kèm địa chỉ → hỏi nhẹ SĐT/Zalo
để gửi sale add Zalo (khách từ chối thì dừng hẳn, không hỏi lại).
Thông tin khách cho (mẫu xe, khu vực, SĐT) propose qua
propose_customer_fact để hệ thống ghi nhận lead; persona chỉ nói sẽ
chuyển thông tin, không tuyên bố đã gửi xong. Validator cho phép hỏi
SĐT chỉ khi `lead_capture` true; hỏi địa chỉ nhà luôn bị cấm.

## Câu ghi chú "mô phỏng"

Lỗi 2026-11-02: model tự chèn "Đây là chi tiết mô phỏng..." vào reply
thường (do identity_note nằm trong PERSONA_CORE bị model echo lại).
Fix: SYSTEM_PROMPT rule 6 + CONVERSATION_POLICY đều cấm tự chèn ghi
chú mô phỏng; chỉ tiết lộ khi khách hỏi TRỰC TIẾP về danh tính, và
khi đó nói theo identity_note (nhóm 200 là người thật, không gọi là
hư cấu).
