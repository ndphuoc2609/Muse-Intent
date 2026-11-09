"""A newly created story and its addendum can be restored together."""
import json

import pytest
from sqlalchemy.orm import sessionmaker

from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.models import PersonaStory, StoryRevision, StoryTold
from app.providers.base import ProviderResult, ToolCall
from conftest import make_persona, turn


class StoryAndAddendumProvider:
    def chat(self, messages, tools):
        results = {}
        for message in messages:
            if message.get("role") == "tool":
                result = json.loads(message["content"])
                results[result.get("tool")] = result
        if "plan_turn" not in results:
            return ProviderResult(tool_calls=[ToolCall("plan_turn", {
                "intents": [{"type": "ask_persona", "subject": "persona",
                             "normalized": "hỏi kỷ niệm", "evidence": "Bác kể kỷ niệm đi"}],
                "goal": "share_experience"})])
        if "propose_story" not in results:
            return ProviderResult(tool_calls=[ToolCall("propose_story", {
                "summary": "Mình đi Creta về quê.",
                "body": "Mình đi Creta về quê.", "event_type": "trip",
                "entities": {"vehicle": "Hyundai Creta"},
                "tags": [], "categories": []})])
        event_id = results["propose_story"]["data"]["event_id"]
        if "propose_addendum" not in results:
            return ProviderResult(tool_calls=[ToolCall("propose_addendum", {
                "event_id": event_id, "content": "Hôm đó mình đi cùng cả nhà."})])
        return ProviderResult(tool_calls=[ToolCall("submit_final_reply", {
            "text": "Mình đi Creta về quê cùng cả nhà.",
            "claims": [{"kind": "story", "ref_id": event_id,
                        "value": "Mình đi Creta về quê cùng cả nhà"}],
            "goal": "share_experience"})])


def test_retry_restores_story_and_addendum_created_in_same_turn(session):
    pid = make_persona(session, "retry-new-story-and-addendum",
                       {"vehicle_model": "Hyundai Creta"})
    text = "Bác kể kỷ niệm đi"
    first = turn(Pipeline(session, StoryAndAddendumProvider(), FakeSender(True)),
                 text, idem="story-and-addendum", persona_id=pid)
    assert first.status == "error", first.validator_errors
    assert first.reply_text and first.saved_stories
    event_id = first.saved_stories[0]["event_id"]
    story = session.get(PersonaStory, event_id)
    revision = session.query(StoryRevision).filter_by(event_id=event_id).one()
    revision_id = revision.id
    assert story.status == "rejected" and revision.status == "rejected"

    with sessionmaker(bind=session.get_bind())() as retry_session:
        sender = FakeSender()
        pipe = Pipeline(retry_session, StoryAndAddendumProvider(), sender)
        recovered = turn(pipe, text, thread_id=first.thread_id,
                         idem="story-and-addendum", persona_id=pid)
        assert recovered.status == "sent", recovered.validator_errors
        assert recovered.message_id == first.message_id
        assert retry_session.get(PersonaStory, event_id).status == "active"
        assert retry_session.get(PersonaStory, event_id).version == 2
        assert retry_session.get(StoryRevision, revision_id).status == "active"
        assert retry_session.query(StoryTold).filter_by(
            event_id=event_id, message_id=first.message_id).count() == 1
        replay = turn(pipe, text, thread_id=first.thread_id,
                      idem="story-and-addendum", persona_id=pid)
        assert replay.status == "sent" and len(sender.sent) == 1


class RetellRevisionProvider:
    def chat(self, messages, tools):
        called = {json.loads(message["content"]).get("tool") for message in messages
                  if message.get("role") == "tool"}
        if "plan_turn" not in called:
            return ProviderResult(tool_calls=[ToolCall("plan_turn", {
                "intents": [{"type": "ask_persona", "subject": "persona",
                             "normalized": "hỏi chuyện đi cùng ai",
                             "evidence": "Bác đi cùng ai?"}],
                "goal": "share_experience"})])
        if "get_persona_stories" not in called:
            return ProviderResult(tool_calls=[ToolCall("get_persona_stories", {
                "event_id": "existing-story-with-revision"})])
        return ProviderResult(tool_calls=[ToolCall("submit_final_reply", {
            "text": "Hôm đó mình đi cùng cả nhà.",
            "claims": [{"kind": "story", "ref_id": "existing-story-with-revision",
                        "value": "Hôm đó mình đi cùng cả nhà"}],
            "goal": "share_experience"})])


@pytest.mark.parametrize("change", ["revoke", "edit"])
def test_retry_rejects_changed_active_revision(session, change):
    pid = make_persona(session, "retry-existing-revision",
                       {"vehicle_model": "Hyundai Creta"})
    story = PersonaStory(event_id="existing-story-with-revision", persona_id=pid,
                         summary="Mình đi Creta về quê.", body="Mình đi Creta về quê.",
                         status="active", version=2)
    revision = StoryRevision(event_id=story.event_id, persona_id=pid,
                             kind="addendum", content="Hôm đó mình đi cùng cả nhà.",
                             status="active", version=2)
    session.add_all([story, revision])
    session.commit()
    revision_id = revision.id
    text = "Bác đi cùng ai?"
    first = turn(Pipeline(session, RetellRevisionProvider(), FakeSender(True)),
                 text, idem="revision-changed", persona_id=pid)
    assert first.status == "error", first.validator_errors
    assert first.reply_text == "Hôm đó mình đi cùng cả nhà."

    if change == "revoke":
        revision.status = "revoked"
    else:
        revision.content = "Hôm đó mình đi một mình."
    session.commit()

    with sessionmaker(bind=session.get_bind())() as retry_session:
        sender = FakeSender()
        retried = turn(Pipeline(retry_session, RetellRevisionProvider(), sender),
                       text, thread_id=first.thread_id, idem="revision-changed",
                       persona_id=pid)
        assert retried.status != "sent"
        assert sender.sent == []
        assert retry_session.get(StoryRevision, revision_id).status == (
            "revoked" if change == "revoke" else "active")
