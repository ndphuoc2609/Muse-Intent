"""Provider adapter: tách model khỏi logic hội thoại (§16).

Một lượt chat của provider trả về danh sách tool call (native tool
calling) hoặc text. Pipeline tự lặp: gọi provider -> thực thi tool ở
engine -> đưa kết quả lại cho provider, tối đa max_tool_rounds.
Provider KHÔNG BAO GIỜ được ghi DB trực tiếp.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    name: str
    arguments: dict
    # id của tool call theo chuẩn OpenAI: bắt buộc để tool message sau
    # đó khớp đúng assistant message. FakeProvider không cần, pipeline
    # sẽ tự sinh nếu provider không trả id.
    id: str | None = None


@dataclass
class ProviderResult:
    tool_calls: list[ToolCall] = field(default_factory=list)
    text: str = ""
    usage: dict = field(default_factory=dict)  # tokens, cost_usd nếu có


class LLMProvider(Protocol):
    def chat(self, messages: list[dict], tools: list[dict]) -> ProviderResult:
        ...
