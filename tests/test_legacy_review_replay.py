"""A replay of an old review remains unsent and retains its diagnostics."""
from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline, _request_hash
from app.models import Actor, Message, Thread, TurnIdempotency
from app.providers.fake import FakeProvider
from app.schemas import TurnRequest


def test_legacy_review_replay_keeps_validator_errors(session):
    thread = Thread(persona_id="AUTO_029", post_id="legacy-post",
                    post_text="", post_vehicle_models=[])
    session.add_all([thread, Actor(id="legacy-actor", display_name="Khách")])
    session.commit()
    user = Message(thread_id=thread.id, actor_id="legacy-actor", role="user",
                   text="Câu hỏi cũ", status="sent")
    draft = Message(thread_id=thread.id, role="persona", text="Bản nháp cũ",
                    status="review", trace={"validator_errors": ["legacy-error"]})
    session.add_all([user, draft])
    session.commit()
    session.add(TurnIdempotency(
        thread_id=thread.id, actor_id="legacy-actor", idempotency_key="legacy-key",
        request_hash=_request_hash(thread.id, "legacy-actor", "Câu hỏi cũ"),
        user_message_id=user.id, persona_message_id=draft.id, status="review"))
    session.commit()

    sender = FakeSender()
    result = Pipeline(session, FakeProvider(), sender).run(
        TurnRequest(persona_id="AUTO_029", thread_id=thread.id,
                    actor_id="legacy-actor", text="Câu hỏi cũ"),
        idempotency_key="legacy-key")

    assert result.status == "review"
    assert result.validator_errors == ["legacy-error"]
    assert result.trace["validator_errors"] == ["legacy-error"]
    assert sender.sent == []
