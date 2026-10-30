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
    return {
        "event_id": story.event_id, "title": story.title,
        "summary": story.summary, "body": story.body,
        "addenda": [r.content for r in revs],
        "entities": story.entities, "event_type": story.event_type,
        "event_date": story.event_date,
        "event_date_precision": story.event_date_precision,
        "fictional_generated": story.fictional_generated,
        "status": story.status, "version": story.version,
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

    def activate_pending(self) -> None:
        """Chuyển mọi proposal của lượt này thành canon (chỉ gọi khi sent)."""
        from ..models import CustomerFact as _CF
        for fid in self.pending_persona_fact_ids:
            row = self.s.get(PersonaFact, fid)
            if row is not None and row.status == "pending":
                row.status = "active"
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
        for fid in self.pending_customer_fact_ids:
            row = self.s.get(_CF, fid)
            if row is None or row.status != "pending":
                continue
            old_q = select(_CF).where(
                _CF.thread_id == self.thread_id,
                _CF.actor_id == self.actor_id,
                _CF.subject == row.subject,
                _CF.predicate == row.predicate,
                _CF.status == "current")
            for o in self.s.execute(old_q).scalars().all():
                if (o.relation or "") == (row.relation or "") and o.id != row.id:
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
    def tool_get_persona_facts(self, args):
        keys = args.get("keys") or []
        q = select(PersonaFact).where(
            PersonaFact.persona_id == self.persona_id,
            PersonaFact.status == "active")
        if keys:
            q = q.where(PersonaFact.fact_key.in_(keys))
        rows = self.s.execute(q).scalars().all()
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
                 "confidence": f.confidence} for f in facts],
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
        return ToolResult(status="FOUND", tool="propose_persona_fact",
                          data={"id": row.id, "fact_key": key,
                                "value": row.value, "status": "pending"})

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
        row = CustomerFact(
            thread_id=self.thread_id, actor_id=self.actor_id,
            subject=args["subject"], relation=args.get("relation"),
            predicate=args["predicate"], value_raw=args["value_raw"],
            value_normalized=args.get("value_normalized") or args["value_raw"],
            evidence=args["evidence"],
            message_id=self.current_message_id,
            source=args.get("source", "stated"),
            status="pending",
            confidence=float(args.get("confidence", 0.8)))
        self.s.add(row)
        self.s.commit()
        self.pending_customer_fact_ids.append(row.id)
        item = {"id": row.id, "subject": row.subject,
                "relation": row.relation, "predicate": row.predicate,
                "value_normalized": row.value_normalized,
                "evidence": row.evidence, "source": row.source,
                "status": "pending", "message_id": row.message_id}
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
            provenance="model_proposal")
        self.s.add(row)
        self.s.commit()
        self.pending_story_ids.append(row.event_id)
        self.retrieved[row.event_id] = (
            "story", row.summary + " " + row.body, {"status": "pending"})
        item = canonical_view(self.s, row)
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
