"""Hồi quy cho lỗi live 30/10: persona Minh Anh lái Tucson, kể chuyện
Tucson đi Vũng Tàu, khách nói "Tôi cũng tính lấy 1 chiếc" nhưng reply
lại khẳng định khách "đang cân nhắc Creta".

Cơ chế (đã xác minh trong code, không đoán): resolved_reference do model
đưa ra trong plan_turn được pipeline chấp nhận nguyên văn; model (và cả
Fake) resolve tham chiếu mơ hồ ("1 chiếc", "con này", "như vậy") theo
XE TRONG BÀI VIẾT, trong khi bài viết mặc định là Creta còn persona lái
Tucson và vừa kể chuyện Tucson. Không có lớp nào kiểm tra resolved_to
với canon vehicle_model hay lịch sử gần nhất.

Fix: engine guard trong pipeline (chạy sau plan, trước khi trả plan tool
result để model thấy bản đã sửa) + prompt cấm khẳng định tên xe khi
resolved_reference đã thành ambiguous.
"""
import json

from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.engine.planner_normalize import guard_resolved_reference
from app.providers.base import ProviderResult, ToolCall
from app.schemas import TurnRequest


def _planner(resolved_to, confidence=0.7):
    return {"resolved_reference": {
        "text": "1 chiếc", "resolved_to": resolved_to,
        "source": "inferred_context", "confidence": confidence}}


def test_guard_downgrades_post_car_when_persona_story_just_told():
    out = guard_resolved_reference(
        _planner("Hyundai Creta"), "Hyundai Tucson",
        "Tôi cũng tính lấy 1 chiếc",
        "Nhà mình từng lái Tucson từ Quận 7 đi Vũng Tàu cuối tuần.")
    ref = out["resolved_reference"]
    assert ref["resolved_to"] is None
    assert ref["confidence"] <= 0.4
    assert "Hyundai Tucson" in ref["ambiguous_models"]
    assert "Hyundai Creta" in ref["ambiguous_models"]
    assert "guard" in ref


def test_guard_keeps_explicit_model_name():
    out = guard_resolved_reference(
        _planner("Hyundai Creta"), "Hyundai Tucson",
        "Tôi cũng tính lấy 1 chiếc Creta",
        "Nhà mình từng lái Tucson từ Quận 7 đi Vũng Tàu cuối tuần.")
    assert out["resolved_reference"]["resolved_to"] == "Hyundai Creta"


def test_guard_keeps_persona_vehicle():
    out = guard_resolved_reference(
        _planner("Hyundai Tucson"), "Hyundai Tucson",
        "Tôi cũng tính lấy 1 chiếc",
        "Nhà mình từng lái Tucson từ Quận 7 đi Vũng Tàu cuối tuần.")
    assert out["resolved_reference"]["resolved_to"] == "Hyundai Tucson"


def test_guard_keeps_post_car_without_recent_persona_mention():
    # Không có lượt persona gần nhất nhắc xe persona -> neo bài viết vẫn
    # đứng được (VD: lượt đầu tiên, khách hỏi giá "con này" trong bài).
    out = guard_resolved_reference(
        _planner("Hyundai Creta"), "Hyundai Tucson",
        "Tôi cũng tính lấy 1 chiếc", "Chào bạn, mình là Minh Anh.")
    assert out["resolved_reference"]["resolved_to"] == "Hyundai Creta"


def test_guard_noop_without_persona_vehicle_or_resolution():
    assert guard_resolved_reference(
        _planner(None), "Hyundai Tucson", "Tôi cũng tính lấy 1 chiếc",
        "Nhà mình từng lái Tucson.")["resolved_reference"]["resolved_to"] is None
    assert guard_resolved_reference(
        _planner("Hyundai Creta"), None, "Tôi cũng tính lấy 1 chiếc",
        "Nhà mình từng lái Tucson.")["resolved_reference"]["resolved_to"] == "Hyundai Creta"


def test_guard_suffix_is_part_of_car_name():
    # Hậu tố là 1 phần của tên xe: Tucson Turbo vẫn là họ Tucson, nên
    # resolve thành Creta sau khi persona nhắc Tucson Turbo phải downgrade,
    # và ambiguous_models giữ tên đầy đủ kèm hậu tố.
    out = guard_resolved_reference(
        _planner("Hyundai Creta"), "Hyundai Tucson Turbo",
        "Tôi cũng tính lấy 1 chiếc",
        "Nhà mình từng lái Tucson Turbo đi xa cuối tuần.")
    ref = out["resolved_reference"]
    assert ref["resolved_to"] is None
    assert "Hyundai Tucson Turbo" in ref["ambiguous_models"]
    assert "Hyundai Creta" in ref["ambiguous_models"]


def test_guard_suffix_only_mention_counts():
    # Lượt persona chỉ nhắc hậu tố 'Turbo' vẫn tính là nhắc xe của mình.
    out = guard_resolved_reference(
        _planner("Hyundai Creta"), "Hyundai Tucson Turbo",
        "Tôi cũng tính lấy 1 chiếc", "Bản Turbo đi êm lắm.")
    assert out["resolved_reference"]["resolved_to"] is None


def test_guard_same_family_with_suffix_keeps():
    # Resolve đúng họ xe (Tucson) dù persona đi bản Turbo -> giữ nguyên.
    out = guard_resolved_reference(
        _planner("Hyundai Tucson"), "Hyundai Tucson Turbo",
        "Tôi cũng tính lấy 1 chiếc",
        "Nhà mình từng lái Tucson Turbo đi xa.")
    assert out["resolved_reference"]["resolved_to"] == "Hyundai Tucson"


def test_guard_different_variants_are_different_cars():
    # VF3 vàng nóc trắng vs VF8: chỉ chung hãng -> khác xe -> downgrade.
    out = guard_resolved_reference(
        _planner("VinFast VF8"), "VinFast VF3 vàng nóc trắng",
        "Tôi cũng tính lấy 1 chiếc",
        "Nhà mình đi VF3 vàng nóc trắng đi phố.")
    assert out["resolved_reference"]["resolved_to"] is None


def test_guard_user_naming_suffix_keeps():
    # Khách tự nêu hậu tố ('1 chiếc Turbo') thì không downgrade.
    out = guard_resolved_reference(
        _planner("Hyundai Tucson Turbo"), "Hyundai Creta",
        "Tôi cũng tính lấy 1 chiếc Turbo",
        "Nhà mình từng lái Creta đi phố.")
    assert out["resolved_reference"]["resolved_to"] == "Hyundai Tucson Turbo"


_MEM3 = ("Quan điểm về 'Trời mưa to, đường ngập lụt cục bộ: Rủi ro chết "
         "máy, ngập nước và kinh nghiệm di chuyển an toàn': Với tôi, "
         "chiếc Tucson chở cả gia đình nên gặp đường ngập thì ưu tiên "
         "tìm đường khác hoặc dừng ở nơi an toàn, không đánh cược chỉ "
         "để tiết kiệm vài phút.")


class _StoryProvider:
    """Lượt 1: kể memory story của AUTO_013 (Hyundai Tucson Turbo) —
    tương đương story Vũng Tàu của Minh Anh trong seed cũ: persona vừa
    nhắc xe Tucson thì lượt sau khách nói mơ hồ "1 chiếc" không được
    gán Creta. Dùng đúng xe có hậu tố để khóa fix guard suffix (trước
    fix này, token từ cuối 'turbo' làm guard không kích hoạt)."""

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
                             "relation": "chuyến đi",
                             "normalized": "Hỏi persona đã đi những đâu",
                             "evidence": "Đi những đâu rồi", "source": "stated",
                             "confidence": 1}],
                "pending_questions": [], "answered_question_keys": [],
                "known_customer": {}, "known_persona": {},
                "data_needs": ["persona_stories"],
                "goal": "share_experience",
                "response_direction": {"answer_first": True,
                                       "ask_followup": False}})])
        if "get_persona_stories" not in called:
            return ProviderResult(tool_calls=[ToolCall("get_persona_stories", {})])
        return ProviderResult(tool_calls=[ToolCall("submit_final_reply", {
            "text": _MEM3,
            "claims": [{"kind": "story", "ref_id": "AUTO_013_mem_3",
                        "value": _MEM3}],
            "goal": "share_experience"})])


class _VagueBuyProvider:
    """Lượt 2: plan resolve '1 chiếc' thành Creta như model live đã làm;
    sau đó đọc plan tool result: nếu engine đã downgrade thành ambiguous
    thì trả lời trung tính không nêu tên xe, ngược lại (guard hỏng) thì
    vẫn khẳng định Creta để test fail rõ ràng."""

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

    def chat(self, messages, tools):
        plan = self._tool_data(messages, "plan_turn")
        if not plan:
            return ProviderResult(tool_calls=[ToolCall("plan_turn", {
                "intents": [{"type": "share_need", "subject": "self",
                             "relation": "mua xe",
                             "normalized": "Khách tính lấy 1 chiếc",
                             "evidence": "Tôi cũng tính lấy 1 chiếc",
                             "source": "stated", "confidence": 1}],
                "pending_questions": [], "answered_question_keys": [],
                "known_customer": {}, "known_persona": {},
                "data_needs": ["persona_facts"],
                "goal": "explore_need",
                "response_direction": {"answer_first": True,
                                       "ask_followup": True,
                                       "followup_purpose": "hiểu điểm khách thích",
                                       "followup_key": "like_point"},
                "resolved_reference": {"text": "1 chiếc",
                                       "resolved_to": "Hyundai Creta",
                                       "source": "inferred_context",
                                       "confidence": 0.7}})])
        resolved_to = (plan.get("data") or {}).get(
            "resolved_reference", {}).get("resolved_to")
        if resolved_to is None:
            text = ("Bạn đang tính lấy 1 chiếc à, bạn thích điểm nào nhất "
                    "ở chiếc xe đó?")
        else:
            text = "Nghe như bạn đang cân nhắc Creta rồi nhỉ."
        return ProviderResult(tool_calls=[ToolCall("submit_final_reply", {
            "text": text, "claims": [], "goal": "explore_need"})])


def test_two_turn_tucson_story_then_vague_buy_no_creta_assertion(session):
    # Setup đúng như live: persona Tucson Turbo (AUTO_013), bài Creta.
    pipe1 = Pipeline(session, _StoryProvider(), FakeSender())
    r1 = pipe1.run(TurnRequest(
        persona_id="AUTO_013", post_text="Bài về Creta.",
        post_vehicle_models=["Hyundai Creta"],
        actor_id="actor-1", text="Đi những đâu rồi"))
    assert r1.status == "sent"
    assert "Tucson" in (r1.reply_text or "")
    pipe2 = Pipeline(session, _VagueBuyProvider(), FakeSender())
    r2 = pipe2.run(TurnRequest(
        persona_id="AUTO_013", post_text="Bài về Creta.",
        post_vehicle_models=["Hyundai Creta"],
        actor_id="actor-1", text="Tôi cũng tính lấy 1 chiếc",
        thread_id=r1.thread_id))
    assert r2.status == "sent"
    # Engine đã downgrade resolved_to thành ambiguous...
    assert r2.planner["resolved_reference"].get("resolved_to") is None
    # ...nên model tuân guard không được khẳng định Creta nữa.
    assert "Creta" not in (r2.reply_text or "")
