"""Hồi quy cho lỗi live Luna 30/10 (trace do Phước gửi).

Luna hỏi "Bác đang đi xe gì và mua lúc nào?" và gọi get_persona_facts
với keys đoán theo ngôn ngữ tự nhiên:
  vehicle, car, current_vehicle, vehicle_purchase_date, purchase_date,
  car_model, xe_dang_di, thoi_diem_mua_xe
Không key nào trùng registry thật (vehicle_model, purchased_at) nên
engine cũ trả NOT_FOUND rỗng -> Luna kết luận "không có dữ kiện" và
gọi stop_or_insufficient, dù fact đã seed sẵn. Fake không bao giờ gặp
lỗi này vì Fake gọi get_persona_facts không truyền keys.

Fix (engine-first, không chỉ sửa prompt):
- synonym map: key đoán -> key registry.
- fallback: nếu toàn bộ key yêu cầu đều lạ ngoài registry mà canon còn
  fact active thì trả toàn bộ canon kèm reason rõ ràng, thay vì [].
- giữ NOT_FOUND khi model hỏi đúng tên key trong registry nhưng fact
  chưa active (VD: vehicle_color chưa propose) — để đường propose và
  test pending-isolation vẫn giữ nguyên ý nghĩa.
"""
import json

from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.engine.tools import ToolExecutor
from app.providers.base import ProviderResult, ToolCall
from app.schemas import TurnRequest

# Đúng key list Luna đã gửi trong trace live 30/10.
LUNA_GUESSED_KEYS = [
    "vehicle", "car", "current_vehicle", "vehicle_purchase_date",
    "purchase_date", "car_model", "xe_dang_di", "thoi_diem_mua_xe",
]


def test_luna_guessed_keys_return_seeded_facts(pipe, session):
    ex = ToolExecutor(session, "hoang-nam", "t", "actor-1")
    r = ex.execute("get_persona_facts", {"keys": LUNA_GUESSED_KEYS})
    assert r.status == "FOUND"
    by_key = {f["fact_key"]: f["value"] for f in r.data}
    assert by_key.get("vehicle_model") == "Hyundai Creta"
    assert by_key.get("purchased_at") == "2024-03"


def test_unknown_only_keys_fallback_to_all_canon(pipe, session):
    ex = ToolExecutor(session, "hoang-nam", "t", "actor-1")
    r = ex.execute("get_persona_facts", {"keys": ["foo_bar_xyz"]})
    assert r.status == "FOUND"
    assert "keys_unmatched_fallback_all" in (r.reason or "")
    assert any(f["fact_key"] == "vehicle_model" for f in r.data)


def test_known_but_inactive_key_stays_not_found(pipe, session):
    # vehicle_color là key đúng registry nhưng chưa active trong seed mới
    # (màu chỉ xuất hiện qua propose) -> NOT_FOUND, không fallback.
    ex = ToolExecutor(session, "hoang-nam", "t", "actor-1")
    r = ex.execute("get_persona_facts", {"keys": ["vehicle_color"]})
    assert r.status == "NOT_FOUND"
    assert r.data == []


class GuessingProvider:
    """Giả lập đúng hành vi Luna trong trace live: plan -> get facts bằng
    key đoán -> submit final với claim lấy từ fact tool trả về."""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return ProviderResult(tool_calls=[ToolCall("plan_turn", {
                "intents": [
                    {"type": "ask_persona", "subject": "persona",
                     "relation": "xe đang đi",
                     "normalized": "Hỏi persona đang đi xe gì",
                     "evidence": "Bác đang đi xe gì", "source": "stated",
                     "confidence": 1},
                    {"type": "ask_persona", "subject": "persona",
                     "relation": "thời điểm mua xe",
                     "normalized": "Hỏi thời điểm persona mua xe",
                     "evidence": "mua lúc nào", "source": "stated",
                     "confidence": 1}],
                "pending_questions": [], "answered_question_keys": [],
                "known_customer": {}, "known_persona": {},
                "data_needs": ["persona_facts"],
                "goal": "answer_question",
                "response_direction": {"answer_first": True,
                                       "ask_followup": False}})])  # noqa: E501
        if self.calls == 2:
            return ProviderResult(tool_calls=[ToolCall("get_persona_facts", {
                "keys": LUNA_GUESSED_KEYS})])
        facts = {}
        for m in messages:
            if m.get("role") == "tool":
                try:
                    data = json.loads(m.get("content", "{}"))
                except Exception:
                    continue
                for f in data.get("data", []) or []:
                    if isinstance(f, dict):
                        facts[f.get("fact_key")] = f
        claims = []
        if "vehicle_model" in facts:
            claims.append({"kind": "persona",
                           "ref_id": facts["vehicle_model"]["id"],
                           "value": "Hyundai Creta"})
        if "purchased_at" in facts:
            claims.append({"kind": "persona",
                           "ref_id": facts["purchased_at"]["id"],
                           "value": "2024-03"})
        return ProviderResult(tool_calls=[ToolCall("submit_final_reply", {
            "text": "Mình đang đi Hyundai Creta 1.5 Cao cấp, mua từ tháng 03/2024.",
            "claims": claims, "goal": "answer_question"})])


def test_guessing_provider_turn_now_sends(session):
    p = Pipeline(session, GuessingProvider(), FakeSender())
    r = p.run(TurnRequest(
        persona_id="hoang-nam", post_text="Bài về Creta.",
        post_vehicle_models=["Hyundai Creta"],
        actor_id="actor-1", text="Bác đang đi xe gì và mua lúc nào?"))
    assert r.status == "sent", f"validator_errors={r.validator_errors}"
    assert "Creta" in (r.reply_text or "")
    assert "03/2024" in (r.reply_text or "")
