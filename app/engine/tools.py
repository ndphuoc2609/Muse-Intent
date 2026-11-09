"""Tool execution ở engine (§7). Server xác định scope từ binding đã
xác minh: persona_id/thread/actor do pipeline truyền vào, model không
được tự chọn. Mọi lỗi DB/tool trả UNKNOWN, không phải NOT_FOUND.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from ..models import (CustomerFact, PersonaFact, PersonaStory,
                      ProductFact, StoryRevision)
from ..schemas import ToolResult
from .typed_values import is_plausible_vn_mobile


def _story_retrieved_value(summary, body, addenda=()):
    """Giá trị story để validator đối chiếu: ghép summary/body/addenda,
    bỏ phần rỗng và phần trùng nguyên văn. Story seed từ memory thường
    có summary == body — ghép cả hai sẽ nhân đôi câu, làm so khớp chính
    xác ở validator lệch (live 2026-11-04: mem_1)."""
    parts = []
    for p in [summary, body, *addenda]:
        p = (p or "").strip()
        if p and p not in parts:
            parts.append(p)
    return " ".join(parts)


def _today() -> str:
    return date.today().isoformat()


def canonical_view(session, story: PersonaStory) -> dict:
    revs = session.execute(
        select(StoryRevision).where(
            StoryRevision.event_id == story.event_id,
            StoryRevision.status == "active")
        .order_by(StoryRevision.version)).scalars().all()
    from ..models import TagAssignment as _TA
    tags = session.execute(
        select(_TA).where(
            _TA.entity_type == "story", _TA.entity_id == story.event_id,
            _TA.status == "active")).scalars().all()
    return {
        "event_id": story.event_id, "title": story.title,
        "summary": story.summary, "body": story.body,
        "addenda": [r.content for r in revs],
        "entities": story.entities, "event_type": story.event_type,
        "event_date": story.event_date,
        "event_date_precision": story.event_date_precision,
        "fictional_generated": story.fictional_generated,
        "status": story.status, "version": story.version,
        "tags": [t.tag for t in tags],
        "story_categories": story.story_categories or [],
        "participants": story.participants or [],
        "context": story.context_text,
        "concern_or_choice": story.concern_or_choice,
        "action": story.action_text,
        "outcome": story.outcome_text,
        "feeling_or_lesson": story.feeling_or_lesson,
        "canonical_facts": story.canonical_facts or [],
        "tellable_policy": story.tellable_policy or "on_relevance",
        "related_need_topics": story.related_need_topics or [],
    }


class ToolExecutor:
    def __init__(self, session, persona_id: str, thread_id: str, actor_id: str,
                 current_message_id: str | None = None,
                 current_user_text: str = ""):
        self.s = session
        self.persona_id = persona_id
        self.thread_id = thread_id
        self.actor_id = actor_id
        self.current_message_id = current_message_id
        self.current_user_text = current_user_text
        self.retrieved: dict[str, tuple[str, str, dict]] = {}
        self.saved_customer_facts: list[dict] = []
        self.saved_stories: list[dict] = []
        # Lifecycle (§7): proposals trong lượt này ở trạng thái pending,
        # KHÔNG phải canon. Chỉ activate khi delivery thành công; nếu
        # lượt đi review/error thì reject_pending() để không làm bẩn
        # active memory.
        self.pending_persona_fact_ids: list[str] = []
        self.pending_story_ids: list[str] = []
        self.pending_addendum_ids: list[str] = []
        self.pending_customer_fact_ids: list[str] = []
        self.correction_customer_fact_ids: set[str] = set()
        self.pending_tag_assignment_ids: list[str] = []

    def activate_pending(self, *, commit: bool = True) -> None:
        """Chuyển mọi proposal của lượt này thành canon (chỉ gọi khi sent)."""
        from ..models import CustomerFact as _CF
        for fid in self.pending_persona_fact_ids:
            row = self.s.get(PersonaFact, fid)
            if row is not None and row.status == "pending":
                row.status = "active"
        from ..models import TagAssignment as _TA2
        for tid in self.pending_tag_assignment_ids:
            trow = self.s.get(_TA2, tid)
            if trow is not None and trow.status == "pending":
                trow.status = "active"
        for eid in self.pending_story_ids:
            row = self.s.get(PersonaStory, eid)
            if row is not None and row.status == "pending":
                row.status = "active"
        # Addenda: activate revision rồi mới bump version của story.
        bumped: dict[str, int] = {}
        for rid in self.pending_addendum_ids:
            rev = self.s.get(StoryRevision, rid)
            if rev is not None and rev.status == "pending":
                rev.status = "active"
                bumped[rev.event_id] = bumped.get(rev.event_id, 0) + 1
        for eid, n in bumped.items():
            st = self.s.get(PersonaStory, eid)
            if st is not None:
                st.version += n
        # Customer facts: supersede đúng subject+predicate+relation
        # (bố vs mẹ khác relation thì KHÔNG ghi đè nhau), rồi current.
        # Ngoại lệ duy nhất: fact được model đánh dấu correction=true
        # (khách đính chính: "à không, mua cho mình" sau "mua cho bố")
        # thì supersede mọi fact current cùng predicate của actor đó,
        # dù khác subject/relation — nếu không, DB sẽ có hai
        # intended_user current mâu thuẫn và lượt sau không biết dùng
        # cái nào. Không tự suy correction từ keyword: model phải khai
        # báo rõ qua tool arg, engine chỉ thực thi.
        for fid in self.pending_customer_fact_ids:
            row = self.s.get(_CF, fid)
            if row is None or row.status != "pending":
                continue
            old_q = select(_CF).where(
                _CF.thread_id == self.thread_id,
                _CF.actor_id == self.actor_id,
                _CF.predicate == row.predicate,
                _CF.status == "current")
            is_correction = fid in getattr(
                self, "correction_customer_fact_ids", set())
            if not is_correction:
                old_q = old_q.where(_CF.subject == row.subject)
            for o in self.s.execute(old_q).scalars().all():
                if o.id != row.id and (
                        is_correction
                        or (o.relation or "") == (row.relation or "")):
                    o.status = "corrected"
            row.status = "current"
        if commit:
            self.s.commit()

    def reject_pending(self) -> None:
        """Candidate của lượt không deliver thành công -> rejected,
        không phải active, không còn pending để lượt sau dùng nhầm."""
        for fid in self.pending_persona_fact_ids:
            row = self.s.get(PersonaFact, fid)
            if row is not None and row.status == "pending":
                row.status = "rejected"
        for eid in self.pending_story_ids:
            row = self.s.get(PersonaStory, eid)
            if row is not None and row.status == "pending":
                row.status = "rejected"
        for rid in self.pending_addendum_ids:
            rev = self.s.get(StoryRevision, rid)
            if rev is not None and rev.status == "pending":
                rev.status = "rejected"
        for fid in self.pending_customer_fact_ids:
            from ..models import CustomerFact as _CF
            row = self.s.get(_CF, fid)
            if row is not None and row.status == "pending":
                row.status = "rejected"
        from ..models import TagAssignment as _TA3
        for tid in self.pending_tag_assignment_ids:
            trow = self.s.get(_TA3, tid)
            if trow is not None and trow.status == "pending":
                trow.status = "rejected"
        self.s.commit()

    def _err(self, name, reason):
        return ToolResult(status="UNKNOWN", tool=name, reason=reason)

    def execute(self, name: str, args: dict) -> ToolResult:
        try:
            fn = getattr(self, f"tool_{name}", None)
            if fn is None:
                return ToolResult(status="UNKNOWN", tool=name,
                                  reason=f"tool không hỗ trợ: {name}")
            return fn(args)
        except Exception as e:  # DB/tool lỗi -> UNKNOWN, không bịa NOT_FOUND
            self.s.rollback()
            return self._err(name, f"{type(e).__name__}: {e}")

    # -- retrieval --
    # Synonym map: Luna (live) đoán key theo ngôn ngữ tự nhiên, không
    # biết registry thật (vehicle_model, purchased_at...). Không có map
    # này, model hỏi đúng ý nhưng sai tên key -> NOT_FOUND rỗng -> nó
    # kết luận "không có dữ kiện" và gọi stop_or_insufficient, dù fact
    # đã seed sẵn. Đây là lỗi live 30/10: Luna hỏi xe + thời điểm mua
    # bằng keys vehicle/car/purchase_date... và nhận về [].
    FACT_KEY_SYNONYMS = {
        "vehicle": "vehicle_model", "car": "vehicle_model",
        "current_vehicle": "vehicle_model", "car_model": "vehicle_model",
        "xe_dang_di": "vehicle_model", "xe": "vehicle_model",
        "model": "vehicle_model",
        "vehicle_trim": "vehicle_trim", "trim": "vehicle_trim",
        "ban_xe": "vehicle_trim",
        "vehicle_purchase_date": "purchased_at",
        "purchase_date": "purchased_at", "purchased": "purchased_at",
        "thoi_diem_mua_xe": "purchased_at", "mua_luc_nao": "purchased_at",
        "is_first_car": "is_first_car", "xe_dau_doi": "is_first_car",
        "first_car": "is_first_car",
        "commute_route": "commute_route", "duong_di_lam": "commute_route",
        "weekend_route": "weekend_route",
        "vehicle_color": "vehicle_color", "mau_xe": "vehicle_color",
        "color": "vehicle_color",
    }

    def _normalize_fact_key(self, key: str) -> str:
        import re as _re
        k = _re.sub(r"\s+", "_", (key or "").strip().lower())
        return self.FACT_KEY_SYNONYMS.get(k, k)

    def tool_get_persona_facts(self, args):
        keys = args.get("keys") or []
        q = select(PersonaFact).where(
            PersonaFact.persona_id == self.persona_id,
            PersonaFact.status == "active")
        all_rows = self.s.execute(q).scalars().all()
        if keys:
            mapped = [self._normalize_fact_key(k) for k in keys]
            matched = [r for r in all_rows if r.fact_key in mapped]
            if matched:
                rows = matched
            else:
                # Fact_key first: key đúng tên trong registry mà chưa
                # active, hay key lạ ngoài registry, đều trả NOT_FOUND
                # (kèm gợi ý registry để model hỏi lại đúng key) — KHÔNG
                # đổ toàn bộ canon: đổ hết làm model lẫn lộn dữ kiện
                # không liên quan và che mất việc canon thiếu key đó.
                # (Luật cũ fallback trả hết canon khi key lạ — bỏ vì
                # synonym map đã xử lý các cách đoán key thường gặp.)
                return ToolResult(
                    status="NOT_FOUND", tool="get_persona_facts", data=[],
                    reason="fact_key chưa có trong canon active; registry: "
                           "vehicle_model, vehicle_trim, purchased_at, "
                           "is_first_car, commute_route, weekend_route, "
                           "vehicle_color, favorite_*, trip_*, commute_*, "
                           "weekend_*, experience_*, habit_*, memory_*")
            rows = matched
        else:
            rows = all_rows
        data = [{"id": r.id, "fact_key": r.fact_key, "value": r.value,
                 "source": r.source, "version": r.version,
                 "valid_from": r.valid_from, "valid_to": r.valid_to}
                for r in rows]
        for r in rows:
            self.retrieved[r.id] = ("persona", r.value,
                                    {"status": "active", "fact_key": r.fact_key})
        if not data:
            return ToolResult(status="NOT_FOUND", tool="get_persona_facts",
                              data=[])
        return ToolResult(status="FOUND", tool="get_persona_facts", data=data)

    def tool_get_persona_stories(self, args):
        event_id = args.get("event_id")
        q = select(PersonaStory).where(
            PersonaStory.persona_id == self.persona_id,
            PersonaStory.status == "active")
        if event_id:
            q = q.where(PersonaStory.event_id == event_id)
        rows = self.s.execute(q).scalars().all()
        data = [canonical_view(self.s, r) for r in rows]
        for r, d in zip(rows, data):
            self.retrieved[r.event_id] = (
                "story", _story_retrieved_value(
                    r.summary, r.body, d["addenda"]),
                {"status": "active"})
        if not data:
            return ToolResult(status="NOT_FOUND", tool="get_persona_stories",
                              data=[])
        return ToolResult(status="FOUND", tool="get_persona_stories", data=data)

    def tool_get_product_facts(self, args):
        model = args.get("model", "")
        q = select(ProductFact).where(ProductFact.model == model)
        if args.get("trim"):
            q = q.where(ProductFact.trim == args["trim"])
        if args.get("fact_type"):
            q = q.where(ProductFact.fact_type == args["fact_type"])
        if args.get("location"):
            q = q.where(ProductFact.location == args["location"])
        rows = self.s.execute(q).scalars().all()
        today = _today()
        active = [r for r in rows if r.status == "active"
                  and (not r.valid_to or today <= r.valid_to)]
        conflicts = [r for r in rows if r.status == "active"]
        # Xung đột: nhiều fact active cùng model+type+trim nhưng giá trị khác.
        seen: dict[tuple, set] = {}
        for r in conflicts:
            seen.setdefault((r.model, r.trim, r.fact_type),
                            set()).add(r.value_text)
        if any(len(v) > 1 for v in seen.values()):
            return ToolResult(status="CONFLICT", tool="get_product_facts",
                              data=[{"id": r.id, "value_text": r.value_text}
                                    for r in conflicts],
                              reason="nhiều product fact active mâu thuẫn")
        if not active:
            return ToolResult(status="NOT_FOUND", tool="get_product_facts",
                              data=[])
        data = [{"id": r.id, "model": r.model, "trim": r.trim,
                 "fact_type": r.fact_type, "value_text": r.value_text,
                 "value_number": r.value_number, "unit": r.unit,
                 "location": r.location, "source": r.source,
                 "valid_to": r.valid_to, "is_sample": r.is_sample}
                for r in active]
        for r in active:
            self.retrieved[r.id] = ("product", r.value_text,
                                    {"status": "active",
                                     "valid_to": r.valid_to, "now": today,
                                     "is_sample": r.is_sample})
        return ToolResult(status="FOUND", tool="get_product_facts", data=data)

    def tool_get_customer_state(self, args):
        facts = self.s.execute(
            select(CustomerFact).where(
                CustomerFact.thread_id == self.thread_id,
                CustomerFact.actor_id == self.actor_id,
                CustomerFact.status == "current")).scalars().all()
        from ..models import QuestionState
        qs = self.s.execute(
            select(QuestionState).where(
                QuestionState.thread_id == self.thread_id,
                QuestionState.actor_id == self.actor_id)).scalars().all()
        return ToolResult(status="FOUND", tool="get_customer_state", data={
            "customer_facts": [
                {"id": f.id, "subject": f.subject, "relation": f.relation,
                 "predicate": f.predicate, "value_normalized": f.value_normalized,
                 "evidence": f.evidence, "source": f.source,
                 "confidence": f.confidence,
                 "need_kind": f.need_kind, "certainty": f.certainty}
                for f in facts],
            "questions": [
                {"question_key": q.question_key, "question_text": q.question_text,
                 "status": q.status} for q in qs],
        })

    def _evidence_matches_server_message(self, evidence: str) -> bool:
        """Evidence phải tồn tại trong message do server xác định:
        lượt khách hiện tại hoặc các lượt user trước TRONG CÙNG ACTOR
        của thread này. Lời của actor khác trong cùng thread KHÔNG được
        làm evidence cho fact của actor này (thread đa actor: Khách 2
        nói gì cũng không thành fact của Khách 1).
        Chấp nhận substring chuẩn hóa hoặc overlap token >= 0.7 (để
        model trích dẫn gần đúng vẫn qua, bịa hoàn toàn thì không)."""
        import re as _re
        ev = _re.sub(r"\s+", " ", (evidence or "").lower().strip())
        if not ev:
            return False
        texts = [self.current_user_text]
        try:
            from ..models import Message as _Msg
            rows = self.s.execute(
                select(_Msg).where(
                    _Msg.thread_id == self.thread_id,
                    _Msg.role == "user",
                    _Msg.actor_id == self.actor_id)).scalars().all()
            texts.extend(r.text for r in rows)
        except Exception:
            pass
        ev_tokens = set(_re.findall(r"\w+", ev))
        for t in texts:
            low = _re.sub(r"\s+", " ", (t or "").lower().strip())
            if ev and ev in low:
                return True
            if ev_tokens:
                tt = set(_re.findall(r"\w+", low))
                if tt and len(ev_tokens & tt) / max(1, len(ev_tokens)) >= 0.7:
                    return True
        return False

    def tool_propose_customer_fact(self, args):
        # Không gán thông tin persona / người thứ ba cho khách và ngược lại:
        # subject phải khớp relation (third_person bắt buộc có relation).
        if args.get("subject") == "third_person" and not args.get("relation"):
            return ToolResult(status="CONFLICT", tool="propose_customer_fact",
                              reason="third_person cần relation")
        if not args.get("evidence"):
            return ToolResult(status="CONFLICT", tool="propose_customer_fact",
                              reason="customer fact cần evidence nguyên văn")
        if not self._evidence_matches_server_message(args["evidence"]):
            return ToolResult(status="CONFLICT", tool="propose_customer_fact",
                              reason="evidence không khớp message nào do "
                                     "server xác định trong thread này")
        # SĐT khách đưa phải qua được kiểm tra hình thức di động Việt
        # Nam, nếu không lead lưu xong cũng là lead giả và lượt xác nhận
        # sẽ hứa chuyển cho sale một số không liên lạc được (feedback
        # transcript 2026-11-04: "0123456789" được xác nhận chuyển như
        # số thật). Trả CONFLICT để model còn trong vòng tool loop hỏi
        # lại khách kiểm tra số, thay vì lưu rồi xác nhận.
        if args.get("predicate") == "phone_number":
            _phone = args.get("value_normalized") or args.get("value_raw") or ""
            if not is_plausible_vn_mobile(_phone):
                return ToolResult(status="CONFLICT",
                                  tool="propose_customer_fact",
                                  reason="SĐT này không hợp lệ (không đúng "
                                         "định dạng di động Việt Nam hoặc "
                                         "giống số giả như dãy số liên tiếp) "
                                         "— đừng lưu, đừng xác nhận chuyển "
                                         "cho sale; hãy hỏi lại khách kiểm "
                                         "tra số")
        # Candidate pending: KHÔNG supersede fact cũ ngay. Supersede chỉ
        # xảy ra ở activate_pending() khi delivery thành công, và chỉ
        # khi cùng subject+predicate+relation (bố vs mẹ không ghi đè nhau).
        # Customer need state: need_kind/certainty rõ ràng, không nâng
        # hypothesis thành fact. Nếu model không nêu, suy ra từ predicate
        # và source (stated -> explicit/high, inferred -> hypothesis/low
        # khi predicate là suy luận, ngược lại medium).
        _kind_map = {"intended_user": "intended_user",
                     "use_case": "usage_context", "usage": "usage_context",
                     "budget": "constraint", "concern": "concern",
                     "worry": "concern", "preference": "preference",
                     "like": "preference", "buying_interest": "explicit"}
        need_kind = args.get("need_kind") or _kind_map.get(
            args["predicate"], "explicit")
        if args.get("source") == "inferred_context" and not args.get("need_kind"):
            need_kind = "hypothesis"
        certainty = args.get("certainty")
        if not certainty:
            certainty = "low" if need_kind == "hypothesis" else (
                "high" if args.get("source", "stated") == "stated" else "medium")
        row = CustomerFact(
            thread_id=self.thread_id, actor_id=self.actor_id,
            subject=args["subject"], relation=args.get("relation"),
            predicate=args["predicate"], value_raw=args["value_raw"],
            value_normalized=args.get("value_normalized") or args["value_raw"],
            evidence=args["evidence"],
            message_id=self.current_message_id,
            source=args.get("source", "stated"),
            status="pending", need_kind=need_kind, certainty=certainty,
            confidence=float(args.get("confidence", 0.8)))
        self.s.add(row)
        self.s.commit()
        self.pending_customer_fact_ids.append(row.id)
        if args.get("correction"):
            self.correction_customer_fact_ids.add(row.id)
        item = {"id": row.id, "subject": row.subject,
                "relation": row.relation, "predicate": row.predicate,
                "value_normalized": row.value_normalized,
                "evidence": row.evidence, "source": row.source,
                "status": "pending", "message_id": row.message_id,
                "need_kind": row.need_kind, "certainty": row.certainty}
        self.saved_customer_facts.append(item)
        return ToolResult(status="FOUND", tool="propose_customer_fact",
                          data=item)

