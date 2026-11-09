"""JSON Schema (v1) cho các tool model được gọi. Engine validate mọi
tool call bằng jsonschema trước khi thực thi; không parse regex từ
văn bản tự do (§7). Model KHÔNG được tự chọn persona_id/customer_id:
mọi tool đều chạy trong scope binding do server xác minh (§7)."""
from __future__ import annotations

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "plan_turn",
            "description": "Bước planner: liệt kê mọi ý trong lượt, câu hỏi đang chờ, "
            "nhu cầu dữ liệu, mục tiêu và hướng trả lời. Gọi đầu tiên. "
            "Khai thác need: nếu khách đã nêu nhu cầu cụ thể mà chưa được "
            "hỏi cụ thể, followup PHẢI là câu hỏi cụ thể theo nhu cầu đó, "
            "không dùng câu chung chung ('còn muốn hỏi thêm gì không?'); "
            "một câu 'không' cho câu hỏi chung không được chọn goal stop "
            "khi need còn chưa khai thác. TRẦN: khai thác tối đa 1-2 câu "
            "hỏi cụ thể, sau đó DỪNG khai thác (trả lời tự nhiên theo "
            "mạch trò chuyện, không hỏi sâu thêm) — KHÔNG tự đẩy sang "
            "giới thiệu sale + xin SĐT; giới thiệu sale/đại lý CHỈ khi "
            "khách yêu cầu rõ đặt cọc/giữ xe/kết nối đại lý/sale hoặc "
            "đồng ý rõ ràng khi được hỏi. Handoff: goal handoff CHỈ khi "
            "khách yêu cầu rõ đặt cọc/giữ xe/kết nối đại lý/sale; câu "
            "nói muốn mua ('Tôi cũng muốn mua một chiếc như vậy') là "
            "share_need, KHÔNG phải handoff/transaction — giải quyết "
            "mẫu xe từ nhánh trả lời (resolved_reference) hoặc hỏi đúng "
            "1 câu làm rõ mẫu xe. Quy chiếu mẫu xe: bài viết chỉ nhắc một "
            "mẫu xe và khách chưa nhắc mẫu nào khác thì bản/màu khách nhắc "
            "tới đều hiểu là mẫu xe trong bài — không hỏi lại 'mẫu trong "
            "bài hay mẫu khác'. "
            "Từ chối: nếu khách từ chối trả lời một câu hỏi đang "
            "chờ (kể cả câu hỏi xin SĐT), nêu key của câu hỏi đó vào "
            "declined_question_keys — engine sẽ đánh dấu từ chối và cấm "
            "hỏi lại key đó. Đáp án: chỉ nêu key vào "
            "answered_question_keys khi lượt này thực sự trả lời câu hỏi "
            "đó; phản hồi ngắn ('ok', 'ừ') chỉ trả lời được câu hỏi dạng "
            "có/không, không trả lời câu hỏi mở. Kết cục lead: chỉ khi "
            "khách từ chối theo đuổi mua nữa thì lead_outcome='refused', "
            "chuyển hẳn sang chuyện khác không còn ý mua thì "
            "'topic_changed', luồng lead đã kết thúc thì 'ended'; từ chối "
            "riêng việc chia sẻ SĐT thì KHÔNG tính là lead_outcome mà là "
            "declined_question_keys. "
            "intent_score_llm (tùy chọn, 0-100): điểm ưu tiên chú ý do "
            "bạn tự chấm cho lượt này — cùng ý nghĩa với điểm rules của "
            "hệ thống (lượt này có cần persona chú ý/trả lời ngay "
            "không), KHÔNG phải confidence hay xác suất: 0 = xã giao "
            "thuần, 100 = cần trả lời ngay (khách hỏi gấp, muốn mua, "
            "muốn dừng). Chỉ để so sánh với điểm rules, không ảnh hưởng "
            "quyết định nào.",
            "parameters": {
                "type": "object",
                "properties": {
                    "intent_score_llm": {"type": ["number", "null"],
                                         "minimum": 0, "maximum": 100},
                    "intents": {"type": "array", "items": {
                        "type": "object",
                        "properties": {
                            "type": {"enum": ["ask_persona", "ask_product",
                                              "share_need", "third_party",
                                              "correction", "negation",
                                              "opt_out", "transaction",
                                              "social", "ai_identity",
                                              "affirmation"]},
                            "subject": {"enum": ["self", "third_person",
                                                 "persona"]},
                            "relation": {"type": ["string", "null"]},
                            "normalized": {"type": "string"},
                            "evidence": {"type": "string"},
                            "source": {"enum": ["stated", "inferred_context"]},
                            "confidence": {"type": "number"},
                        },
                        "required": ["type", "normalized", "evidence"],
                    }},
                    "pending_questions": {"type": "array", "items": {"type": "string"}},
                    "answered_question_keys": {"type": "array", "items": {"type": "string"}},
                    "declined_question_keys": {"type": "array", "items": {"type": "string"}},
                    "lead_outcome": {"enum": ["refused", "topic_changed", "ended", None]},
                    "known_customer": {"type": "object"},
                    "known_persona": {"type": "object"},
                    "data_needs": {"type": "array", "items": {"type": "string"}},
                    "goal": {"enum": ["answer_question", "share_experience",
                                      "clarify", "explore_need", "empathize",
                                      "stop", "handoff"]},
                    "response_direction": {"type": "object", "properties": {
                        "answer_first": {"type": "boolean"},
                        "ask_followup": {"type": "boolean"},
                        "followup_purpose": {"type": ["string", "null"]},
                        "followup_key": {"type": ["string", "null"]},
                    }},
                    "resolved_reference": {"type": "object"},
                },
                "required": ["intents", "goal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_persona_facts",
            "description": "Tìm facts của persona đang bound theo đúng fact_key. "
            "KHÔNG truyền keys để lấy toàn bộ canon active (khuyến nghị). "
            "Nếu truyền keys, CHỈ dùng đúng tên key trong registry: "
            "vehicle_model, vehicle_trim, purchased_at, is_first_car, "
            "commute_route, weekend_route, vehicle_color, favorite_*, "
            "trip_*, commute_*, weekend_*, experience_*, habit_*, memory_*. "
            "Đừng tự đoán key khác (vehicle, car, purchase_date...): engine "
            "có synonym map nhưng key lạ hoàn toàn sẽ trả NOT_FOUND kèm "
            "gợi ý registry để hỏi lại đúng key. Key đúng tên mà fact "
            "chưa active sẽ trả NOT_FOUND — nghĩa là canon CHƯA CÓ dữ "
            "kiện đó: trả lời tự nhiên ('mình không rõ lắm') hoặc hỏi "
            "lại, TUYỆT ĐỐI không tự chọn giá trị rồi đưa vào reply.",
            "parameters": {
                "type": "object",
                "properties": {"keys": {"type": "array", "items": {"type": "string"}}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_persona_stories",
            "description": "Tìm stories + canonical view của persona đang bound.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "event_id": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_facts",
            "description": "Tìm product facts còn hiệu lực cho đúng mẫu/phiên bản.",
            "parameters": {
                "type": "object",
                "properties": {
                    "model": {"type": "string"},
                    "trim": {"type": ["string", "null"]},
                    "fact_type": {"type": "string"},
                    "location": {"type": ["string", "null"]},
                },
                "required": ["model"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_customer_state",
            "description": "Xem customer facts và trạng thái câu hỏi của thread/actor này.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_customer_fact",
            "description": "Đề xuất lưu một customer fact. Engine kiểm chứng subject, "
            "evidence và supersede bản cũ trước khi lưu. need_kind phân "
            "biệt explicit/preference/concern/constraint/intended_user/"
            "usage_context/hypothesis/declined/opt_out; hypothesis không "
            "được nâng thành fact. certainty: high/medium/low. Khi khách "
            "ĐÍNH CHÍNH một thông tin cũ (VD 'à không, mua cho mình' sau "
            "'mua cho bố'): đặt correction=true để fact cũ cùng predicate "
            "bị supersede dù khác subject/relation; không đặt khi khách "
            "chỉ bổ sung thêm (VD 'mua cho bố và cho mẹ' thì cả hai cùng "
            "tồn tại).",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "relation": {"type": ["string", "null"]},
                    "predicate": {"type": "string"},
                    "value_raw": {"type": "string"},
                    "value_normalized": {"type": ["string", "null"]},
                    "evidence": {"type": "string"},
                    "source": {"type": "string"},
                    "confidence": {"type": "number"},
                    "need_kind": {"type": "string"},
                    "certainty": {"type": "string"},
                    "correction": {"type": "boolean"},
                },
                "required": ["subject", "predicate", "value_raw", "evidence"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_final_reply",
            "description": "Trả lời cuối cùng. Mọi claim có dữ kiện phải kèm ref_id "
            "đã được tool trả về trong lượt này, hoặc từ RAG_EVIDENCE "
            "trong system message (khi đó kind là rag, ref_id là chunk "
            "id), hoặc từ COMPANY_EVIDENCE (khi đó kind là company, "
            "ref_id là chunk id đại lý). Claim value phải là giá trị "
            "chuẩn ngắn gọn copy từ tool result (VD 'Hyundai Creta', "
            "'Trắng'), không viết thành câu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "claims": {"type": "array", "items": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string"},
                            "ref_id": {"type": ["string", "null"]},
                            "value": {"type": "string"},
                        },
                        "required": ["kind", "value"],
                    }},
                    "followup_question": {"type": ["string", "null"]},
                    "followup_key": {"type": ["string", "null"]},
                    "goal": {"type": "string"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_or_insufficient",
            "description": "Dừng lượt hoặc báo chưa đủ dữ liệu (không tự bịa).",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "draft_text": {"type": ["string", "null"]},
                },
                "required": ["reason"],
            },
        },
    },
]

TOOL_SCHEMAS = {
    t["function"]["name"]: t["function"]["parameters"] for t in TOOLS
}
