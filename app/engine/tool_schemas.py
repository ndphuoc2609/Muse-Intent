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
            "nhu cầu dữ liệu, mục tiêu và hướng trả lời. Gọi đầu tiên.",
            "parameters": {
                "type": "object",
                "properties": {
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
            "description": "Tìm facts của persona đang bound. Không truyền persona_id.",
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
            "name": "propose_persona_fact",
            "description": "Đề xuất tạo persona fact mới. Engine kiểm tra key có được "
            "phép tạo, không khóa, không mâu thuẫn trước khi lưu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact_key": {"type": "string"},
                    "value": {"type": "string"},
                    "evidence": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["fact_key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_customer_fact",
            "description": "Đề xuất lưu một customer fact. Engine kiểm chứng subject, "
            "evidence và supersede bản cũ trước khi lưu.",
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
                },
                "required": ["subject", "predicate", "value_raw", "evidence"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_story",
            "description": "Đề xuất story hư cấu mới. Engine kiểm tra trùng/mâu thuẫn/"
            "timeline trước khi lưu; chỉ sau khi lưu mới được đưa vào reply.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "body": {"type": "string"},
                    "event_type": {"type": "string"},
                    "event_date": {"type": ["string", "null"]},
                    "event_date_precision": {"type": "string"},
                    "entities": {"type": "object"},
                },
                "required": ["summary", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_addendum",
            "description": "Đề xuất chi tiết bổ sung cho story đã có (không ghi đè).",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "content": {"type": "string"},
                    "entities": {"type": "object"},
                },
                "required": ["event_id", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_final_reply",
            "description": "Trả lời cuối cùng. Mọi claim có dữ kiện phải kèm ref_id "
            "đã được tool trả về trong lượt này.",
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
