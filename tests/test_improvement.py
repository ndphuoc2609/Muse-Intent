"""Regression tests for the improvement branch (claim/history/idempotency).

All offline, Fake/stub providers only — no network, no OpenRouter.
"""
from sqlalchemy import select

from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.engine.tools import ToolExecutor
from app.engine.validator import validate
from app.engine.typed_values import value_matches
from app.models import CustomerFact, PersonaFact, PersonaStory, QuestionState, StoryTold
from app.providers.base import ProviderResult, ToolCall
from app.providers.fake import FakeProvider
from conftest import turn


# --- 4.2 typed values ---
def test_typed_money_wrong_number_does_not_match():
    assert value_matches("5999 triệu đồng", "599 triệu đồng") is False
    assert value_matches("599 triệu đồng", "599 triệu đồng") is True
    assert value_matches("599 triệu", "599.000.000 đồng") is True
    assert value_matches("1,2 tỷ", "1200 triệu") is True
    assert value_matches("trắng sữa", "trắng") is False


def test_validator_uncovered_money_without_claim():
    errs = validate({"text": "Giá xe là 599 triệu đồng.", "claims": []},
                    {"retrieved": {}})
    assert any("uncovered_factual_assertion" in e for e in errs)


def test_validator_uncovered_money_with_matching_claim_passes():
    errs = validate(
        {"text": "Giá xe là 599 triệu đồng (giá mẫu).", "claims": [
            {"kind": "product", "ref_id": "y", "value": "599 triệu đồng"}]},
        {"retrieved": {"y": ("product", "599 triệu đồng",
                              {"status": "active", "valid_to": None,
                               "now": "2026-10-30", "is_sample": True})}})
    assert errs == []


def test_validator_question_and_negation_and_echo_are_not_assertions():
    assert validate({"text": "Giá xe bao nhiêu? 599 triệu có đúng không?",
                     "claims": []}, {"retrieved": {}}) == []
    assert validate({"text": "Giá không phải 599 triệu đâu bạn.",
                     "claims": []}, {"retrieved": {}}) == []
    assert validate({"text": "Bạn nói giá 599 triệu, mình nhớ rồi.",
                     "claims": []}, {"retrieved": {}}) == []


def test_validator_sample_unlabelled_blocked():
    errs = validate(
        {"text": "Giá xe là 599 triệu đồng.", "claims": [
            {"kind": "product", "ref_id": "y", "value": "599 triệu đồng"}]},
        {"retrieved": {"y": ("product", "599 triệu đồng",
                              {"status": "active", "valid_to": None,
                               "now": "2026-10-30", "is_sample": True})}})
    assert any("product_sample_unlabelled" in e for e in errs)


# --- Lifecycle: pending -> active only on sent, rejected otherwise ---
def test_proposal_is_pending_until_activated(session):
    ex = ToolExecutor(session, "hoang-nam", "t1", "actor-1",
                      current_message_id="m1",
                      current_user_text="Mình thích màu trắng.")
    r = ex.execute("propose_persona_fact",
                   {"fact_key": "vehicle_color", "value": "trắng",
                    "source": "generated", "evidence": "màu trắng"})
    assert r.status == "FOUND"
    row = session.get(PersonaFact, r.data["id"])
    assert row.status == "pending"
    # get_persona_facts (active only) must NOT return the candidate.
    got = ex.execute("get_persona_facts", {"keys": ["vehicle_color"]})
    assert got.status == "NOT_FOUND"
    ex.activate_pending()
    assert session.get(PersonaFact, r.data["id"]).status == "active"
    got2 = ex.execute("get_persona_facts", {"keys": ["vehicle_color"]})
    assert got2.status == "FOUND"


def test_proposal_rejected_on_failed_turn_does_not_pollute(session):
    ex = ToolExecutor(session, "hoang-nam", "t1", "actor-1",
                      current_message_id="m1",
                      current_user_text="Mình thích màu đỏ.")
    r = ex.execute("propose_persona_fact",
                   {"fact_key": "vehicle_color", "value": "đỏ",
                    "source": "generated", "evidence": "màu đỏ"})
    ex.reject_pending()
    assert session.get(PersonaFact, r.data["id"]).status == "rejected"
    assert ex.execute("get_persona_facts",
                      {"keys": ["vehicle_color"]}).status == "NOT_FOUND"


def test_customer_facts_father_and_mother_do_not_overwrite_each_other(session):
    ex = ToolExecutor(session, "hoang-nam", "t1", "actor-1",
                      current_message_id="m1",
                      current_user_text="Mua cho bố và cho mẹ.")
    for rel in ("bố", "mẹ"):
        r = ex.execute("propose_customer_fact",
                       {"subject": "third_person", "relation": rel,
                        "predicate": "intended_user", "value_raw": rel,
                        "evidence": f"Mua cho {rel}."})
        assert r.status == "FOUND", r
    ex.activate_pending()
    rows = session.execute(
        select(CustomerFact).where(
            CustomerFact.thread_id == "t1", CustomerFact.actor_id == "actor-1",
            CustomerFact.status == "current")).scalars().all()
    assert sorted(r.relation for r in rows) == ["bố", "mẹ"]


def test_customer_fact_evidence_must_match_server_message(session):
    ex = ToolExecutor(session, "hoang-nam", "t1", "actor-1",
                      current_message_id="m1",
                      current_user_text="Mình chủ yếu đi phố.")
    r = ex.execute("propose_customer_fact",
                   {"subject": "self", "predicate": "use_case",
                    "value_raw": "đi xa", "evidence": "Tôi đi đường dài mỗi tuần."})
    assert r.status == "CONFLICT"


# --- QuestionState: topic change does not lose pending; followup only on sent ---
def test_pending_question_survives_topic_change(pipe):
    r1 = turn(pipe, "Em đang cân nhắc con này cho mẹ.")
    assert r1.status == "sent"
    # Like-point question was asked in r1 (followup). Turn 2 changes topic.
    r2 = turn(pipe, "Mình chủ yếu chở gia đình đi xa.",
              thread_id=r1.thread_id)
    assert r2.status == "sent"
    qs = pipe.s.execute(
        select(QuestionState).where(
            QuestionState.thread_id == r1.thread_id)).scalars().all()
    assert any(q.question_key == "like_point" and q.status == "pending"
               for q in qs)


def test_followup_not_created_when_delivery_fails(session):
    pipe_fail = Pipeline(session, FakeProvider(), FakeSender(fail_next=True))
    r1 = turn(pipe_fail, "Em đang cân nhắc con này cho mẹ.")
    assert r1.status == "error"
    qs = session.execute(
        select(QuestionState).where(
            QuestionState.thread_id == r1.thread_id)).scalars().all()
    assert not any(q.question_key == "like_point" for q in qs)


def test_story_told_recorded_for_retold_story(pipe):
    r1 = turn(pipe, "Bác có kỷ niệm nào đáng nhớ với xe không?")
    assert r1.status == "sent"
    rows = pipe.s.execute(
        select(StoryTold).where(
            StoryTold.thread_id == r1.thread_id,
            StoryTold.actor_id == "actor-1")).scalars().all()
    assert len(rows) >= 1


# --- Protocol: planner first, plain text ---
class _PlannerFirstStub:
    """First call tries to propose before plan; second call is proper."""
    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return ProviderResult(
                tool_calls=[ToolCall(name="propose_persona_fact",
                                     arguments={"fact_key": "vehicle_color",
                                                "value": "đỏ"})],
                usage={"cost_usd": 0.0, "http_attempts": 0})
        if self.calls == 2:
            return ProviderResult(
                tool_calls=[ToolCall(name="plan_turn", arguments={
                    "intents": [{"type": "social", "normalized": "chào",
                                 "evidence": "chào"}], "goal": "answer_question"})],
                usage={"cost_usd": 0.0, "http_attempts": 0})
        return ProviderResult(
            tool_calls=[ToolCall(name="submit_final_reply", arguments={
                "text": "Chào bạn.", "claims": [], "goal": "answer_question"})],
            usage={"cost_usd": 0.0, "http_attempts": 0})


def test_mutation_before_plan_is_blocked(session):
    pipe = Pipeline(session, _PlannerFirstStub(), FakeSender())
    r = turn(pipe, "Chào bạn.")
    assert r.status == "sent"
    assert r.reply_text == "Chào bạn."
    # The pre-plan propose must NOT have become a fact.
    rows = session.execute(
        select(PersonaFact).where(
            PersonaFact.persona_id == "hoang-nam",
            PersonaFact.fact_key == "vehicle_color",
            PersonaFact.status == "active")).scalars().all()
    assert rows == []


class _PlainTextStub:
    def __init__(self, text):
        self.text = text

    def chat(self, messages, tools):
        return ProviderResult(tool_calls=[], text=self.text,
                              usage={"cost_usd": None, "http_attempts": 1})


def test_plain_text_with_facts_is_not_sent(session):
    pipe = Pipeline(session, _PlainTextStub("Giá xe là 599 triệu đồng."),
                    FakeSender())
    r = turn(pipe, "Giá xe bao nhiêu?")
    # Protocol error, not sent: factual text never reached the user.
    assert r.status == "error"
    assert r.reply_text is None or "599" not in r.reply_text


def test_plain_text_pure_social_is_sent(session):
    pipe = Pipeline(session, _PlainTextStub("Chào bạn, mình đây."),
                    FakeSender())
    r = turn(pipe, "Chào bạn.")
    assert r.status == "sent"


def test_cost_unknown_is_not_zero_in_trace(session):
    pipe = Pipeline(session, _PlainTextStub("Chào bạn, mình đây."),
                    FakeSender())
    r = turn(pipe, "Chào bạn.")
    assert r.trace.get("cost_known") is False
    assert r.trace.get("cost_unknown_calls", 0) >= 1
    assert r.trace.get("http_attempts", 0) >= 1
