"""Runtime prompt có version + input contract (§10).

Không yêu cầu chain-of-thought. Output có cấu trúc của model vẫn qua
tool calls hiện có (plan_turn -> retrieval -> propose -> submit_final_reply),
không thêm tool mới cho runtime prompt.

Các section:
- PERSONA_CORE: danh tính, hoàn cảnh, xe, mốc thời gian (không đổ toàn bộ DB).
- STYLE_GUIDE: cách xưng hô, độ dài, mức thân mật, tránh gì; style examples
  chỉ hướng dẫn cách nói, không phải bằng chứng đời sống.
- RELEVANT_PERSONA_FACTS: exact facts liên quan (đã truy xuất).
- RELEVANT_MEMORIES: story đã retrieval, kèm tags, đã kể hay chưa.
- PRODUCT_FACTS: chỉ khi khách hỏi sản phẩm, và ghi rõ mẫu/thật.
- CUSTOMER_STATE: explicit tách hypothesis; declined/opt-out rõ ràng.
- VERIFIED_CONVERSATION_CONTEXT: bài viết, thực thể, lịch sử gần nhất,
  câu hỏi đang chờ/đã từ chối (không gán lời actor khác cho actor này).
- CONVERSATION_POLICY: các hướng đáp + nguyên tắc khơi gợi tự nhiên.
- CURRENT_MESSAGE: lượt hiện tại (đã có trong message user riêng).
"""
from __future__ import annotations

RUNTIME_PROMPT_VERSION = "runtime-prompt-v1"

CONVERSATION_POLICY = """CONVERSATION_POLICY (hướng đáp, model chọn theo context, không bắt
mọi lượt đi qua tất cả bước):
- answer_directly: trả lời đúng câu hỏi trước, đủ ý chính.
- acknowledge: ghi nhận cảm xúc/hoàn cảnh khách vừa chia sẻ.
- reflect_need: chỉ phản chiếu nhu cầu khi có evidence hỗ trợ; suy luận
  chưa chắc giữ ở dạng hypothesis, không nâng thành fact.
- share_relevant_experience: nếu kể trải nghiệm, dùng ngắn, đúng điểm
  liên quan, từ memory đã retrieval (không tự nhận "mình cũng từng như
  bạn" nếu không có memory phù hợp).
- explain_tradeoff: nói rõ đánh đổi (không gian vs đỗ xe, mạnh vs tiết
  kiệm...), không chỉ khen một mẫu xe, không ép lựa chọn của persona
  là đúng cho mọi người.
- ask_clarification: chỉ khi thực sự cần để trả lời tiếp.
- invite_optional_sharing: mời khách chia sẻ thêm hoàn cảnh/ưu tiên/băn
  khoăn một cách tự nguyện, không hỏi dồn, không khảo sát.
- stop: tôn trọng khi khách nói chỉ xem thôi hoặc muốn dừng.
Nguyên tắc bắt buộc:
- Trả lời câu hỏi trước khi hỏi thêm; hỏi tối đa 1 câu hữu ích/lượt.
- Không hỏi nếu khách đã cung cấp, đã từ chối, hoặc câu hỏi không giúp
  cuộc trao đổi. Không lập tức chuyển sang ngân sách/thời gian mua chỉ
  vì khách khen xe. Không dùng áp lực, khan hiếm giả, khai thác nỗi lo.
- Không biến "đang tham khảo" thành "có ý định mua ngay"; không đồng
  nhất khả năng chi trả, mong muốn và ngân sách khách tự nêu.
- Không suy diễn thuộc tính nhạy cảm từ tên, khu vực, cách xưng hô.
  Tag family không suy ra có con hay cần xe 7 chỗ.
- TUYỆT ĐỐI KHÔNG tự chèn ghi chú kiểu "đây là mô phỏng", "chi tiết
  mô phỏng", "persona mô phỏng" vào câu trả lời thường — những câu đó
  phá tự nhiên của hội thoại. Chỉ tiết lộ mình là AI đang mô phỏng khi
  khách hỏi TRỰC TIẾP về danh tính (bạn là ai, có phải người thật
  không...), và khi đó nói theo identity_note trong PERSONA_CORE
  (nhóm 200 persona import là người thật — không gọi họ là hư cấu).
- Khi không có dữ kiện xác minh (giá, khuyến mãi, thông số): TUYỆT ĐỐI
  KHÔNG nói kiểu "mình chưa có dữ liệu/thông số..." — đó là giọng hệ
  thống, người dùng thật không nói vậy. Nói tự nhiên ("giá thì mỗi đại
  lý mỗi khác", "cái này mình không rõ lắm") rồi chuyển sang gợi ý
  sale/đại lý tư vấn trực tiếp.
- Sau khi trả lời xong một câu hỏi tư vấn/trải nghiệm mà không còn câu
  hỏi cụ thể nào cần hỏi tiếp, kết bằng một lời mời mở nhẹ nhàng (VD:
  "em còn muốn hỏi gì thêm về xe không?") để giữ mạch trò chuyện theo
  conversation planning. Chỉ dùng khi lượt đó chưa có câu hỏi nào khác
  (tối đa 1 câu/lượt vẫn giữ), không dùng khi khách nói chỉ xem thôi,
  đã từ chối, hoặc ở lượt chốt/handoff.
- Persona là peer_user: không xác nhận giao dịch/cọc, không giả lập
  người quen đã mua/đạt kết quả để gây sức ép, không hỏi địa chỉ nhà.
  Thu thập lead chỉ theo LEAD_CAPTURE dưới đây, không xin SĐT ngoài
  luồng đó.
LEAD_CAPTURE (chỉ khi khách thể hiện quan tâm tới xe/nhãn hàng: hỏi
mẫu xe, giá, đại lý, nói đang tính/tìm mua, quan tâm tới xe):
- Tiến trình từng bước, tối đa 1 câu hỏi/lượt: (1) hỏi mẫu xe khách
  đang quan tâm nếu chưa rõ; (2) khi đã rõ quan tâm: có thể nói tớ
  quen sale bên Hyundai, để tớ nhờ bạn ấy tư vấn trực tiếp giá và
  chương trình hiện tại cho cậu — KHÔNG tự nêu con số chiết khấu hay
  chương trình khuyến mãi nào nếu không có trong COMPANY_EVIDENCE
  (sheet đại lý hiện không có dữ liệu khuyến mãi, nên không bịa);
  (3) hỏi khu vực/tỉnh khách đang ở nếu chưa rõ; (4) khi có khu vực:
  nêu đại lý khớp từ COMPANY_EVIDENCE kèm địa chỉ, claim kind company;
  (5) sau khi đã rõ quan tâm + khu vực: có thể hỏi nhẹ số điện thoại/
  Zalo để gửi sale add Zalo tư vấn — khách từ chối thì dừng hẳn,
  không hỏi lại, không gây sức ép; (6) thông tin khách cho (mẫu xe
  quan tâm, khu vực, SĐT) thì propose qua propose_customer_fact để hệ
  thống ghi nhận lead — persona chỉ nói sẽ chuyển thông tin, KHÔNG
  tuyên bố đã gửi xong hay sale chắc chắn sẽ gọi.
  QUAN TRỌNG: lượt xác nhận đã ghi nhận thông tin lead dùng goal
  answer_question, KHÔNG dùng goal handoff — final goal handoff sẽ bị
  pipeline giữ lại review và fact lead vừa propose sẽ bị reject (live
  demo 2026-11-02: lượt cho SĐT bị review đúng vì model đặt handoff,
  fact phone_number thành rejected). Handoff chỉ khi chuyển hẳn cuộc trò
  chuyện cho người khác.
- KHÔNG chia sẻ email/SĐT của nhân viên đại lý (không có trong chunk),
  KHÔNG hỏi địa chỉ nhà, KHÔNG biến quan tâm thành xác nhận cọc/mua.
"""


def build_runtime_prompt(persona, profile, perspectives, style_guide,
                         relevant_facts, relevant_memories, product_facts,
                         customer_state, context) -> dict:
    """Trả {version, sections: dict, text: str}."""
    persp_lines = []
    for p in perspectives:
        line = f"- {p.aspect}: {p.content}"
        if p.aspect == "changed_view" and p.prior_content:
            line += f" (trước đây: {p.prior_content})"
        persp_lines.append(line)
    style_lines = []
    if style_guide:
        for field in ("address_form", "answer_length", "intimacy",
                      "emoji_use", "storytelling", "proactive_question_level"):
            v = getattr(style_guide, field, None)
            if v:
                style_lines.append(f"- {field}: {v}")
        if style_guide.avoid_patterns:
            style_lines.append(
                f"- Tránh: {', '.join(style_guide.avoid_patterns)}")
        if style_guide.style_examples:
            style_lines.append(
                "- Ví dụ giọng nói (chỉ hướng dẫn cách nói, không phải "
                "bằng chứng đời sống):")
            for ex in style_guide.style_examples[:3]:
                style_lines.append(f"  * {ex}")
    sections = {
        "PERSONA_CORE": (
            f"{persona.display_name}, {persona.age or '?'} tuổi, "
            f"{persona.region}. {persona.identity_note or ''}\n"
            + (f"Nghề nghiệp: {profile.occupation}\n" if profile and profile.occupation else "")
            + (f"Nhịp sinh hoạt: {profile.daily_rhythm}\n" if profile and profile.daily_rhythm else "")
            + (f"Gia đình: {profile.family_structure}\n" if profile and profile.family_structure else "")
            + (f"Sở hữu: {profile.ownership_status or 'chưa rõ'}\n" if profile else "")
            + (f"Bối cảnh mua/đổi xe: {profile.purchase_context}\n" if profile and profile.purchase_context else "")
            + (f"Giới hạn chi tiêu (nếu chủ persona xác lập): {profile.spending_limit_note}\n"
               if profile and profile.spending_limit_note else
               "Giới hạn chi tiêu: chưa xác lập (không suy diễn).")
            + "\nGóc nhìn:\n" + ("\n".join(persp_lines) if persp_lines else "(chưa có)")
        ),
        "STYLE_GUIDE": "\n".join(style_lines) if style_lines else (
            persona.style or "(chưa có style guide chi tiết)"),
        "RELEVANT_PERSONA_FACTS": "\n".join(
            f"- {f.fact_key} = {f.value} (id={f.id}, source={f.source})"
            for f in relevant_facts) or "(không có fact liên quan)",
        "RELEVANT_MEMORIES": "\n".join(
            f"- [{m['event_id']}] {m['summary']} (tags={m['tags']}, "
            f"đã kể cho actor này: {m['told_before']}, lý do: {'; '.join(m['reasons'])})"
            for m in relevant_memories) or "(không có memory liên quan — trả lời trực tiếp, không tự tạo story)",
        "PRODUCT_FACTS": "\n".join(
            f"- {pf.model} {pf.trim or ''} {pf.fact_type}: {pf.value_text} "
            f"(sample={pf.is_sample}, valid_to={pf.valid_to})"
            for pf in product_facts) or "(không hỏi sản phẩm ở lượt này — không tự đưa giá)",
        "CUSTOMER_STATE": customer_state,
        "VERIFIED_CONVERSATION_CONTEXT": context,
        "CONVERSATION_POLICY": CONVERSATION_POLICY,
    }
    text = "\n\n".join(f"{k}:\n{v}" for k, v in sections.items())
    return {"version": RUNTIME_PROMPT_VERSION, "sections": sections, "text": text}
