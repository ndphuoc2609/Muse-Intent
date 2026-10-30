"""Pipeline: model vs engine (§3, §5, §12, §13).

Luồng một lượt (không giữ khóa DB khi chờ model):
1. Xác minh binding (persona/post/thread/actor) — server quyết scope.
2. Idempotency: cùng key + cùng nội dung -> trả kết quả cũ, không gửi lại.
3. Tool loop với provider (planner là tool đầu tiên, không thêm 1 LLM
   call riêng: lý do latency + chi phí — planner vẫn là bước tường minh
   trước mọi retrieval, xem README).
4. Validator -> repair tối đa N vòng -> hết vòng thì review, không thay
   bằng câu chung chung sai ý khách.
5. Lưu story/addendum/customer fact TRƯỚC khi gửi (đã lưu trong tool
   loop); chỉ ghi delivery + told_in_message_id khi FakeSender sent.
6. Cập nhật QuestionState: chỉ ANSWERED khi lượt này trả lời đúng câu hỏi.

Mọi số vòng, budget, timeout đều có giới hạn rõ ràng trong config.
"""
from __future__ import annotations

import json
import time
import uuid

import jsonschema
from sqlalchemy import select

from ..config import SETTINGS
from ..models import (Actor, Delivery, Message, Persona, PersonaStory,
                      QuestionState, Thread)
from ..schemas import FinalReply, Planner, TurnRequest, TurnResponse
from .fake_sender import FakeSender
from .planner_normalize import normalize_planner_args
from .tool_schemas import TOOL_SCHEMAS, TOOLS
from .tools import ToolExecutor
from .validator import validate


def _assign_ids(tool_calls):
    """Gán tool_call_id nếu provider không trả (Fake)."""
    for tc in tool_calls:
        if not getattr(tc, "id", None):
            tc.id = "call_" + uuid.uuid4().hex[:16]
    return tool_calls


def _append_assistant(messages, result):
    """Assistant message BẮT BUỘC đứng trước mọi tool message theo chuẩn
    OpenAI: thiếu nó, OpenRouter trả 400 ở lần gọi thứ 2 trở đi (đúng
    lỗi trong trace cậu gửi)."""
    messages.append({
        "role": "assistant",
        "content": result.text or "",
        "tool_calls": [{
            "id": tc.id, "type": "function",
            "function": {"name": tc.name,
                         "arguments": json.dumps(tc.arguments,
                                                 ensure_ascii=False)},
        } for tc in result.tool_calls],
    })


def _append_tool(messages, tc, payload):
    messages.append({
        "role": "tool", "tool_call_id": tc.id,
        "content": json.dumps(payload, ensure_ascii=False),
    })


def _context_block(persona, thread, history, pending_qs, customer_facts):
    lines = [
        f"Persona: {persona.display_name}, {persona.age or ''} tuổi, "
        f"khu vực {persona.region}. Phong cách: {persona.style}",
        f"Bài viết: {thread.post_text or '(không có nội dung bài viết)'}",
        f"Xe trong bài viết: {', '.join(thread.post_vehicle_models) or '(chưa rõ)'}",
        "Câu hỏi đang chờ: " + (", ".join(
            f"{q.question_key} ({q.question_text})" for q in pending_qs)
            or "(không có)"),
        "Customer facts hiện tại: " + (", ".join(
            f"{f.subject}:{f.predicate}={f.value_normalized}"
            for f in customer_facts) or "(không có)"),
        "Lịch sử gần đây:",
    ]
    for m in history[-6:]:
        lines.append(f"- {m.role}: {m.text}")
    return "\n".join(lines)


def _validate_tool_args(name, args):
    schema = TOOL_SCHEMAS.get(name)
    if schema is None:
        return f"tool không có schema: {name}"
    try:
        jsonschema.validate(args, schema)
    except jsonschema.ValidationError as e:
        return f"schema error: {e.message}"
    return None


class Pipeline:
    def __init__(self, session, provider, sender: FakeSender | None = None):
        self.s = session
        self.provider = provider
        self.sender = sender or FakeSender(
            fail_next=SETTINGS.fake_sender_fail_next)

    def run(self, req: TurnRequest, idempotency_key: str | None = None) -> TurnResponse:
        started = time.time()
        # --- 1. Binding: persona phải tồn tại; thread thuộc persona đó.
        persona = self.s.get(Persona, req.persona_id)
        if persona is None:
            return TurnResponse(status="error", thread_id="",
                                reply_text=None,
                                validator_errors=["persona_not_found"])
        thread = None
        if req.thread_id:
            thread = self.s.get(Thread, req.thread_id)
            if thread is None or thread.persona_id != req.persona_id:
                return TurnResponse(status="error", thread_id="",
                                    reply_text=None,
                                    validator_errors=["thread_binding_mismatch"])
        if thread is None:
            thread = Thread(persona_id=req.persona_id, post_id=req.post_id,
                            post_text=req.post_text,
                            post_vehicle_models=req.post_vehicle_models)
            self.s.add(thread)
            self.s.commit()
        actor = self.s.get(Actor, req.actor_id)
        if actor is None:
            actor = Actor(id=req.actor_id, display_name=req.actor_name)
            self.s.add(actor)
            self.s.commit()
        # --- 2. Idempotency (§13).
        if idempotency_key:
            prev = self.s.execute(
                select(Message).where(
                    Message.idempotency_key == idempotency_key)).scalars().first()
            if prev is not None:
                if prev.status == "sent":
                    return TurnResponse(
                        status="sent", thread_id=thread.id,
                        message_id=prev.id, reply_text=prev.text,
                        trace={**prev.trace, "idempotent_replay": True})
                if prev.status == "error":
                    # Retry cùng key cho lần gửi lỗi: gửi lại đúng draft cũ,
                    # không chạy lại model, không tạo story mới.
                    result = self.sender.send(prev.id, prev.text)
                    if result["status"] == "sent":
                        prev.status = "sent"
                        old_del = self.s.execute(
                            select(Delivery).where(
                                Delivery.message_id == prev.id)).scalars().first()
                        if old_del is not None:
                            old_del.status = "sent"
                            old_del.error = None
                        else:
                            self.s.add(Delivery(message_id=prev.id,
                                                sender="fake", status="sent"))
                        self.s.commit()
                        return TurnResponse(status="sent", thread_id=thread.id,
                                            message_id=prev.id,
                                            reply_text=prev.text,
                                            trace={**prev.trace,
                                                   "recovered": True})
                    return TurnResponse(status="error", thread_id=thread.id,
                                        message_id=prev.id,
                                        reply_text=prev.text,
                                        trace={**prev.trace,
                                               "recovery_failed": True})
                # draft/review/blocked: trả lại nguyên trạng, không gửi lặp.
                return TurnResponse(status=prev.status, thread_id=thread.id,  # type: ignore
                                    message_id=prev.id, reply_text=prev.text,
                                    trace={**prev.trace,
                                           "idempotent_replay": True})
        # Lưu message khách trước (message_id cho evidence).
        user_msg = Message(thread_id=thread.id, actor_id=req.actor_id,
                           role="user", text=req.text, status="sent")
        self.s.add(user_msg)
        self.s.commit()
        # Lịch sử: 20 message GẦN NHẤT, không phải 20 message đầu tiên
        # (query cũ order ASC + limit 20 sẽ mất hẳn lịch sử gần đây khi
        # thread dài — model không còn thấy lượt vừa rồi để trả lời tiếp).
        history = list(reversed(self.s.execute(
            select(Message).where(Message.thread_id == thread.id)
            .order_by(Message.created_at.desc()).limit(20)).scalars().all()))
        pending_qs = self.s.execute(
            select(QuestionState).where(
                QuestionState.thread_id == thread.id,
                QuestionState.actor_id == req.actor_id,
                QuestionState.status == "pending")).scalars().all()
        from ..models import CustomerFact
        cust_facts = self.s.execute(
            select(CustomerFact).where(
                CustomerFact.thread_id == thread.id,
                CustomerFact.actor_id == req.actor_id,
                CustomerFact.status == "current")).scalars().all()
        executor = ToolExecutor(self.s, req.persona_id, thread.id, req.actor_id)
        # Lịch sử phải là message thật (user/assistant), không chỉ là text
        # nhét trong system: câu trả lời ngắn, lược bỏ chủ ngữ như
        # "Tiết kiệm thôi" chỉ hiểu được khi model thấy câu hỏi ngay
        # trước đó ở đúng vị trí hội thoại. Trước đây pipeline chỉ gửi
        # [system, user], nên model dễ coi lượt mới là độc lập và hỏi
        # lại đúng câu vừa hỏi ở lượt trước.
        # Giữ nguyên block system (FakeProvider vẫn parse pending question
        # và xe trong bài từ đó), chỉ thêm history dạng message thật.
        messages: list[dict] = [
            {"role": "system",
             "content": _context_block(persona, thread, history, pending_qs,
                                       cust_facts)},
        ]
        for m in history:
            if m.id == user_msg.id:
                continue
            if m.role == "user":
                messages.append({"role": "user", "content": m.text})
            elif m.role == "persona":
                messages.append({"role": "assistant", "content": m.text})
        messages.append({"role": "user", "content": req.text})
        tool_calls_log: list[dict] = []
        planner: Planner | None = None
        final: dict | None = None
        stop_reason: str | None = None
        usage: dict = {}
        llm_calls = 0
        cost = 0.0
        # --- 3. Tool loop (không giữ transaction khi chờ model).
        for _round in range(SETTINGS.max_tool_rounds):
            if llm_calls >= SETTINGS.max_llm_calls_per_turn:
                stop_reason = "budget_llm_calls_exceeded"
                break
            llm_calls += 1
            try:
                result = self.provider.chat(messages, TOOLS)
            except Exception as e:
                stop_reason = f"provider_error: {e}"
                break
            if result.usage.get("cost_usd"):
                cost += float(result.usage["cost_usd"] or 0)
            if cost > SETTINGS.max_cost_usd_per_turn:
                stop_reason = "budget_cost_exceeded"
                break
            if not result.tool_calls:
                # Provider trả text thuần: coi như final không claim, để
                # validator kiểm chứng (không parse regex từ text).
                final = {"text": result.text, "claims": [],
                         "goal": "answer_question"}
                break
            _assign_ids(result.tool_calls)
            _append_assistant(messages, result)
            for tc in result.tool_calls:
                tool_calls_log.append({"tool": tc.name, "arguments": tc.arguments})
                if tc.name == "plan_turn":
                    # Normalize TRƯỚC khi validate schema: Luna có thể trả
                    # goal tự do, source="user", type="question"... (trace
                    # 30/10). Nếu validate raw trước, plan sẽ bị chặn
                    # luôn và normalize không bao giờ được chạy.
                    normalized = normalize_planner_args(tc.arguments)
                    err = _validate_tool_args(tc.name, normalized)
                    if err:
                        _append_tool(messages, tc, {
                            "tool": tc.name, "status": "UNKNOWN",
                            "reason": err})
                        continue
                    try:
                        planner = Planner(**normalized)
                    except Exception as e:
                        _append_tool(messages, tc, {
                            "tool": tc.name, "status": "UNKNOWN",
                            "reason": f"planner schema: {e}"})
                        continue
                    _append_tool(messages, tc, {
                        "tool": tc.name, "status": "FOUND",
                        "data": planner.model_dump()})
                    continue
                err = _validate_tool_args(tc.name, tc.arguments)
                if err:
                    _append_tool(messages, tc, {
                        "tool": tc.name, "status": "UNKNOWN", "reason": err})
                    continue
                if tc.name == "submit_final_reply":
                    try:
                        final = FinalReply(**tc.arguments).model_dump()
                    except Exception as e:
                        _append_tool(messages, tc, {
                            "tool": tc.name, "status": "UNKNOWN",
                            "reason": f"final schema: {e}"})
                        continue
                    # Đóng chuỗi tool call cho final để vòng repair sau
                    # đó (nếu có) không gửi history dở dang lên OpenRouter.
                    _append_tool(messages, tc, {
                        "tool": tc.name, "status": "FOUND"})
                    break
                if tc.name == "stop_or_insufficient":
                    stop_reason = tc.arguments.get("reason", "insufficient")
                    final = {"text": tc.arguments.get("draft_text") or
                             "Mình chưa đủ dữ kiện để trả lời chắc chắn phần này.",
                             "claims": [], "goal": "answer_question"}
                    _append_tool(messages, tc, {
                        "tool": tc.name, "status": "FOUND"})
                    break
                # Tool thật: mở transaction ngắn trong executor, commit xong
                # mới trả kết quả cho model (không giữ khóa khi chờ model).
                tr = executor.execute(tc.name, tc.arguments)
                _append_tool(messages, tc, tr.model_dump())
            if final is not None or stop_reason:
                break
        if planner is None:
            planner = Planner(intents=[], goal="answer_question")
        # Customer fact gắn với thread/actor/message qua evidence + thread;
        # message_id của lượt khách đã lưu ở user_msg (dùng cho audit sau).
        trace_base = {
            "planner": planner.model_dump(),
            "tool_calls": tool_calls_log,
            "saved_customer_facts": executor.saved_customer_facts,
            "saved_stories": executor.saved_stories,
            "llm_calls": llm_calls, "cost_usd": round(cost, 6),
            "latency_s": round(time.time() - started, 3),
            "provider": type(self.provider).__name__,
        }
        if final is None:
            msg = Message(thread_id=thread.id, role="persona", text="",
                          status="error", idempotency_key=idempotency_key,
                          trace={**trace_base, "error": stop_reason})
            self.s.add(msg)
            self.s.commit()
            return TurnResponse(status="error", thread_id=thread.id,
                                message_id=msg.id, reply_text=None,
                                planner=planner.model_dump(),
                                tool_calls=tool_calls_log,
                                saved_customer_facts=executor.saved_customer_facts,
                                saved_stories=executor.saved_stories,
                                validator_errors=[stop_reason or "no_final"],
                                trace=trace_base)
        # --- 4. Validator + repair loop.
        opted_out = planner.goal == "stop" or any(
            i.type == "opt_out" for i in planner.intents)
        asked_ai = any(i.type == "ai_identity" for i in planner.intents)
        refused = [q.question_key for q in pending_qs
                   if q.status == "declined"]
        validator_errors: list[str] = []
        for _repair in range(SETTINGS.max_validator_repairs + 1):
            validator_errors = validate(final, {
                "retrieved": executor.retrieved, "opted_out": opted_out,
                "asked_ai_identity": asked_ai, "refused_keys": refused})
            if not validator_errors:
                break
            if _repair == SETTINGS.max_validator_repairs:
                break
            messages.append({"role": "user", "content":
                "VALIDATOR_FEEDBACK: " + "; ".join(validator_errors) +
                " Hãy sửa reply theo đúng lỗi trên, không thêm claim mới."})
            try:
                result = self.provider.chat(messages, TOOLS)
                llm_calls += 1
            except Exception as e:
                validator_errors.append(f"provider_error_on_repair: {e}")
                break
            repaired = None
            if result.tool_calls:
                # Đóng chuỗi tool call cho vòng repair, nếu không lần
                # repair tiếp theo sẽ lại gửi history dở dang -> 400.
                _assign_ids(result.tool_calls)
                _append_assistant(messages, result)
                for tc in result.tool_calls:
                    if tc.name == "submit_final_reply":
                        repaired = tc.arguments
                    _append_tool(messages, tc, {
                        "tool": tc.name, "status": "FOUND"})
            if repaired is not None:
                final = repaired
            elif result.text:
                final = {"text": result.text, "claims": [],
                         "goal": final.get("goal", "answer_question")}
            else:
                break
        goal = final.get("goal", planner.goal)
        # --- 5. Cập nhật QuestionState.
        self._update_questions(thread.id, req.actor_id, planner, user_msg.id,
                               pending_qs)
        # Followup mới (nếu có) -> tạo QuestionState pending, trừ khi đã có.
        # Model có thể trả followup_question mà quên followup_key: trước
        # đây cả câu hỏi bị rớt luôn, lượt sau context ghi "(không có)"
        # nên model không biết mình vừa hỏi gì và hỏi lại. Gán key chung
        # thay vì rớt câu hỏi.
        if final.get("followup_question") and not final.get("followup_key"):
            final["followup_key"] = "followup_question"
        if final.get("followup_question") and final.get("followup_key") \
                and not opted_out and goal != "handoff":
            exists = self.s.execute(
                select(QuestionState).where(
                    QuestionState.thread_id == thread.id,
                    QuestionState.actor_id == req.actor_id,
                    QuestionState.question_key == final["followup_key"],
                    QuestionState.status == "pending")).scalars().first()
            if not exists:
                self.s.add(QuestionState(
                    thread_id=thread.id, actor_id=req.actor_id,
                    question_key=final["followup_key"],
                    question_text=final["followup_question"]))
                self.s.commit()
        # --- 6. Delivery theo goal/status.
        reply_text = final.get("text", "")
        if validator_errors:
            msg = Message(thread_id=thread.id, role="persona", text=reply_text,
                          status="review", idempotency_key=idempotency_key,
                          trace={**trace_base,
                                 "validator_errors": validator_errors})
            self.s.add(msg)
            self.s.commit()
            return TurnResponse(status="review", thread_id=thread.id,
                                message_id=msg.id, reply_text=reply_text,
                                planner=planner.model_dump(),
                                tool_calls=tool_calls_log,
                                saved_customer_facts=executor.saved_customer_facts,
                                saved_stories=executor.saved_stories,
                                validator_errors=validator_errors,
                                trace=trace_base)
        if goal == "handoff":
            msg = Message(thread_id=thread.id, role="persona", text=reply_text,
                          status="review", idempotency_key=idempotency_key,
                          trace={**trace_base, "handoff": True})
            self.s.add(msg)
            self.s.commit()
            return TurnResponse(status="review", thread_id=thread.id,
                                message_id=msg.id, reply_text=reply_text,
                                planner=planner.model_dump(),
                                tool_calls=tool_calls_log,
                                saved_customer_facts=executor.saved_customer_facts,
                                saved_stories=executor.saved_stories,
                                trace=trace_base)
        msg = Message(thread_id=thread.id, role="persona", text=reply_text,
                      status="draft", idempotency_key=idempotency_key,
                      trace=trace_base)
        self.s.add(msg)
        self.s.commit()
        result = self.sender.send(msg.id, reply_text)
        if result["status"] == "sent":
            msg.status = "sent"
            self.s.add(Delivery(message_id=msg.id, sender="fake",
                                status="sent"))
            # Chỉ khi gửi thành công mới đánh dấu story đã kể.
            for st in executor.saved_stories:
                row = self.s.get(PersonaStory, st["event_id"])
                if row and not row.told_in_message_id:
                    row.told_in_message_id = msg.id
            self.s.commit()
            return TurnResponse(status="sent", thread_id=thread.id,
                                message_id=msg.id, reply_text=reply_text,
                                planner=planner.model_dump(),
                                tool_calls=tool_calls_log,
                                saved_customer_facts=executor.saved_customer_facts,
                                saved_stories=executor.saved_stories,
                                trace=trace_base)
        msg.status = "error"
        self.s.add(Delivery(message_id=msg.id, sender="fake",
                            status="failed", error=result.get("error")))
        self.s.commit()
        return TurnResponse(status="error", thread_id=thread.id,
                            message_id=msg.id, reply_text=reply_text,
                            planner=planner.model_dump(),
                            tool_calls=tool_calls_log,
                            saved_customer_facts=executor.saved_customer_facts,
                            saved_stories=executor.saved_stories,
                            validator_errors=["fake_sender_failed"],
                            trace=trace_base)

    def _update_questions(self, thread_id, actor_id, planner, user_msg_id,
                          pending_qs):
        # Chỉ ANSWERED khi planner nêu answered_question_keys; còn lại,
        # nếu lượt này nói sang chủ đề khác thì redirected, không xóa.
        answered = set(planner.answered_question_keys or [])
        # Fake/LLM không nêu key nhưng intent trả lời đúng chủ đề: engine
        # không tự đoán từ keyword đơn lẻ (§3) — chỉ dùng key planner nêu.
        for q in pending_qs:
            if q.question_key in answered:
                q.status = "answered"
                q.answered_by_message_id = user_msg_id
                q.answer_field = q.question_key
            else:
                # Lượt này có nội dung (không phải opt-out) nhưng không
                # trả lời câu hỏi cũ -> redirected, giữ lại để model quyết
                # định có quay lại hay không ở lượt sau, không hỏi dồn.
                if planner.goal != "stop":
                    q.status = "redirected"
        self.s.commit()
