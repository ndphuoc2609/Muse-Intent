"""Exercise the partner HTTP contract without using the configured database."""
import os
import subprocess
import sys
from pathlib import Path


def test_partner_turn_http_contract(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["DATABASE_URL"] = "sqlite:///" + (tmp_path / "partner.db").as_posix()
    env["LLM_PROVIDER"] = "fake"
    env["MAX_VALIDATOR_REPAIRS"] = "0"
    program = r'''
from fastapi.testclient import TestClient
import app.api as api
from app.engine.fake_sender import FakeSender
from app.providers.fake import FakeProvider
from app.seed import seed


class InvalidReplyProvider:
    def __init__(self):
        self.provider = FakeProvider()
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        result = self.provider.chat(messages, tools)
        for call in result.tool_calls:
            if call.name == "submit_final_reply":
                call.arguments["text"] += " Giá xe là 999 triệu đồng."
        return result


with api.Session() as session:
    seed(session)
api.PROVIDER = InvalidReplyProvider()
api.PROVIDER_ERROR = None
api.SENDER = FakeSender()
with TestClient(api.app) as client:
    created = client.post("/threads", json={
        "persona_id": "AUTO_029", "post_id": "partner-post-1",
        "post_text": "Bài về Creta.", "post_vehicle_models": ["Hyundai Creta"]})
    assert created.status_code == 200, created.text
    thread_id = created.json()["thread_id"]
    body = {"persona_id": "AUTO_029", "actor_id": "partner-user-1",
            "thread_id": thread_id, "text": "Bác đi xe màu gì thế?"}
    headers = {"Idempotency-Key": "partner-comment-1"}

    first = client.post("/turns", json=body, headers=headers)
    assert first.status_code == 200, first.text
    first_data = first.json()
    assert first_data["status"] == "sent", first_data
    assert first_data["thread_id"] == thread_id
    assert first_data["message_id"] and first_data["reply_text"]
    assert any("uncovered_factual_assertion" in e
               for e in first_data["validator_errors"]), first_data
    assert len(api.SENDER.sent) == 1
    calls_before_replay = api.PROVIDER.calls

    replay = client.post("/turns", json=body, headers=headers)
    assert replay.status_code == 200, replay.text
    replay_data = replay.json()
    assert replay_data["status"] == "sent"
    assert replay_data["message_id"] == first_data["message_id"]
    assert replay_data["reply_text"] == first_data["reply_text"]
    assert replay_data["trace"]["idempotent_replay"] is True
    assert replay_data["validator_errors"] == first_data["validator_errors"]
    assert api.PROVIDER.calls == calls_before_replay
    assert len(api.SENDER.sent) == 1

    conflict = client.post("/turns", json={**body, "text": "Bác đi xe gì?"},
                           headers=headers)
    assert conflict.status_code == 200, conflict.text
    conflict_data = conflict.json()
    assert conflict_data["status"] == "error"
    assert any("idempotency_conflict" in e
               for e in conflict_data["validator_errors"]), conflict_data
    assert len(api.SENDER.sent) == 1

    skipped = client.post("/turns", json={**body, "actor_id": "partner-user-2",
                                          "text": "ok"},
                          headers={"Idempotency-Key": "partner-comment-2"})
    assert skipped.status_code == 200, skipped.text
    skipped_data = skipped.json()
    assert skipped_data["status"] == "skipped"
    assert skipped_data["reply_text"] is None and skipped_data["message_id"] is None
    assert len(api.SENDER.sent) == 1
api.ENGINE.dispose()
'''
    result = subprocess.run([sys.executable, "-c", program], cwd=root, env=env,
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
