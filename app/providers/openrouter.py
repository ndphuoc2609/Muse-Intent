"""OpenRouter adapter (OpenAI-compatible chat completions).

Model mặc định: openai/gpt-6-luna (Luna, theo connector custom.openrouter
đã kiểm chứng smoke test 2026-10-23). Model cấu hình được qua env
OPENROUTER_MODEL; adapter không hardcode key — key chỉ qua env, không
ghi log, không đưa vào trace.
"""
from __future__ import annotations

import json
import time

import httpx

from ..config import SETTINGS
from .base import ProviderResult, ToolCall

SYSTEM_PROMPT = """Bạn là model hội thoại cho persona chia sẻ trải nghiệm xe (peer_user,
không phải nhân viên bán hàng). Tuân thủ tuyệt đối:
1. Gọi plan_turn trước tiên: liệt kê MỌI ý trong lượt, không ép thành một intent.
   plan_turn BẮT BUỘC dùng đúng vocab sau, không viết tự do:
   - goal: chỉ một trong answer_question, share_experience, clarify,
     explore_need, empathize, stop, handoff.
   - intent type: chỉ một trong ask_persona, ask_product, share_need,
     third_party, correction, negation, opt_out, transaction, social,
     ai_identity, affirmation.
   - intent source: chỉ stated (khách nói rõ) hoặc inferred_context
     (suy luận từ context bài viết/lượt trước).
   - intent subject: chỉ self, third_person hoặc persona.
   - response_direction: chỉ answer_first, ask_followup, followup_purpose,
     followup_key — không thêm field khác.
2. Chỉ dùng dữ kiện tool trả về; không bịa giá, thông số, màu xe, câu chuyện.
3. Muốn dùng chi tiết persona mới: gọi propose_persona_fact / propose_story /
   propose_addendum trước, chỉ đưa vào reply sau khi engine trả FOUND kèm ID.
4. Trả lời câu hỏi trước, hỏi thêm tối đa 1 câu và chỉ khi thực sự hữu ích.
   QUAN TRỌNG VỀ LỊCH SỬ: các message user/assistant trước lượt này là
   hội thoại thật, theo đúng thứ tự. Câu trả lời ngắn, lược bỏ chủ ngữ
   (VD: "Tiết kiệm thôi", "Ừ, rộng rãi") thường là trả lời câu hỏi
   NGAY TRƯỚC ĐÓ trong lịch sử, không phải câu mới. Khi đó:
   - plan_turn PHẢI liệt kê key của câu hỏi đó vào answered_question_keys
     (key lấy từ "Câu hỏi đang chờ" trong context, hoặc "followup_question"
     nếu model tự hỏi ở lượt trước);
   - reply PHẢI ghi nhận đáp án đó trước rồi mới đi tiếp (VD: khách chọn
     tiết kiệm thì nói về tiết kiệm / xem xe, không hỏi lại "bạn ưu tiên
     điều gì nhất" nữa);
   - TUYỆT ĐỐI KHÔNG hỏi lại một câu hỏi đã xuất hiện trong lịch sử,
     kể cả khi câu hỏi đó không có trong "Câu hỏi đang chờ".
5. Không xác nhận cọc/mua bán, không hứa nhân viên sẽ gọi, không xin số điện thoại.
6. Khi khách hỏi trực tiếp có phải AI không: nói rõ là persona hư cấu, không phải người thật.
7. Kết thúc bằng submit_final_reply (mọi claim kèm ref_id) hoặc stop_or_insufficient.
Trả lời bằng tiếng Việt tự nhiên, không điền form, không dùng câu mẫu cứng.
"""


class OpenRouterProvider:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or SETTINGS.openrouter_api_key
        self.model = model or SETTINGS.openrouter_model

    def chat(self, messages: list[dict], tools: list[dict]) -> ProviderResult:
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY chưa cấu hình")
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.3,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://persona-dialogue.local",
            "X-Title": "persona-dialogue",
        }
        last_err: Exception | None = None
        http_attempts = 0
        for attempt in range(SETTINGS.llm_max_retries + 1):
            try:
                http_attempts += 1
                with httpx.Client(timeout=SETTINGS.llm_timeout_s) as client:
                    r = client.post(
                        f"{SETTINGS.openrouter_base_url}/chat/completions",
                        json=payload, headers=headers)
                if r.status_code >= 400:
                    # 400/401/403/404 là lỗi client/payload/auth: retry
                    # cùng payload không sửa được gì, chỉ tốn call.
                    # Trả kèm body của OpenRouter (không chứa key) để
                    # chẩn đoán, thay vì chỉ "400 Bad Request" chung chung.
                    body = (r.text or "")[:1000]
                    raise RuntimeError(
                        f"OpenRouter HTTP {r.status_code}: {body}")
                data = r.json()
                choice = data["choices"][0]["message"]
                usage = data.get("usage", {}) or {}
                calls = []
                for tc in choice.get("tool_calls") or []:
                    try:
                        args = json.loads(tc["function"].get("arguments") or "{}")
                    except Exception:
                        args = {}
                    calls.append(ToolCall(
                        tc["function"]["name"], args, id=tc.get("id")))
                return ProviderResult(
                    tool_calls=calls, text=choice.get("content") or "",
                    usage={"prompt_tokens": usage.get("prompt_tokens"),
                           "completion_tokens": usage.get("completion_tokens"),
                           # cost có thể None nếu provider không trả về:
                           # pipeline KHÔNG được coi None là 0.
                           "cost_usd": usage.get("cost"),
                           "http_attempts": http_attempts})
            except RuntimeError:
                raise
            except Exception as e:  # timeout / 5xx / 429: retry có giới hạn
                last_err = e
                if attempt < SETTINGS.llm_max_retries:
                    time.sleep(1.0 * (attempt + 1))
        raise RuntimeError(f"OpenRouter call thất bại sau retry: {last_err}")


def make_provider():
    """Factory: có key + model thì OpenRouter, ngược lại Fake (offline).

    KHÔNG fallback im lặng: nếu cấu hình yêu cầu openrouter nhưng thiếu
    key, raise để API báo misconfigured và turns trả lỗi rõ ràng, thay
    vì chạy Fake mà người dùng tưởng Luna đang trả lời.
    """
    from .fake import FakeProvider

    if SETTINGS.llm_provider == "openrouter":
        if not SETTINGS.openrouter_api_key:
            raise RuntimeError(
                "LLM_PROVIDER=openrouter nhưng OPENROUTER_API_KEY chưa "
                "cấu hình — không fallback sang FakeProvider")
        return OpenRouterProvider()
    return FakeProvider()
