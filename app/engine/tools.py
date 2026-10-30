"""Tool execution ở engine (§7). Server xác định scope từ binding đã
xác minh: persona_id/thread/actor do pipeline truyền vào, model không
được tự chọn. Mọi lỗi DB/tool trả UNKNOWN, không phải NOT_FOUND.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select

from ..config import CREATABLE_FACT_KEY_PREFIXES, LOCKED_FACT_KEYS, SETTINGS
from ..models import (CustomerFact, Persona, PersonaFact, PersonaStory,
                      ProductFact, StoryRevision)
from ..schemas import ToolResult
from .consistency import check_addendum, check_story


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

    def activate_pending(self) -> None:
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

    def _add_pending_tags(self, entity_type: str, entity_id: str,
                          tags: list | None) -> dict:
        """Tạo TagAssignment pending cho tag đã biết; tag lạ không tự
        thành taxonomy, trả về trong unknown để model/admin duyệt."""
        from ..engine.taxonomy import is_known_tag as _known
        from ..models import TagAssignment as _TA
        accepted, unknown = [], []
        for tag in tags or []:
            if not _known(tag):
                unknown.append(tag)
                continue
            existing = self.s.execute(
                select(_TA).where(
                    _TA.entity_type == entity_type,
                    _TA.entity_id == entity_id,
                    _TA.tag == tag)).scalars().first()
            if existing is not None:
                continue
            row = _TA(entity_type=entity_type, entity_id=entity_id,
                      tag=tag, source="generated", status="pending")
            self.s.add(row)
            self.s.commit()
            self.pending_tag_assignment_ids.append(row.id)
            accepted.append(tag)
        return {"tags_accepted": accepted, "tags_unknown": unknown}

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
                # Phân biệt hai trường hợp, không gộp thành một NOT_FOUND:
                # (a) model hỏi đúng tên key trong registry nhưng fact
                # chưa active (VD: vehicle_color chưa propose) -> NOT_FOUND
                # để model đi đường propose, và để test pending-isolation
                # (test_improvement) vẫn giữ nguyên ý nghĩa.
                # (b) model đoán toàn key lạ ngoài registry (VD: vehicle,
                # car, purchase_date) -> fallback trả toàn bộ canon active,
                # kèm reason rõ ràng. Trả [] ở đây là nói dối model rằng
                # persona không có dữ kiện, trong khi fact đã seed sẵn.
                from ..config import CREATABLE_FACT_KEY_PREFIXES as _PFX
                from ..config import LOCKED_FACT_KEYS as _LOCK
                known = set(_LOCK) | {
                    "vehicle_model", "vehicle_trim", "purchased_at",
                    "is_first_car", "commute_route", "weekend_route"}
                _all_keys = {r.fact_key for r in self.s.execute(
                    select(PersonaFact).where(
                        PersonaFact.persona_id == self.persona_id)
                ).scalars().all()}

                def _is_known(k: str) -> bool:
                    return k in known or k.startswith(_PFX) or k in _all_keys
                if any(_is_known(k) for k in mapped):
                    return ToolResult(status="NOT_FOUND",
                                      tool="get_persona_facts", data=[])
                rows = all_rows
                if not rows:
                    return ToolResult(status="NOT_FOUND",
                                      tool="get_persona_facts", data=[])
                data = [{"id": r.id, "fact_key": r.fact_key, "value": r.value,
                         "source": r.source, "version": r.version,
                         "valid_from": r.valid_from, "valid_to": r.valid_to}
                        for r in rows]
                for r in rows:
                    self.retrieved[r.id] = (
                        "persona", r.value,
                        {"status": "active", "fact_key": r.fact_key})
                return ToolResult(status="FOUND", tool="get_persona_facts",
                                  data=data,
                                  reason="keys_unmatched_fallback_all: "
                                         "requested keys không thuộc registry, "
                                         "trả toàn bộ canon active thay vì []")
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
                "story", r.summary + " " + r.body + " " + " ".join(d["addenda"]),
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

    # -- proposals (engine kiểm chứng rồi mới lưu) --
    def tool_propose_persona_fact(self, args):
        key = args.get("fact_key", "")
        if key in LOCKED_FACT_KEYS:
            return ToolResult(status="CONFLICT", tool="propose_persona_fact",
                              reason=f"fact_key '{key}' bị khóa, không được sửa")
        if not key.startswith(CREATABLE_FACT_KEY_PREFIXES):
            return ToolResult(status="CONFLICT", tool="propose_persona_fact",
                              reason=f"fact_key '{key}' không thuộc policy tạo mới")
        existing = self.s.execute(
            select(PersonaFact).where(
                PersonaFact.persona_id == self.persona_id,
                PersonaFact.fact_key == key,
                PersonaFact.status == "active")).scalars().first()
        if existing:
            if existing.value == args.get("value"):
                self.retrieved[existing.id] = ("persona", existing.value,
                                               {"status": "active",
                                                "fact_key": key})
                return ToolResult(status="FOUND", tool="propose_persona_fact",
                                  data={"id": existing.id, "fact_key": key,
                                        "value": existing.value,
                                        "reused": True})
            return ToolResult(status="CONFLICT", tool="propose_persona_fact",
                              reason="fact đã có giá trị khác, cần correction "
                                     "có thẩm quyền, không ghi đè")
        pending = self.s.execute(
            select(PersonaFact).where(
                PersonaFact.persona_id == self.persona_id,
                PersonaFact.fact_key == key,
                PersonaFact.status == "pending")).scalars().first()
        if pending:
            if pending.value == args.get("value"):
                self.retrieved[pending.id] = ("persona", pending.value,
                                              {"status": "pending",
                                               "fact_key": key})
                return ToolResult(status="FOUND", tool="propose_persona_fact",
                                  data={"id": pending.id, "fact_key": key,
                                        "value": pending.value,
                                        "reused": True, "status": "pending"})
            return ToolResult(status="CONFLICT", tool="propose_persona_fact",
                              reason="đã có candidate pending khác cho cùng key "
                                     "trong lượt này, không ghi đè")
        # Candidate: status pending, CHƯA phải canon. Pipeline chỉ
        # activate khi delivery thành công, ngược lại reject.
        row = PersonaFact(persona_id=self.persona_id, fact_key=key,
                          value=args.get("value", ""),
                          source=args.get("source", "generated"),
                          status="pending",
                          provenance=args.get("evidence", ""))
        self.s.add(row)
        self.s.commit()
        self.pending_persona_fact_ids.append(row.id)
        self.retrieved[row.id] = ("persona", row.value,
                                  {"status": "pending", "fact_key": key})
        tag_res = self._add_pending_tags("fact", row.id,
                                         args.get("tags"))
        return ToolResult(status="FOUND", tool="propose_persona_fact",
                          data={"id": row.id, "fact_key": key,
                                "value": row.value, "status": "pending",
                                **tag_res})

    def _evidence_matches_server_message(self, evidence: str) -> bool:
        """Evidence phải tồn tại trong message do server xác định:
        lượt khách hiện tại hoặc các lượt user trước trong thread.
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
                    _Msg.role == "user")).scalars().all()
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

    def tool_propose_story(self, args):
        persona = self.s.get(Persona, self.persona_id)
        purchased = None
        if persona:
            f = self.s.execute(
                select(PersonaFact).where(
                    PersonaFact.persona_id == self.persona_id,
                    PersonaFact.fact_key == "purchased_at",
                    PersonaFact.status == "active")).scalars().first()
            purchased = f.value if f else None
        candidates = self.s.execute(
            select(PersonaStory).where(
                PersonaStory.persona_id == self.persona_id)).scalars().all()
        check = check_story(args, candidates, purchased)
        if check["verdict"] == "SAME_EVENT":
            story = self.s.get(PersonaStory, check["event_id"])
            view = canonical_view(self.s, story)
            self.retrieved[story.event_id] = (
                "story", story.summary + " " + story.body,
                {"status": story.status})
            return ToolResult(status="FOUND", tool="propose_story",
                              data={**view, "reused": True},
                              ref_id=story.event_id)
        if check["verdict"] != "NEW_EVENT":
            return ToolResult(status=check["verdict"], tool="propose_story",
                              reason=check["reason"])
        # Giới hạn story/ngày/persona được kiểm ở API/pipeline qua
        # MAX_STORIES_PER_DAY_PER_PERSONA khi cần (sandbox mặc định tắt
        # hard-cap để không chặn luồng demo; test concurrency kiểm tra
        # version/conflict, không phải cap này).
        # Candidate pending, chưa phải canon — activate khi sent.
        # Luna PHẢI trả tags + phân loại (categories) để engine update DB:
        # tags lạ không tự thành taxonomy, categories lạ bị chặn rõ ràng.
        from ..engine.taxonomy import CATEGORIES as _CATS
        cats = args.get("categories") or []
        bad_cats = [c for c in cats if c not in _CATS]
        if bad_cats:
            return ToolResult(status="CONFLICT", tool="propose_story",
                              reason=f"category chưa có trong taxonomy: {bad_cats}")
        tellable = args.get("tellable_policy") or "on_relevance"
        if tellable not in ("always", "on_relevance", "never"):
            return ToolResult(status="CONFLICT", tool="propose_story",
                              reason=f"tellable_policy không hợp lệ: {tellable}")
        row = PersonaStory(
            event_id="evt_" + uuid.uuid4().hex[:16],
            persona_id=self.persona_id, title=args.get("title", ""),
            summary=args["summary"], body=args["body"],
            entities=args.get("entities") or {},
            event_type=args.get("event_type", "experience"),
            event_date=args.get("event_date"),
            event_date_precision=args.get("event_date_precision", "unknown"),
            fictional_generated=True,
            status="pending",
            participants=args.get("participants") or [],
            context_text=args.get("context"),
            concern_or_choice=args.get("concern_or_choice"),
            action_text=args.get("action"),
            outcome_text=args.get("outcome"),
            feeling_or_lesson=args.get("feeling_or_lesson"),
            canonical_facts=args.get("canonical_facts") or [],
            tellable_policy=tellable,
            related_need_topics=args.get("related_need_topics") or [],
            story_categories=cats,
            provenance="model_proposal")
        self.s.add(row)
        self.s.commit()
        self.pending_story_ids.append(row.event_id)
        self.retrieved[row.event_id] = (
            "story", row.summary + " " + row.body, {"status": "pending"})
        tag_res = self._add_pending_tags("story", row.event_id,
                                         args.get("tags"))
        item = canonical_view(self.s, row)
        # canonical_view chỉ lấy tag active, nên cộng thêm tag pending
        # của lượt này để model thấy đúng những gì nó vừa đề xuất.
        item["tags"] = sorted(set(item.get("tags") or [])
                              | set(tag_res["tags_accepted"]))
        item = {**item, **tag_res}
        self.saved_stories.append(item)
        return ToolResult(status="FOUND", tool="propose_story", data=item,
                          ref_id=row.event_id)

    def tool_propose_addendum(self, args):
        story = self.s.get(PersonaStory, args["event_id"])
        if story is None or story.persona_id != self.persona_id:
            return ToolResult(status="NOT_FOUND", tool="propose_addendum",
                              reason="event_id không thuộc persona này")
        check = check_addendum(story, args["content"], args.get("entities") or {})
        if check["verdict"] != "COMPATIBLE":
            return ToolResult(status="CONFLICT", tool="propose_addendum",
                              reason=check["reason"])
        # Addendum candidate pending: version của story CHƯA bump, và
        # canonical view (chỉ gồm revision active) CHƯA chứa nó. Để model
        # vẫn trích dẫn được trong lượt này, retrieved và data trả về
        # gồm cả pending addendum một cách tường minh.
        rev = StoryRevision(
            event_id=story.event_id, persona_id=self.persona_id,
            kind="addendum", content=args["content"],
            entities=args.get("entities") or {},
            version=story.version + 1, status="pending")
        self.s.add(rev)
        self.s.commit()
        self.pending_addendum_ids.append(rev.id)
        view = canonical_view(self.s, story)
        full_text = story.summary + " " + story.body + " " + \
            " ".join(view["addenda"]) + " " + args["content"]
        self.retrieved[story.event_id] = (
            "story", full_text, {"status": "pending"})
        out = {**view, "pending_addenda": [args["content"]]}
        self.saved_stories.append(out)
        return ToolResult(status="FOUND", tool="propose_addendum", data=out,
                          ref_id=story.event_id)
