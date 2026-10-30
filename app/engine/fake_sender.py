"""FakeSender: mô phỏng gửi, không tích hợp Facebook/Meta (§14).

- send() trả sent/failed rõ ràng; failed thì không ghi delivery sent
  và không đánh dấu story đã kể.
- fail_next: dùng cho test recovery, bật qua env FAKE_SENDER_FAIL_NEXT=1
  hoặc cờ trên instance.
"""
from __future__ import annotations


class FakeSender:
    def __init__(self, fail_next: bool = False):
        self.fail_next = fail_next
        self.sent: list[dict] = []

    def send(self, message_id: str, text: str) -> dict:
        if self.fail_next:
            self.fail_next = False
            return {"status": "failed", "error": "fake_sender_simulated_failure"}
        self.sent.append({"message_id": message_id, "text": text})
        return {"status": "sent"}
