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
    # Lỗi blocking (uncovered money) -> giữ ở review, KHÔNG gửi.
    assert first_data["status"] == "review", first_data
    assert first_data["thread_id"] == thread_id
    assert first_data["message_id"] and first_data["reply_text"]
    assert any("uncovered_factual_assertion" in e
               for e in first_data["validator_errors"]), first_data
    assert len(api.SENDER.sent) == 0
    calls_before_replay = api.PROVIDER.calls

    replay = client.post("/turns", json=body, headers=headers)
    assert replay.status_code == 200, replay.text
    replay_data = replay.json()
    assert replay_data["status"] == "review"
    assert replay_data["message_id"] == first_data["message_id"]
    assert replay_data["reply_text"] == first_data["reply_text"]
    assert replay_data["trace"]["idempotent_replay"] is True
    assert replay_data["validator_errors"] == first_data["validator_errors"]
    assert api.PROVIDER.calls == calls_before_replay
    assert len(api.SENDER.sent) == 0

    conflict = client.post("/turns", json={**body, "text": "Bác đi xe gì?"},
                           headers=headers)
    assert conflict.status_code == 200, conflict.text
    conflict_data = conflict.json()
    assert conflict_data["status"] == "error"
    assert any("idempotency_conflict" in e
               for e in conflict_data["validator_errors"]), conflict_data
    assert len(api.SENDER.sent) == 0

    skipped = client.post("/turns", json={**body, "actor_id": "partner-user-2",
                                          "text": "ok"},
                          headers={"Idempotency-Key": "partner-comment-2"})
    assert skipped.status_code == 200, skipped.text
    skipped_data = skipped.json()
    assert skipped_data["status"] == "skipped"
    assert skipped_data["reply_text"] is None and skipped_data["message_id"] is None
    assert len(api.SENDER.sent) == 0
api.ENGINE.dispose()
'''
    result = subprocess.run([sys.executable, "-c", program], cwd=root, env=env,
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_partner_mode_ready_and_confirmation_http_contract(tmp_path):
    """Partner mode (PARTNER_API_KEY): turn stage ở ready_for_delivery,
    chỉ sent khi partner xác nhận; auth 401, tenant 403, xác nhận
    idempotent, mâu thuẫn -> 409 + trace.delivery_conflict."""
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["DATABASE_URL"] = "sqlite:///" + (tmp_path / "partner-mode.db").as_posix()
    env["LLM_PROVIDER"] = "fake"
    env["MAX_VALIDATOR_REPAIRS"] = "0"
    env["PARTNER_API_KEY"] = "test-partner-key"
    env["PARTNER_TENANT"] = "acme"
    program = r'''
from fastapi.testclient import TestClient
import app.api as api
from app.engine.fake_sender import FakeSender
from app.models import Delivery, Message, StoryTold, Thread
from app.providers.fake import FakeProvider
from app.seed import seed

with api.Session() as session:
    seed(session)
api.PROVIDER = FakeProvider()
api.PROVIDER_ERROR = None
api.SENDER = FakeSender()
KEY = {"X-API-Key": "test-partner-key"}
with TestClient(api.app) as client:
    # Không key -> 401 trên endpoint partner.
    assert client.post("/threads", json={"persona_id": "AUTO_029"}).status_code == 401
    assert client.post("/turns", json={"persona_id": "AUTO_029", "actor_id": "a",
                                       "text": "x"}).status_code == 401
    # Có key -> tạo thread, tenant ép theo key (body tenant bị bỏ qua).
    created = client.post("/threads", json={
        "persona_id": "AUTO_029", "post_id": "partner-post-1",
        "post_text": "Bài về Creta.", "post_vehicle_models": ["Hyundai Creta"],
        "tenant": "other"}, headers=KEY)
    assert created.status_code == 200, created.text
    assert created.json()["tenant"] == "acme"
    thread_id = created.json()["thread_id"]
    # Thread tenant khác -> 403.
    with api.Session() as session:
        session.add(Thread(persona_id="AUTO_029", post_id="other-post",
                           post_text="Bài khác.",
                           post_vehicle_models=["Hyundai Creta"],
                           tenant="other-tenant"))
        session.commit()
        other_id = session.query(Thread).filter_by(post_id="other-post").one().id
    denied = client.post(f"/threads/{other_id}/turns", json={
        "persona_id": "AUTO_029", "actor_id": "a", "text": "Bác đang đi xe gì?"},
        headers=KEY)
    assert denied.status_code == 403, denied.text
    # Turn sạch -> ready_for_delivery, KHÔNG gọi sender.
    first = client.post(f"/threads/{thread_id}/turns", json={
        "persona_id": "AUTO_029", "actor_id": "partner-user-1",
        "text": "Bác đang đi xe gì?"}, headers={**KEY, "Idempotency-Key": "c1"})
    assert first.status_code == 200, first.text
    data = first.json()
    assert data["status"] == "ready_for_delivery", data
    assert len(api.SENDER.sent) == 0
    mid = data["message_id"]
    with api.Session() as session:
        assert session.get(Message, mid).status == "ready_for_delivery"
        row = session.query(Delivery).filter_by(message_id=mid).one()
        assert row.status == "unknown" and row.sender == "partner"
    url = f"/threads/{thread_id}/messages/{mid}/delivery-confirmation"
    # Xác nhận không key -> 401; unknown -> vẫn chờ.
    assert client.post(url, json={"status": "sent"}).status_code == 401
    unk = client.post(url, json={"status": "unknown"}, headers=KEY)
    assert unk.status_code == 200 and unk.json()["status"] == "ready_for_delivery"
    # Xác nhận sent -> kích hoạt, báo lại cùng status -> idempotent.
    ok = client.post(url, json={"status": "sent", "channel_message_id": "fb-1"},
                     headers=KEY)
    assert ok.status_code == 200 and ok.json()["status"] == "sent"
    with api.Session() as session:
        assert session.get(Message, mid).status == "sent"
        assert session.query(Delivery).filter_by(
            message_id=mid, status="sent").count() == 1
    again = client.post(url, json={"status": "sent"}, headers=KEY)
    assert again.status_code == 200 and again.json().get("idempotent_replay") is True
    # Báo mâu thuẫn sau khi đã sent -> 409 + ghi trace, trạng thái giữ nguyên.
    conflict = client.post(url, json={"status": "failed", "error": "late"},
                           headers=KEY)
    assert conflict.status_code == 409, conflict.text
    with api.Session() as session:
        assert "delivery_conflict" in (session.get(Message, mid).trace or {})
        assert session.get(Message, mid).status == "sent"
    # Lượt story: xác nhận sent mới kích hoạt StoryTold.
    second = client.post(f"/threads/{thread_id}/turns", json={
        "persona_id": "AUTO_029", "actor_id": "partner-user-1",
        "text": "Bác có kỷ niệm nào đáng nhớ với xe không?"},
        headers={**KEY, "Idempotency-Key": "c2"})
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "ready_for_delivery"
    mid2 = second.json()["message_id"]
    with api.Session() as session:
        assert session.query(StoryTold).filter_by(message_id=mid2).count() == 0
    url2 = f"/threads/{thread_id}/messages/{mid2}/delivery-confirmation"
    assert client.post(url2, json={"status": "sent"}, headers=KEY).status_code == 200
    with api.Session() as session:
        assert session.query(StoryTold).filter_by(message_id=mid2).count() == 1
    # Lượt failed: xác nhận failed -> error + idempotent; báo sent sau -> 409.
    third = client.post(f"/threads/{thread_id}/turns", json={
        "persona_id": "AUTO_029", "actor_id": "partner-user-1",
        "text": "Bác mua xe khi nào?"},
        headers={**KEY, "Idempotency-Key": "c3"})
    mid3 = third.json()["message_id"]
    url3 = f"/threads/{thread_id}/messages/{mid3}/delivery-confirmation"
    fail = client.post(url3, json={"status": "failed", "error": "timeout"},
                       headers=KEY)
    assert fail.status_code == 200 and fail.json()["status"] == "error"
    with api.Session() as session:
        assert session.get(Message, mid3).status == "error"
        assert session.query(Delivery).filter_by(
            message_id=mid3, status="failed").count() == 1
    assert client.post(url3, json={"status": "failed"}, headers=KEY).json().get(
        "idempotent_replay") is True
    assert client.post(url3, json={"status": "sent"}, headers=KEY).status_code == 409
    # Status lạ -> 400; message không tồn tại -> 404.
    assert client.post(url3, json={"status": "maybe"}, headers=KEY).status_code == 400
    assert client.post(
        f"/threads/{thread_id}/messages/nope/delivery-confirmation",
        json={"status": "sent"}, headers=KEY).status_code == 404
api.ENGINE.dispose()
'''
    result = subprocess.run([sys.executable, "-c", program], cwd=root, env=env,
                            text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
