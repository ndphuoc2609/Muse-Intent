"""A story retold from canon: recovery must refuse it if the active
revision changed since the draft was made.

(In-turn story+addendum creation is gone — Phase 2c removed
propose_story/propose_addendum — so the old
test_retry_restores_story_and_addendum_created_in_same_turn has no
live producer path anymore and was deleted.)
"""

import json

import pytest
from sqlalchemy.orm import sessionmaker

from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.models import PersonaStory, StoryRevision
from app.providers.base import ProviderResult, ToolCall
from conftest import make_persona, turn


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
