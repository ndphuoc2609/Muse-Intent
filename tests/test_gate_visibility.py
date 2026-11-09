"""The API exposes the existing rule-based gate decision without a score."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.models import Message
from app.providers.fake import FakeProvider
from conftest import turn


class NoCallsProvider:
    def chat(self, messages, tools):
        raise AssertionError("a replay or gate bypass must not call the model")


@pytest.mark.parametrize(("text", "action", "status"), [
    ("ok", "bypass", "skipped"),
    ("Cảm ơn bác", "faq", "sent"),
    ("Bác đang đi xe gì?", "pipeline", "sent"),
])
def test_gate_decision_visible_and_persisted_on_customer_message(session, text, action, status):
    sender = FakeSender()
    result = turn(Pipeline(session, FakeProvider(), sender), text, idem="gate-visible")

    assert result.status == status
    gate = result.trace["gate"]
    assert gate["action"] == action
    assert isinstance(gate["reason"], str) and gate["reason"]
    customer = session.execute(select(Message).where(
        Message.thread_id == result.thread_id, Message.role == "user"
    )).scalar_one()
    assert customer.trace["gate"] == gate
    assert "score" not in gate


@pytest.mark.parametrize(("text", "action"), [
    ("ok", "bypass"),
    ("Cảm ơn bác", "faq"),
    ("Bác đang đi xe gì?", "pipeline"),
])
def test_gate_replay_preserves_original_reason_without_calling_provider(session, text, action):
    sender = FakeSender()
    first = turn(Pipeline(session, FakeProvider(), sender), text, idem="gate-replay")
    original_gate = first.trace["gate"]
    sent_before = list(sender.sent)
    replay = turn(Pipeline(session, NoCallsProvider(), sender), text,
                  thread_id=first.thread_id, idem="gate-replay")

    assert replay.status == first.status
    assert replay.trace["idempotent_replay"] is True
    assert replay.trace["gate"] == original_gate
    assert replay.trace["gate"]["action"] == action
    assert sender.sent == sent_before
    assert len(session.execute(select(Message).where(
        Message.thread_id == first.thread_id, Message.role == "user"
    )).scalars().all()) == 1


def test_legacy_skipped_replay_does_not_invent_a_reason(session):
    first = turn(Pipeline(session, FakeProvider(), FakeSender()), "ok", idem="old-skip")
    customer = session.execute(select(Message).where(
        Message.thread_id == first.thread_id, Message.role == "user"
    )).scalar_one()
    customer.trace = {}
    session.commit()

    replay = turn(Pipeline(session, NoCallsProvider(), FakeSender()), "ok",
                  thread_id=first.thread_id, idem="old-skip")
    assert replay.status == "skipped"
    assert not (replay.trace.get("gate") or {}).get("reason")


def test_http_messages_include_customer_gate_trace(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["DATABASE_URL"] = "sqlite:///" + (tmp_path / "gate.db").as_posix()
    env["LLM_PROVIDER"] = "fake"
    program = r'''
from fastapi.testclient import TestClient
import app.api as api
from app.seed import seed

with api.Session() as session:
    seed(session)
with TestClient(api.app) as client:
    created = client.post('/threads', json={'persona_id':'AUTO_029'})
    assert created.status_code == 200
    thread_id = created.json()['thread_id']
    result = client.post('/turns', json={'persona_id':'AUTO_029',
        'thread_id':thread_id, 'actor_id':'partner-1', 'text':'ok'},
        headers={'Idempotency-Key':'comment-1'})
    assert result.status_code == 200
    decision = result.json()['trace']['gate']
    assert decision['action'] == 'bypass' and decision['reason']
    messages = client.get('/threads/' + thread_id + '/messages')
    assert messages.status_code == 200
    assert len(messages.json()) == 1
    assert messages.json()[0]['role'] == 'user'
    assert messages.json()[0]['trace']['gate'] == decision
api.ENGINE.dispose()
'''
    result = subprocess.run([sys.executable, "-c", program], cwd=root, env=env,
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
