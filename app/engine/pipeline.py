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

import hashlib
import json
import time
import uuid

import jsonschema
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..config import SETTINGS
from ..models import (Actor, Delivery, Message, Persona, PersonaProfile,
                      PersonaPerspective, PersonaStory, PersonaStyleGuide,
                      QuestionState, StoryTold, Thread)
from .typed_values import (find_money, parse_dates, parse_distance_km,
                           parse_percent)
from ..schemas import FinalReply, Planner, TurnRequest, TurnResponse
from .fake_sender import FakeSender
from .planner_normalize import guard_resolved_reference, normalize_planner_args
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


def _norm_text(t: str) -> str:
    return " ".join((t or "").split())


def _request_hash(thread_id: str, actor_id: str, text: str) -> str:
    return hashlib.sha256(
        f"{thread_id}|{actor_id}|{_norm_text(text)}".encode("utf-8")
    ).hexdigest()[:32]


def _context_block(persona, thread, history, pending_qs, customer_facts,
                   actor=None, declined_qs=None):
    lines = [
        f"Persona: {persona.display_name}, {persona.age or ''} tuổi, "
        f"khu vực {persona.region}. Phong cách: {persona.style}",
        f"Bài viết: {thread.post_text or '(không có nội dung bài viết)'}",
        f"Xe trong bài viết: {', '.join(thread.post_vehicle_models) or '(chưa rõ)'}",
        f"Actor hiện tại: {actor.id if actor else '(không rõ)'} "
        f"({actor.display_name if actor else ''})",
        "Câu hỏi đang chờ của actor hiện tại: " + (", ".join(
            f"{q.question_key} ({q.question_text})" for q in pending_qs)
            or "(không có)"),
        "Câu hỏi actor hiện tại đã từ chối (KHÔNG hỏi lại): " + (", ".join(
            f"{q.question_key} ({q.question_text})" for q in (declined_qs or []))
            or "(không có)"),
        "Customer facts RIÊNG của actor hiện tại (không gán cho actor khác): "
        + (", ".join(
            f"{f.subject}:{f.predicate}={f.value_normalized}"
            for f in customer_facts) or "(không có)"),
        "Lịch sử chung của thread (công khai, có thể có lời của actor khác "
        "— không gán lời người khác thành thông tin của actor hiện tại):",
    ]
    for m in history[-6:]:
        who = m.actor_id or "persona"
        lines.append(f"- {m.role} [{who}]: {m.text}")
    return "\n".join(lines)


def _is_pure_social(text: str) -> bool:
    """Text thuần chỉ được chấp nhận làm final khi là xã giao thuần túy:
    không tiền/ngày/%/km, không màu xe, không câu hỏi, không cam kết
    giao dịch, không xin thông tin riêng. Mọi thứ khác phải qua
    submit_final_reply để validator kiểm chứng claim."""
    t = (text or "").strip()
    if not t or len(t) > 200 or "?" in t:
        return False
    low = t.lower()
    if any(p in low for p in ("đã đặt cọc", "đã giữ xe", "đã chốt xe",
                              "nhân viên sẽ gọi", "số điện thoại",
                              "địa chỉ nhà", "màu ")):
        return False
    if find_money(t) or parse_percent(t) or parse_distance_km(t) \
            or parse_dates(t):
        return False
    return True


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
        # --- 2. Idempotency (§13, scoped qua bảng turn_idempotency):
        # key chỉ có nghĩa trong (thread, actor, payload hash). Cùng key +
        # cùng payload -> replay. Cùng key + khác payload/scope -> conflict
        # rõ ràng, KHÔNG trả lại reply cũ im lặng. Message.idempotency_key
        # là legacy unique toàn cục nên các lượt mới để NULL, tránh
        # user+persona cùng key vi phạm unique và key trùng ở thread khác.
        reuse_user_msg = None
        idem_rec = None
        if idempotency_key:
            from ..models import TurnIdempotency as _TI
            req_hash = _request_hash(thread.id, req.actor_id, req.text)
            idem_rec = self.s.execute(
                select(_TI).where(
                    _TI.thread_id == thread.id,
                    _TI.actor_id == req.actor_id,
                    _TI.idempotency_key == idempotency_key)).scalars().first()
            if idem_rec is not None:
                if idem_rec.request_hash != req_hash:
                    return TurnResponse(
                        status="error", thread_id=thread.id,
                        reply_text=None,
                        validator_errors=[
                            "idempotency_conflict: Idempotency-Key đã dùng "
                            "cho nội dung khác trong cùng thread/actor; "
                            "dùng key mới cho lượt mới, hoặc gửi lại đúng "
                            "payload cũ"],
                        trace={"idempotency_conflict": True})
                if idem_rec.persona_message_id:
                    prev = self.s.get(Message, idem_rec.persona_message_id)
                    if prev is not None:
                        if prev.status == "sent":
                            return TurnResponse(
                                status="sent", thread_id=thread.id,
                                message_id=prev.id, reply_text=prev.text,
                                trace={**prev.trace, "idempotent_replay": True})
                        if prev.status == "error":
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
                                idem_rec.status = "sent"
                                self.s.commit()
                                return TurnResponse(
                                    status="sent", thread_id=thread.id,
                                    message_id=prev.id, reply_text=prev.text,
                                    trace={**prev.trace, "recovered": True})
                            return TurnResponse(
                                status="error", thread_id=thread.id,
                                message_id=prev.id, reply_text=prev.text,
                                trace={**prev.trace, "recovery_failed": True})
                        return TurnResponse(
                            status=prev.status, thread_id=thread.id,
                            message_id=prev.id, reply_text=prev.text,
                            trace={**prev.trace, "idempotent_replay": True})
                elif idem_rec.user_message_id:
                    reuse_user_msg = self.s.get(Message, idem_rec.user_message_id)
            else:
                # Key ở thread/actor khác không ảnh hưởng scope này:
                # không lookup toàn cục nữa.
                pass
        # Lưu message khách trước (message_id cho evidence).
        if reuse_user_msg is not None:
            user_msg = reuse_user_msg
        else:
            user_msg = Message(thread_id=thread.id, actor_id=req.actor_id,
                               role="user", text=req.text, status="sent")
            self.s.add(user_msg)
            self.s.commit()
            if idempotency_key:
                from ..models import TurnIdempotency as _TI2
                if idem_rec is None:
                    idem_rec = _TI2(
                        thread_id=thread.id, actor_id=req.actor_id,
                        idempotency_key=idempotency_key,
                        request_hash=_request_hash(thread.id, req.actor_id,
                                                   req.text),
                        user_message_id=user_msg.id, status="in_progress")
                    self.s.add(idem_rec)
                    try:
                        self.s.commit()
                    except IntegrityError:
                        # Race: request khác cùng scope+key đã claim trước.
                        self.s.rollback()
                        return TurnResponse(
                            status="error", thread_id=thread.id,
                            reply_text=None,
                            validator_errors=[
                                "idempotency_conflict: hai request cùng key "
                                "chạy đồng thời; gửi lại cùng key để nhận "
                                "replay, hoặc dùng key mới"],
                            trace={"idempotency_conflict": True})
                else:
                    idem_rec.user_message_id = user_msg.id
                    self.s.commit()
        def _idem_update(msg):
            if idempotency_key and idem_rec is not None:
                try:
                    idem_rec.persona_message_id = msg.id
                    idem_rec.status = msg.status
                    self.s.commit()
                except Exception:
                    self.s.rollback()
        # Lịch sử: 20 message GẦN NHẤT, không phải 20 message đầu tiên
        # (query cũ order ASC + limit 20 sẽ mất hẳn lịch sử gần đây khi
        # thread dài — model không còn thấy lượt vừa rồi để trả lời tiếp).
        history = list(reversed(self.s.execute(
            select(Message).where(Message.thread_id == thread.id)
            .order_by(Message.created_at.desc()).limit(20)).scalars().all()))
        # Load cả pending và declined: query cũ chỉ lấy pending rồi
        # kiểm tra status==declined trong danh sách pending nên
        # refused_keys luôn rỗng — câu từ chối bị mất khỏi context.
        all_qs = self.s.execute(
            select(QuestionState).where(
                QuestionState.thread_id == thread.id,
                QuestionState.actor_id == req.actor_id)).scalars().all()
        pending_qs = [q for q in all_qs if q.status == "pending"]
        declined_qs = [q for q in all_qs if q.status == "declined"]
        declined_keys = [q.question_key for q in declined_qs]
        from ..models import CustomerFact
        cust_facts = self.s.execute(
            select(CustomerFact).where(
                CustomerFact.thread_id == thread.id,
                CustomerFact.actor_id == req.actor_id,
                CustomerFact.status == "current")).scalars().all()
        executor = ToolExecutor(self.s, req.persona_id, thread.id, req.actor_id,
                                current_message_id=user_msg.id,
                                current_user_text=req.text)
        # Lịch sử phải là message thật (user/assistant), không chỉ là text
        # nhét trong system: câu trả lời ngắn, lược bỏ chủ ngữ như
        # "Tiết kiệm thôi" chỉ hiểu được khi model thấy câu hỏi ngay
        # trước đó ở đúng vị trí hội thoại. Trước đây pipeline chỉ gửi
        # [system, user], nên model dễ coi lượt mới là độc lập và hỏi
        # lại đúng câu vừa hỏi ở lượt trước.
        # Giữ nguyên block system (FakeProvider vẫn parse pending question
        # và xe trong bài từ đó), chỉ thêm history dạng message thật.
        # Runtime prompt v1 (§10): PERSONA_CORE + STYLE_GUIDE + policy,
        # cộng thêm _context_block cũ để Fake vẫn parse được pending
        # question / xe trong bài từ system block.
        _profile = self.s.get(PersonaProfile, req.persona_id)
        _persps = self.s.execute(
            select(PersonaPerspective).where(
                PersonaPerspective.persona_id == req.persona_id,
                PersonaPerspective.status == "active")).scalars().all()
        _style = self.s.get(PersonaStyleGuide, req.persona_id)
        from .runtime_prompt import build_runtime_prompt as _build_rp
        _cust_state_text = (
            "Explicit: " + ", ".join(
                f"{f.subject}:{f.predicate}={f.value_normalized}"
                for f in cust_facts) or "(không có)") + (
            f"\nCâu hỏi đã từ chối: {', '.join(declined_keys) or '(không có)'}"
            "\nOpt-out: (xác định sau planner)")
        _rp = _build_rp(persona, _profile, list(_persps), _style, [],
                        [], [], _cust_state_text,
                        _context_block(persona, thread, history, pending_qs,
                                       cust_facts, actor=actor,
                                       declined_qs=declined_qs))
        messages: list[dict] = [
            {"role": "system", "content": _rp["text"]},
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
        trace_retrieval: list[dict] = []
        planner: Planner | None = None
        final: dict | None = None
        stop_reason: str | None = None
        usage: dict = {}
        llm_calls = 0
        cost = 0.0
        cost_unknown_calls = 0
        http_attempts = 0
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
            # Cost: không coi cost provider không trả về là 0. Ghi rõ
            # known/unknown; budget tiền chỉ áp khi cost đã biết, còn
            # budget số call và số HTTP attempt luôn áp.
            if result.usage.get("cost_usd") is not None:
                cost += float(result.usage["cost_usd"] or 0)
            else:
                cost_unknown_calls += 1
            if result.usage.get("http_attempts"):
                http_attempts += int(result.usage["http_attempts"])
            if cost > SETTINGS.max_cost_usd_per_turn:
                stop_reason = "budget_cost_exceeded"
                break
            if http_attempts > SETTINGS.max_llm_calls_per_turn * (
                    SETTINGS.llm_max_retries + 1):
                stop_reason = "budget_http_attempts_exceeded"
                break
            if not result.tool_calls:
                # Protocol: text thuần không qua submit_final_reply thì
                # KHÔNG được coi là final có claim. Chỉ chấp nhận khi
                # là xã giao thuần túy (không dữ kiện, không câu hỏi,
                # không cam kết) — còn lại là lỗi protocol, không gửi.
                if _is_pure_social(result.text or ""):
                    final = {"text": result.text, "claims": [],
                             "goal": "answer_question"}
                else:
                    stop_reason = ("protocol_plain_text_no_tool_call: "
                                   "provider trả text thuần không qua "
                                   "submit_final_reply, có thể chứa dữ kiện "
                                   "chưa kiểm chứng")
                break
            _assign_ids(result.tool_calls)
            _append_assistant(messages, result)
            for tc in result.tool_calls:
                tool_calls_log.append({"tool": tc.name, "arguments": tc.arguments})
                if planner is None and tc.name != "plan_turn":
                    # Protocol: plan_turn phải là tool đầu tiên. Mutation
                    # hoặc final trước plan đều bị chặn, không thực thi.
                    _append_tool(messages, tc, {
                        "tool": tc.name, "status": "UNKNOWN",
                        "reason": "protocol_plan_turn_first: plan_turn chưa "
                                  "được gọi, tool này không được thực thi"})
                    continue
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
                    # Engine guard cho tham chiếu mơ hồ: planner do model
                    # đưa ra được chấp nhận nguyên văn trước đây, nên
                    # "1 chiếc" bị resolve theo xe trong bài (Creta) dù
                    # persona lái Tucson và vừa kể chuyện Tucson. Guard
                    # chạy TRƯỚC khi trả plan tool result, để model thấy
                    # bản đã downgrade thành ambiguous và phải hỏi làm rõ
                    # hoặc nói chung, thay vì khẳng định tên xe sai.
                    try:
                        from ..models import PersonaFact as _PF
                        _veh = self.s.execute(
                            select(_PF).where(
                                _PF.persona_id == req.persona_id,
                                _PF.fact_key == "vehicle_model",
                                _PF.status == "active")).scalars().first()
                        _last_persona = None
                        for _m in reversed(history):
                            if _m.role == "persona" and _m.text:
                                _last_persona = _m.text
                                break
                        _guarded = guard_resolved_reference(
                            planner.model_dump(),
                            _veh.value if _veh else None,
                            req.text, _last_persona)
                        planner = Planner(**_guarded)
                    except Exception:
                        pass
                    _append_tool(messages, tc, {
                        "tool": tc.name, "status": "FOUND",
                        "data": planner.model_dump()})
                    # Server-side retrieval (§7): sau plan, engine truy
                    # xuất story theo tag/entity, ghi log, đưa ref_id
                    # vào executor.retrieved để model claim được, và
                    # cập nhật system message đầu (không thêm system
                    # message mới làm Fake mất context cũ).
                    if not getattr(self, "_server_retrieval_done", False):
                        self._server_retrieval_done = True
                        try:
                            from .retrieval import (
                                derive_wanted_tags, log_retrieval,
                                retrieve_stories)
                            _wt, _wc = derive_wanted_tags(
                                req.text, planner)
                            _retell = any(
                                k in req.text.lower()
                                for k in ("kể lại", "lại chuyến", "chuyến đó"))
                            _ev = retrieve_stories(
                                self.s, req.persona_id, thread.id,
                                req.actor_id, query_text=req.text,
                                wanted_tags=_wt, wanted_categories=_wc,
                                retell_requested=_retell, limit=3)
                            if _ev:
                                log_retrieval(self.s, req.persona_id,
                                              thread.id, req.actor_id, _ev)
                                for _d in _ev:
                                    executor.retrieved[_d["event_id"]] = (
                                        "story", _d["summary"] + " " + _d["body"],
                                        {"status": "active"})
                                messages[0]["content"] += (
                                    "\n\nRELEVANT_MEMORIES (server retrieval, "
                                    "chỉ kể khi liên quan):\n" + "\n".join(
                                        f"- [{_d['event_id']}] {_d['summary']} "
                                        f"(tags={_d['tags']}, đã kể: {_d['told_before']}, "
                                        f"lý do: {'; '.join(_d['reasons'])})"
                                        for _d in _ev))
                                trace_retrieval = _ev
                        except Exception:
                            pass
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
            "cost_known": cost_unknown_calls == 0,
            "cost_unknown_calls": cost_unknown_calls,
            "http_attempts": http_attempts,
            "latency_s": round(time.time() - started, 3),
            "provider": type(self.provider).__name__,
            "retrieval_evidence": trace_retrieval,
        }
        if final is None:
            msg = Message(thread_id=thread.id, role="persona", text="",
                          status="error", idempotency_key=None,
                          trace={**trace_base, "error": stop_reason})
            self.s.add(msg)
            self.s.commit()
            executor.reject_pending()
            _idem_update(msg)
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
        refused = declined_keys
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
                if result.usage.get("cost_usd") is not None:
                    cost += float(result.usage["cost_usd"] or 0)
                else:
                    cost_unknown_calls += 1
                if result.usage.get("http_attempts"):
                    http_attempts += int(result.usage["http_attempts"])
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
        # --- 5. Cập nhật QuestionState: chỉ ANSWERED khi planner nêu key.
        # Chuyển chủ đề KHÔNG có nghĩa đã trả lời, và KHÔNG tự đổi
        # pending -> redirected (lỗi cũ làm mất câu hỏi đang chờ).
        self._update_questions(thread.id, req.actor_id, planner, user_msg.id,
                               pending_qs)
        # Followup key fallback (không rớt câu hỏi chỉ vì thiếu key).
        if final.get("followup_question") and not final.get("followup_key"):
            final["followup_key"] = "followup_question"
        # Followup QuestionState CHỈ được tạo khi delivery thành công
        # (xem nhánh sent bên dưới). Lượt review/error chưa từng được
        # gửi cho khách thì không được tính là "đã hỏi".
        # --- 6. Delivery theo goal/status.
        reply_text = final.get("text", "")
        if validator_errors:
            msg = Message(thread_id=thread.id, role="persona", text=reply_text,
                          status="review", idempotency_key=None,
                          trace={**trace_base,
                                 "validator_errors": validator_errors})
            self.s.add(msg)
            self.s.commit()
            executor.reject_pending()
            _idem_update(msg)
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
                          status="review", idempotency_key=None,
                          trace={**trace_base, "handoff": True})
            self.s.add(msg)
            self.s.commit()
            executor.reject_pending()
            _idem_update(msg)
            return TurnResponse(status="review", thread_id=thread.id,
                                message_id=msg.id, reply_text=reply_text,
                                planner=planner.model_dump(),
                                tool_calls=tool_calls_log,
                                saved_customer_facts=executor.saved_customer_facts,
                                saved_stories=executor.saved_stories,
                                trace=trace_base)
        msg = Message(thread_id=thread.id, role="persona", text=reply_text,
                      status="draft", idempotency_key=None,
                      trace=trace_base)
        self.s.add(msg)
        self.s.commit()
        result = self.sender.send(msg.id, reply_text)
        if result["status"] == "sent":
            msg.status = "sent"
            self.s.add(Delivery(message_id=msg.id, sender="fake",
                                status="sent"))
            # Chỉ khi gửi thành công mới activate proposals và đánh dấu
            # story đã kể. Story được truy xuất lại và claim trong final
            # cũng được ghi StoryTold, không chỉ story vừa tạo.
            executor.activate_pending()
            for st in executor.saved_stories:
                row = self.s.get(PersonaStory, st["event_id"])
                if row and not row.told_in_message_id:
                    row.told_in_message_id = msg.id
            for claim in final.get("claims", []) or []:
                if claim.get("kind") == "story" and claim.get("ref_id"):
                    st = self.s.get(PersonaStory, claim["ref_id"])
                    if st is not None:
                        try:
                            self.s.add(StoryTold(
                                event_id=st.event_id,
                                persona_id=req.persona_id,
                                actor_id=req.actor_id,
                                thread_id=thread.id, message_id=msg.id))
                        except IntegrityError:
                            self.s.rollback()
            # Followup chỉ được tạo khi delivery thành công.
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
                        question_text=final["followup_question"],
                        asked_message_id=msg.id))
            self.s.commit()
            _idem_update(msg)
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
        executor.reject_pending()
        _idem_update(msg)
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
        # Chỉ ANSWERED khi planner nêu answered_question_keys. Engine
        # không tự đoán từ keyword đơn lẻ, và không tự đổi pending ->
        # redirected chỉ vì lượt này nói sang chủ đề khác: câu hỏi cũ
        # vẫn pending để model quyết định có quay lại hay không, và
        # không bị mất chỉ vì một lượt chuyển chủ đề.
        answered = set(planner.answered_question_keys or [])
        for q in pending_qs:
            if q.question_key in answered:
                q.status = "answered"
                q.answered_by_message_id = user_msg_id
                q.answer_field = q.question_key
        self.s.commit()
