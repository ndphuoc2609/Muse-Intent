"""Review fixes (2026-11-05): chunk versioning + seed purge, declined
writes, lead terminal state, negation guard, evidence actor-scoping.

Mỗi test kiểm tra đúng một cơ chế mới; full suite chạy kèm để bắt
hồi quy ở các test cũ (đặc biệt: evidence actor filter và negation
guard có thể làm lộ test cũ đang dựa vào lỗ cũ).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import init_db
from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.engine.rag import index_status, sha_bytes
from app.engine.tools import ToolExecutor
from app.engine.validator import (_negated_against, _story_matches,
                                  _stripped_tokens, validate)
from app.models import (LeadState, Message, Persona, QuestionState, RagChunk,
                        RagIndexMeta, Thread)
from app.schemas import Planner
from app.seed import _purge_removed
from tests.conftest import turn


class _PlanFinalStub:
    """Stub: call 1 -> plan_turn (kèm plan_extra), call 2 -> final."""

    def __init__(self, final_text, plan_extra=None, goal="answer_question",
                 final_goal="answer_question"):
        self.calls = 0
        self.final_text = final_text
        self.plan_extra = plan_extra or {}
        self.goal = goal
        self.final_goal = final_goal

    def chat(self, messages, tools):
        from app.providers.base import ProviderResult, ToolCall
        self.calls += 1
        if self.calls == 1:
            args = {"intents": [{"type": "social", "normalized": "x",
                                 "evidence": "x"}],
                    "goal": self.goal}
            args.update(self.plan_extra)
            return ProviderResult(tool_calls=[ToolCall(name="plan_turn",
                                                       arguments=args)],
                                  usage={"cost_usd": 0.0, "http_attempts": 0})
        return ProviderResult(
            tool_calls=[ToolCall(name="submit_final_reply", arguments={
                "text": self.final_text, "claims": [],
                "goal": self.final_goal})],
            usage={"cost_usd": 0.0, "http_attempts": 0})


def _lead_row(session, thread_id, actor="actor-1"):
    return session.execute(
        select(LeadState).where(
            LeadState.thread_id == thread_id,
            LeadState.actor_id == actor)).scalars().first()


# --- 1. Chunk versioning + index status ---

def test_sha_bytes_deterministic():
    assert sha_bytes(b"a") == sha_bytes(b"a")
    assert sha_bytes(b"a") != sha_bytes(b"b")


def test_index_status_states(session):
    # Vừa seed, chưa build: có meta seed_persona_sha nhưng 0 chunk.
    st = index_status(session)
    assert st["status"] == "unavailable"
    assert st["chunk_count"] == 0
    # Build cũ (trước versioning): có chunk nhưng thiếu persona_sha.
    session.add(RagChunk(id="c1", persona_id="AUTO_029", domain="persona",
                         chunk_type="memory", source_ref="r1", text="t",
                         embedding="[]"))
    session.commit()
    assert index_status(session)["status"] == "unknown"
    # Build mới nhưng seed đã chạy với dữ liệu khác sau đó.
    session.add(RagIndexMeta(key="persona_sha", value='"aaa"'))
    _seed_row = session.execute(
        select(RagIndexMeta).where(
            RagIndexMeta.key == "seed_persona_sha")).scalars().first()
    _seed_row.value = '"bbb"'
    session.commit()
    assert index_status(session)["status"] == "stale"
    # Khớp nhau -> ready.
    row = session.execute(
        select(RagIndexMeta).where(
            RagIndexMeta.key == "seed_persona_sha")).scalars().first()
    row.value = '"aaa"'
    session.commit()
    st = index_status(session)
    assert st["status"] == "ready"
    assert st["chunk_count"] == 1


def test_seed_purge_deletes_chunks(session):
    fake = Persona(id="FAKE_001", display_name="Fake", age=None, region="",
                   stance="peer_user", style="", is_fictional=True)
    session.add(fake)
    session.add(RagChunk(id="c-fake", persona_id="FAKE_001",
                         domain="persona", chunk_type="memory",
                         source_ref="r", text="t", embedding="[]"))
    session.add(RagChunk(id="c-company", persona_id=None, domain="company",
                         chunk_type="dealer", source_ref="d", text="t",
                         embedding="[]"))
    session.commit()
    keep = {p.id for p in session.execute(select(Persona)).scalars().all()
            if p.id != "FAKE_001"}
    removed = _purge_removed(session, keep)
    session.commit()  # _purge_removed chỉ xóa trong session, seed() mới commit.
    assert "FAKE_001" in removed
    assert session.get(Persona, "FAKE_001") is None
    assert session.get(RagChunk, "c-fake") is None
    # Chunk đại lý (persona_id NULL) không bị purge theo persona.
    assert session.get(RagChunk, "c-company") is not None


# --- 2. Declined writes + validator refused ---

def test_declined_write_and_validator_blocks_reask(session, pipe):
    session.add(Thread(id="t-decl", persona_id="AUTO_029",
                       post_text="Bài về Creta."))
    q = QuestionState(thread_id="t-decl", actor_id="actor-1",
                      question_key="area", question_text="Bạn ở đâu?",
                      status="pending")
    session.add(q)
    session.commit()
    pipe._update_questions("t-decl", "actor-1",
                           Planner(declined_question_keys=["area"]),
                           "m1", [q])
    assert q.status == "declined"
    # Validator §4 cấm hỏi lại key đã từ chối (qua followup_key).
    final = {"text": "Bạn ở khu vực nào?", "goal": "answer_question",
             "claims": [], "followup_question": "Bạn ở khu vực nào?",
             "followup_key": "area"}
    errs = validate(final, {"refused_keys": ["area"], "retrieved": {}})
    assert any("refused_question" in e for e in errs)
    # Control: không có refused key thì không lỗi refused_question.
    errs2 = validate(final, {"refused_keys": [], "retrieved": {}})
    assert not any("refused_question" in e for e in errs2)
    # ANSWERED thắng khi planner nêu cả hai (mâu thuẫn của planner).
    q2 = QuestionState(thread_id="t-decl", actor_id="actor-1",
                       question_key="color", question_text="Màu gì?",
                       status="pending")
    session.add(q2)
    session.commit()
    pipe._update_questions(
        "t-decl", "actor-1",
        Planner(answered_question_keys=["color"],
                declined_question_keys=["color"]), "m2", [q2])
    assert q2.status == "answered"


# --- 3. Lead terminal state: latch + reopen + opt-out ---

def test_lead_terminal_latch_and_reopen(session):
    # T1: tín hiệu mua -> latch bật (lưu vào history).
    p1 = Pipeline(session, _PlanFinalStub("Mình thấy Creta đi phố ổn."),
                  FakeSender())
    r1 = turn(p1, "Em đang tính mua Creta, bác tư vấn giúp")
    assert r1.status == "sent"
    tid = r1.thread_id
    # T2: planner phán quyết topic_changed -> ghi trạng thái kết thúc.
    p2 = Pipeline(session, _PlanFinalStub(
        "Ok bạn, chúc vui nhé.",
        plan_extra={"lead_outcome": "topic_changed"}), FakeSender())
    r2 = turn(p2, "Thôi, em chỉ hỏi chơi thôi, không mua nữa",
              thread_id=tid)
    row = _lead_row(session, tid)
    assert row is not None and row.status == "topic_changed"
    # T3: không còn tín hiệu mới, latch lịch sử bị khóa -> hỏi SĐT bị chặn.
    p3 = Pipeline(session, _PlanFinalStub("Cho mình số điện thoại nhé"),
                  FakeSender())
    r3 = turn(p3, "Bác đi Creta lâu chưa?", thread_id=tid)
    assert r3.status == "review"
    assert any("private_request" in e for e in r3.validator_errors)
    # T4: tín hiệu mua mới, planner không phán quyết terminal -> mở lại.
    p4 = Pipeline(session, _PlanFinalStub("Creta đi phố tiết kiệm lắm."),
                  FakeSender())
    turn(p4, "Em lại muốn mua Creta rồi", thread_id=tid)
    assert _lead_row(session, tid) is None
    # T5: không tín hiệu mới nhưng latch lịch sử đã mở lại -> SĐT được hỏi.
    p5 = Pipeline(session, _PlanFinalStub("Cho mình số điện thoại nhé"),
                  FakeSender())
    r5 = turn(p5, "Bác kể chuyện đi chơi đi", thread_id=tid)
    assert r5.status == "sent"


def test_lead_opt_out_writes_refused(session):
    p1 = Pipeline(session, _PlanFinalStub("Mình thấy Creta đi phố ổn."),
                  FakeSender())
    r1 = turn(p1, "Em đang tính mua Creta, bác tư vấn giúp")
    tid = r1.thread_id
    p2 = Pipeline(session, _PlanFinalStub("Ok, mình dừng nhé.",
                                          goal="stop", final_goal="stop"),
                  FakeSender())
    turn(p2, "Dừng lại đi, đừng nhắn nữa", thread_id=tid)
    row = _lead_row(session, tid)
    assert row is not None and row.status == "refused"


# --- 4. Negation guard ---

def test_negation_guard():
    # Ví dụ của review: trùng 6/7 token nhưng nghĩa ngược nhau.
    assert _story_matches("Tôi đã chạy xe này đường dài",
                          "Tôi chưa từng chạy xe này đường dài") is False
    # Cùng phủ định thì vẫn khớp.
    assert _story_matches("Tôi chưa từng chạy xe này đường dài",
                          "Tôi chưa từng chạy xe này đường dài") is True
    # Không phủ định: khớp như cũ.
    assert _story_matches("xe màu trắng", "xe màu trắng") is True
    # 'không chỉ' là nhượng bộ, không tính là phủ định.
    assert _negated_against(
        _stripped_tokens("xe không chỉ đẹp mà còn bền"),
        {"dep"}) is False
    assert _story_matches("xe đẹp", "xe không chỉ đẹp mà còn bền") is True
    # Phủ định đảo cả khẳng định số (typed match cũng không cứu).
    assert _story_matches("đi 500km", "chưa đi 500km") is False
    # 'đúng' sau khi bỏ dấu thành 'dung' nên không được tính là 'đừng'.
    assert _negated_against(
        _stripped_tokens("xe này đúng là đẹp"), {"dep"}) is False
    assert _negated_against(
        _stripped_tokens("Tôi chưa từng đi"), {"di"}) is True


def test_negation_guard_mem3_regression(session):
    # Hồi quy: 'không rõ độ sâu' trong mem_3 chỉ bổ nghĩa cho 'độ sâu',
    # không phủ định mệnh đề chính — claim của test_storytold_dedupe
    # phải khớp (bản guard đầu tiên chặn nhầm ca này -> review).
    from app.models import PersonaStory
    story = session.get(PersonaStory, "AUTO_029_mem_3")
    assert story is not None
    assert _story_matches(
        "Thảo luôn chọn quay đầu thay vì liều qua đoạn ngập",
        story.summary) is True


# --- 5. Evidence đúng actor ---

def test_evidence_actor_scoped(session):
    session.add(Thread(id="t-ev", persona_id="AUTO_029",
                       post_text="Bài về Creta."))
    session.add(Message(thread_id="t-ev", actor_id="actor-1", role="user",
                        text="Mình ở Hà Nội", status="sent"))
    session.add(Message(thread_id="t-ev", actor_id="actor-2", role="user",
                        text="Mình ở TP.HCM", status="sent"))
    session.commit()
    ex = ToolExecutor(session, "AUTO_029", "t-ev", "actor-1",
                      current_message_id="m1",
                      current_user_text="Mình ở Hà Nội")
    # Lời của Khách 2 không làm evidence cho fact của Khách 1.
    res = ex.tool_propose_customer_fact({
        "subject": "self", "predicate": "location",
        "value_raw": "TP.HCM", "evidence": "Mình ở TP.HCM"})
    assert res.status == "CONFLICT"
    assert "evidence" in res.reason
    # Lời của chính Khách 1 thì qua.
    res2 = ex.tool_propose_customer_fact({
        "subject": "self", "predicate": "location",
        "value_raw": "Hà Nội", "evidence": "Mình ở Hà Nội"})
    assert res2.status == "FOUND"


# --- 6. Build script end-to-end (meta preservation) ---

def test_build_script_end_to_end(monkeypatch):
    import json
    from sqlalchemy import create_engine as _ce
    from sqlalchemy.orm import sessionmaker as _sm
    from scripts.build_rag_index import main as _build_main
    scratch = Path.home() / "workspace" / ".build-test-review-fixes.db"
    try:
        monkeypatch.setattr(
            sys, "argv",
            ["build_rag_index.py", "--db", f"sqlite:///{scratch}",
             "--provider", "tfidf",
             "--company", "data/dealers.json"])
        # Chạy từ thư mục project để --company tương đối đúng.
        monkeypatch.chdir(Path(__file__).resolve().parents[1])
        assert _build_main() == 0
        eng = _ce(f"sqlite:///{scratch}")
        s = _sm(bind=eng)()
        meta = {r.key: json.loads(r.value) for r in
                s.execute(select(RagIndexMeta)).scalars().all()}
        assert meta["persona_sha"] == meta["seed_persona_sha"]
        assert meta["company_sha"]
        st = index_status(s)
        assert st["status"] == "ready"
        assert st["chunk_count"] > 2000
        persona_chunk = s.execute(
            select(RagChunk).where(
                RagChunk.domain == "persona")).scalars().first()
        assert persona_chunk.source_sha == meta["persona_sha"]
        company_chunk = s.execute(
            select(RagChunk).where(
                RagChunk.domain == "company")).scalars().first()
        assert company_chunk.source_sha == meta["company_sha"]
        s.close()
    finally:
        if scratch.exists():
            scratch.unlink()
