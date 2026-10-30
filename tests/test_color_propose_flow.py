"""Luồng màu xe: chưa có trong canon -> propose -> sent -> active -> lượt
sau trả cùng màu từ canon, không propose lại. Có màu khác trong canon rồi
-> propose màu mới bị CONFLICT, không ghi đè.

Đây là yêu cầu của Phước 30/10 sau lượt màu xe bị review: "tự update db
nếu không conflict thông tin cũ và ghi nhớ để lần tiếp theo có người hỏi
thì trả lời". Engine đã làm đúng từ trước (pending -> active khi sent);
phần hỏng ở live là Luna từ chối propose vì prompt cũ cấm "bịa màu xe"
trong khi propose chính là luồng tạo chi tiết hư cấu. Test này khóa
luồng engine + prompt mới (SYSTEM_PROMPT quy định màu mặc định trắng
khi chưa có, và propose trước khi trả lời).
"""
import json

from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.engine.tools import ToolExecutor
from app.models import PersonaFact
from app.providers.base import ProviderResult, ToolCall
from app.providers.openrouter import SYSTEM_PROMPT
from app.schemas import TurnRequest


def test_system_prompt_resolves_fiction_vs_fabrication():
    # Prompt mới phải nói rõ: tạo chi tiết hư cấu qua propose KHÔNG phải
    # bịa dữ kiện thật, và màu xe chưa có thì propose (mặc định trắng).
    assert "propose_persona_fact" in SYSTEM_PROMPT
    assert "vehicle_color" in SYSTEM_PROMPT
    assert "trắng" in SYSTEM_PROMPT
    # Không còn câu cấm tuyệt đối "không bịa ... màu xe" như prompt cũ.
    assert "không bịa giá, thông số, màu xe, câu chuyện" not in SYSTEM_PROMPT


class ColorProposingProvider:
    """Giả lập Luna sau fix: lượt 1 propose màu rồi trả lời; lượt sau
    (canon đã có màu) chỉ đọc canon, không propose lại. Quyết định dựa
    trên tool đã gọi (như Fake), không đếm số lần gọi."""

    def _tool_data(self, messages, tool):
        for m in messages:
            if m.get("role") == "tool":
                try:
                    data = json.loads(m.get("content", "{}"))
                except Exception:
                    continue
                if data.get("tool") == tool:
                    return data
        return {}

    def _called(self, messages):
        called = set()
        for m in messages:
            if m.get("role") == "tool":
                try:
                    called.add(json.loads(m.get("content", "{}")).get("tool"))
                except Exception:
                    continue
        return called

    def chat(self, messages, tools):
        called = self._called(messages)
        if "plan_turn" not in called:
            return ProviderResult(tool_calls=[ToolCall("plan_turn", {
                "intents": [{"type": "ask_persona", "subject": "persona",
                             "relation": "màu xe",
                             "normalized": "Hỏi màu xe của persona",
                             "evidence": "Bác đi xe màu gì thế?",
                             "source": "stated", "confidence": 1}],
                "pending_questions": [], "answered_question_keys": [],
                "known_customer": {}, "known_persona": {},
                "data_needs": ["persona_facts"],
                "goal": "answer_question",
                "response_direction": {"answer_first": True,
                                       "ask_followup": False}})])
        if "get_persona_facts" not in called:
            return ProviderResult(tool_calls=[ToolCall("get_persona_facts", {})])
        facts = self._tool_data(messages, "get_persona_facts")
        for f in facts.get("data", []) or []:
            if isinstance(f, dict) and f.get("fact_key") == "vehicle_color":
                return ProviderResult(tool_calls=[ToolCall("submit_final_reply", {
                    "text": "Xe mình màu trắng đó bạn.",
                    "claims": [{"kind": "persona", "ref_id": f["id"],
                                "value": "trắng"}],
                    "goal": "answer_question"})])
        if "propose_persona_fact" not in called:
            return ProviderResult(tool_calls=[ToolCall("propose_persona_fact", {
                "fact_key": "vehicle_color", "value": "trắng",
                "evidence": "khách hỏi màu xe, canon chưa có",
                "source": "generated"})])
        proposed = self._tool_data(messages, "propose_persona_fact")
        fid = (proposed.get("data") or {}).get("id")
        return ProviderResult(tool_calls=[ToolCall("submit_final_reply", {
            "text": "Xe mình màu trắng đó bạn.",
            "claims": [{"kind": "persona", "ref_id": fid, "value": "trắng"}],
            "goal": "answer_question"})])


def test_color_propose_then_remembered(session):
    pipe = Pipeline(session, ColorProposingProvider(), FakeSender())
    r1 = pipe.run(TurnRequest(
        persona_id="hoang-nam", post_text="Bài về Creta.",
        post_vehicle_models=["Hyundai Creta"],
        actor_id="actor-1", text="Bác đi xe màu gì thế?"))
    assert r1.status == "sent", f"validator_errors={r1.validator_errors}"
    assert "trắng" in (r1.reply_text or "")
    # Đã active trong DB sau sent (không còn pending).
    row = session.query(PersonaFact).filter_by(
        persona_id="hoang-nam", fact_key="vehicle_color",
        status="active").first()
    assert row is not None and row.value == "trắng"
    # Lượt 2: cùng thread, hỏi lại -> trả cùng màu từ canon, không tạo
    # thêm fact, không propose lại.
    pipe2 = Pipeline(session, ColorProposingProvider(), FakeSender())
    r2 = pipe2.run(TurnRequest(
        persona_id="hoang-nam", post_text="Bài về Creta.",
        post_vehicle_models=["Hyundai Creta"],
        actor_id="actor-1", text="Bác đi xe màu gì thế?",
        thread_id=r1.thread_id))
    assert r2.status == "sent"
    assert "trắng" in (r2.reply_text or "")
    rows = session.query(PersonaFact).filter_by(
        persona_id="hoang-nam", fact_key="vehicle_color").all()
    assert len(rows) == 1


def test_color_conflict_does_not_overwrite(session):
    # Canon đã có trắng -> propose đen phải CONFLICT, giá trị cũ giữ nguyên.
    # (Fixture session đã seed sẵn Hoàng Nam.)
    pipe = Pipeline(session, ColorProposingProvider(), FakeSender())
    r1 = pipe.run(TurnRequest(
        persona_id="hoang-nam", post_text="Bài về Creta.",
        post_vehicle_models=["Hyundai Creta"],
        actor_id="actor-1", text="Bác đi xe màu gì thế?"))
    assert r1.status == "sent"
    ex = ToolExecutor(session, "hoang-nam", r1.thread_id, "actor-1")
    res = ex.execute("propose_persona_fact",
                     {"fact_key": "vehicle_color", "value": "đen",
                      "source": "generated"})
    assert res.status == "CONFLICT"
    row = session.query(PersonaFact).filter_by(
        persona_id="hoang-nam", fact_key="vehicle_color",
        status="active").first()
    assert row.value == "trắng"
