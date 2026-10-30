from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.engine.validator import validate
from app.providers.fake import FakeProvider
from conftest import turn


def test_idempotency_same_key_same_result(pipe):
    # Scoped: cùng thread + actor + payload mới replay. turn() tạo
    # thread mới mỗi lần nếu không truyền thread_id, nên phải truyền
    # cùng thread_id để replay có nghĩa.
    r1 = turn(pipe, "Bác đang đi xe gì?", idem="k-1")
    r2 = turn(pipe, "Bác đang đi xe gì?", idem="k-1",
              thread_id=r1.thread_id)
    assert r1.message_id == r2.message_id
    assert r2.trace.get("idempotent_replay") is True


def test_same_idem_different_content_not_resent(pipe):
    r1 = turn(pipe, "Bác đang đi xe gì?", idem="k-2")
    # Cùng thread/actor/key nhưng khác nội dung -> conflict rõ ràng,
    # KHÔNG trả lại reply cũ im lặng (hành vi cũ đã bị sửa).
    r2 = turn(pipe, "Bác đi xe màu gì?", idem="k-2",
              thread_id=r1.thread_id)
    assert r2.status == "error"
    assert any("idempotency_conflict" in e
               for e in (r2.validator_errors or []))


def test_idem_same_key_different_thread_is_new_turn(pipe):
    # Cùng key nhưng khác thread là scope khác -> lượt mới, không replay.
    r1 = turn(pipe, "Bác đang đi xe gì?", idem="k-2b")
    r2 = turn(pipe, "Bác đang đi xe gì?", idem="k-2b")
    assert r2.status == "sent"
    assert r2.message_id != r1.message_id


def test_send_failure_and_recovery(session):
    pipe_fail = Pipeline(session, FakeProvider(), FakeSender(fail_next=True))
    r1 = turn(pipe_fail, "Bác đang đi xe gì?", idem="k-3")
    assert r1.status == "error"
    # Retry cùng key trong cùng thread: gửi lại đúng draft cũ,
    # không tạo story mới.
    r2 = turn(pipe_fail, "Bác đang đi xe gì?", idem="k-3",
              thread_id=r1.thread_id)
    assert r2.status == "sent"
    assert r2.message_id == r1.message_id


def test_db_error_is_unknown_not_not_found(session, monkeypatch):
    # Giả lập DB lỗi: query raise -> tool trả UNKNOWN, không phải NOT_FOUND
    # (để pipeline không coi lỗi DB là "fact chưa có" rồi sáng tác).
    from app.engine.tools import ToolExecutor

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated db failure")

    monkeypatch.setattr(session, "execute", _boom)
    ex = ToolExecutor(session, "hoang-nam", "t", "actor-1")
    r = ex.execute("get_persona_facts", {})
    assert r.status == "UNKNOWN"


def test_validator_blocks_unsourced_claim():
    errs = validate(
        {"text": "Giá xe là 999 triệu.", "claims": [
            {"kind": "product", "ref_id": "nope", "value": "999 triệu"}]},
        {"retrieved": {}})
    assert any("unsourced_claim" in e for e in errs)


def test_validator_allows_smalltalk_without_claim():
    errs = validate({"text": "Ừ, mình hiểu ý bạn rồi.", "claims": []},
                    {"retrieved": {}})
    assert errs == []


def test_validator_blocks_transaction_phrase():
    errs = validate({"text": "Mình đã đặt cọc cho bạn rồi nhé.", "claims": []},
                    {"retrieved": {}})
    assert any("transaction_confirmation" in e for e in errs)


def test_budget_enforced_by_pipeline_config(pipe, monkeypatch):
    # Budget call: pipeline dừng khi vượt max_llm_calls_per_turn.
    from app import config
    monkeypatch.setattr(config.SETTINGS, "max_llm_calls_per_turn", 0)
    r = turn(pipe, "Bác đang đi xe gì?")
    assert r.status == "error"
