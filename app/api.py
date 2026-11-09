"""FastAPI: API + sandbox UI tĩnh. Không có tích hợp Facebook/Meta."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from .config import SETTINGS
from .db import init_db, make_engine, session_factory
from .engine.delivery_state import can_recover, finish_delivery
from .engine.enrichment import find_gaps, publish_draft, validate_draft
from .engine.fake_sender import FakeSender
from .engine.persona_validator import validate_persona
from .engine.pipeline import Pipeline
from .engine.reactions import get_reactions, toggle_reaction
from .engine.taxonomy import CATEGORIES, TAGS, TAXONOMY_VERSION
from .engine.tools import ToolExecutor, canonical_view
from .models import (Delivery, EnrichmentDraft, Message, Persona, PersonaCategory,
                     PersonaFact, PersonaPerspective, PersonaProfile,
                     PersonaStory, PersonaStyleGuide, RetrievalLog,
                     TagAssignment, Thread, TurnIdempotency)
from .providers.openrouter import make_provider
from .schemas import TurnRequest
from .seed import seed

ENGINE = make_engine()
init_db(ENGINE)
Session = session_factory(ENGINE)
try:
    PROVIDER = make_provider()
    PROVIDER_ERROR = None
except RuntimeError as _e:
    # Cấu hình yêu cầu live nhưng thiếu key: app vẫn chạy để /health
    # báo rõ, nhưng turns sẽ trả lỗi thay vì chạy Fake im lặng.
    PROVIDER = None
    PROVIDER_ERROR = str(_e)
SENDER = FakeSender()


def _require_provider():
    if PROVIDER is None:
        raise HTTPException(502, PROVIDER_ERROR or "provider chưa cấu hình")
    return PROVIDER

app = FastAPI(title="persona-dialogue", version="1.0.0")


def _session():
    return Session()


def _partner_tenant(x_api_key: str | None) -> str | None:
    """Partner mode (PARTNER_API_KEY đã cấu hình): endpoint partner yêu
    cầu đúng key, trả về tenant của key đó. Sandbox (chưa cấu hình
    key): không yêu cầu auth, trả None (không giới hạn tenant)."""
    if not SETTINGS.partner_mode:
        return None
    if x_api_key != SETTINGS.partner_api_key:
        raise HTTPException(401, "X-API-Key không hợp lệ")
    return SETTINGS.partner_tenant


def _check_thread_tenant(thread: Thread | None, tenant: str | None) -> None:
    """Partner chỉ được chạm thread thuộc tenant của mình (kể cả thread
    tenant NULL — thread sandbox không thuộc partner nào)."""
    if tenant is None:
        return
    if thread is None:
        raise HTTPException(404, "thread không tồn tại")
    if thread.tenant != tenant:
        raise HTTPException(403, "thread không thuộc tenant này")


@app.get("/health")
def health():
    return {
        "status": "misconfigured" if PROVIDER is None else "ok",
        "provider": type(PROVIDER).__name__ if PROVIDER is not None else None,
        "provider_error": PROVIDER_ERROR,
        "llm_provider_config": SETTINGS.llm_provider,
        "openrouter_configured": bool(SETTINGS.openrouter_api_key),
        "openrouter_model": SETTINGS.openrouter_model,
        "database": SETTINGS.database_url.split("://")[0],
    }


@app.post("/seed")
def do_seed():
    with _session() as s:
        return seed(s)


@app.get("/personas")
def list_personas():
    with _session() as s:
        rows = s.execute(select(Persona)).scalars().all()
        return [{"id": r.id, "display_name": r.display_name, "age": r.age,
                 "region": r.region, "stance": r.stance,
                 "is_fictional": r.is_fictional, "style": r.style}
                for r in rows]


@app.get("/personas/{persona_id}")
def persona_detail(persona_id: str):
    with _session() as s:
        p = s.get(Persona, persona_id)
        if not p:
            raise HTTPException(404, "persona không tồn tại")
        facts = s.execute(
            select(PersonaFact).where(
                PersonaFact.persona_id == persona_id,
                PersonaFact.status == "active")).scalars().all()
        stories = s.execute(
            select(PersonaStory).where(
                PersonaStory.persona_id == persona_id,
                PersonaStory.status == "active")).scalars().all()
        return {
            "id": p.id, "display_name": p.display_name, "age": p.age,
            "region": p.region, "stance": p.stance,
            "is_fictional": p.is_fictional, "style": p.style,
            "facts": [{"id": f.id, "fact_key": f.fact_key, "value": f.value,
                       "source": f.source, "version": f.version}
                      for f in facts],
            "stories": [canonical_view(s, st) for st in stories],
        }


class CreateThreadRequest(BaseModel):
    persona_id: str
    post_id: str = "post-1"
    post_text: str = ""
    post_vehicle_models: list[str] = []
    tenant: str | None = None


@app.post("/threads")
def create_thread(req: CreateThreadRequest,
                  x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Tạo bài viết (thread) cho một persona đã chọn — bước 2 của sandbox."""
    tenant = _partner_tenant(x_api_key)
    with _session() as s:
        p = s.get(Persona, req.persona_id)
        if not p:
            raise HTTPException(404, "persona không tồn tại — bấm Seed trước")
        t = Thread(persona_id=req.persona_id, post_id=req.post_id,
                   post_text=req.post_text,
                   post_vehicle_models=req.post_vehicle_models,
                   tenant=tenant if tenant is not None else req.tenant)
        s.add(t)
        s.commit()
        return {"thread_id": t.id, "persona_id": t.persona_id,
                "post_id": t.post_id, "post_text": t.post_text,
                "post_vehicle_models": t.post_vehicle_models,
                "tenant": t.tenant}


@app.get("/threads/{thread_id}")
def thread_detail(thread_id: str):
    with _session() as s:
        t = s.get(Thread, thread_id)
        if not t:
            raise HTTPException(404, "thread không tồn tại")
        return {"thread_id": t.id, "persona_id": t.persona_id,
                "post_id": t.post_id, "post_text": t.post_text,
                "post_vehicle_models": t.post_vehicle_models}


@app.post("/threads/{thread_id}/turns")
def turn(thread_id: str, req: TurnRequest,
         idempotency_key: str | None = Header(default=None,
                                              alias="Idempotency-Key"),
         x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    tenant = _partner_tenant(x_api_key)
    # Thread trong body phải khớp thread trên URL (binding, §3).
    if req.thread_id and req.thread_id != thread_id:
        raise HTTPException(400, "thread_id trong body khác URL")
    req.thread_id = thread_id if thread_id != "new" else None
    if tenant is not None:
        req.tenant = tenant
    with _session() as s:
        if tenant is not None and req.thread_id:
            _check_thread_tenant(s.get(Thread, req.thread_id), tenant)
        pipe = Pipeline(s, _require_provider(), SENDER)
        result = pipe.run(req, idempotency_key=idempotency_key)
        return JSONResponse(result.model_dump(),
                            status_code=200 if result.status != "error" else 502)


@app.post("/turns")
def turn_new(req: TurnRequest,
             idempotency_key: str | None = Header(default=None,
                                                  alias="Idempotency-Key"),
             x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    tenant = _partner_tenant(x_api_key)
    if tenant is not None:
        req.tenant = tenant
    with _session() as s:
        if tenant is not None and req.thread_id:
            _check_thread_tenant(s.get(Thread, req.thread_id), tenant)
        pipe = Pipeline(s, _require_provider(), SENDER)
        return pipe.run(req, idempotency_key=idempotency_key)


def _confirmation_actor_id(s, msg: Message) -> str:
    """Actor của lượt tạo ra message này: từ TurnIdempotency, fallback
    qua user message cha (persona reply có parent = user message)."""
    rec = s.execute(
        select(TurnIdempotency).where(
            TurnIdempotency.persona_message_id == msg.id)).scalars().first()
    if rec is not None:
        return rec.actor_id
    if msg.parent_message_id:
        parent = s.get(Message, msg.parent_message_id)
        if parent is not None and parent.actor_id:
            return parent.actor_id
    return ""


def _record_delivery_report(s, msg: Message, req) -> None:
    trace = dict(msg.trace or {})
    reports = list(trace.get("delivery_reports") or [])
    reports.append({"status": req.status,
                    "channel_message_id": req.channel_message_id,
                    "error": req.error})
    trace["delivery_reports"] = reports
    msg.trace = trace
    s.commit()


def _flag_delivery_conflict(s, msg: Message, req) -> None:
    """Báo mâu thuẫn với trạng thái đã xác nhận: ghi lại để đối soát,
    không đổi trạng thái message."""
    trace = dict(msg.trace or {})
    trace["delivery_conflict"] = {
        "previous_status": msg.status, "reported": req.status,
        "channel_message_id": req.channel_message_id,
        "error": req.error}
    msg.trace = trace
    s.commit()


def _delivery_row(s, message_id: str):
    return s.execute(
        select(Delivery).where(Delivery.message_id == message_id)
    ).scalars().first()


def _confirm_sent(s, msg: Message, thread: Thread, req) -> None:
    trace = dict(msg.trace or {})
    snapshot = trace.get("delivery_recovery")
    actor_id = _confirmation_actor_id(s, msg)
    if snapshot:
        executor = ToolExecutor(s, thread.persona_id, thread.id, actor_id)
        if can_recover(s, snapshot, msg, thread.persona_id, actor_id):
            finish_delivery(s, executor, snapshot, msg, recovered=True)
        else:
            # Canon đã đổi sau khi stage: message thì đã gửi thật trên
            # kênh, nhưng không kích hoạt fact cũ — reject để khỏi ô
            # nhiễm lượt sau, ghi dấu để đối soát.
            for field, ids in (
                    ("pending_persona_fact_ids", snapshot.get("pending_persona_fact_ids") or []),
                    ("pending_story_ids", snapshot.get("pending_story_ids") or []),
                    ("pending_addendum_ids", snapshot.get("pending_addendum_ids") or []),
                    ("pending_customer_fact_ids", snapshot.get("pending_customer_fact_ids") or []),
                    ("pending_tag_assignment_ids", snapshot.get("pending_tag_assignment_ids") or [])):
                setattr(executor, field, list(ids))
            executor.reject_pending()
            trace["delivery_recovery_blocked"] = True
    msg.status = "sent"
    row = _delivery_row(s, msg.id)
    if row is not None:
        row.status = "sent"
        row.error = None
    else:
        s.add(Delivery(message_id=msg.id, sender="partner", status="sent"))
    rec = s.execute(
        select(TurnIdempotency).where(
            TurnIdempotency.persona_message_id == msg.id)).scalars().first()
    if rec is not None:
        rec.status = "sent"
    msg.trace = trace
    s.commit()


def _confirm_failed(s, msg: Message, thread: Thread, req) -> None:
    trace = dict(msg.trace or {})
    snapshot = trace.get("delivery_recovery")
    if snapshot:
        executor = ToolExecutor(s, thread.persona_id, thread.id,
                                _confirmation_actor_id(s, msg))
        for field in ("pending_persona_fact_ids", "pending_story_ids",
                      "pending_addendum_ids", "pending_customer_fact_ids",
                      "pending_tag_assignment_ids"):
            setattr(executor, field, list(snapshot.get(field) or []))
        executor.reject_pending()
    msg.status = "error"
    row = _delivery_row(s, msg.id)
    if row is not None:
        row.status = "failed"
        row.error = req.error
    else:
        s.add(Delivery(message_id=msg.id, sender="partner",
                       status="failed", error=req.error))
    rec = s.execute(
        select(TurnIdempotency).where(
            TurnIdempotency.persona_message_id == msg.id)).scalars().first()
    if rec is not None:
        rec.status = "error"
    msg.trace = trace
    s.commit()


class DeliveryConfirmationRequest(BaseModel):
    status: str  # sent | failed | unknown
    channel_message_id: str | None = None
    error: str | None = None


@app.post("/threads/{thread_id}/messages/{message_id}/delivery-confirmation")
def confirm_delivery(thread_id: str, message_id: str,
                     req: DeliveryConfirmationRequest,
                     x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Partner báo kết quả gửi trên kênh thật (Phase 3b).

    Partner mode: reply chỉ ở ready_for_delivery cho tới khi partner
    xác nhận. sent -> kích hoạt pending candidates từ snapshot;
    failed -> reject pending; unknown -> giữ nguyên chờ báo lại.
    Idempotent: báo lại cùng status trả kết quả cũ, không kích hoạt
    lần hai. Báo mâu thuẫn (đã sent rồi báo failed, hoặc ngược lại)
    -> 409 + ghi trace.delivery_conflict để đối soát.
    """
    tenant = _partner_tenant(x_api_key)
    if req.status not in ("sent", "failed", "unknown"):
        raise HTTPException(400, "status phải là sent | failed | unknown")
    with _session() as s:
        msg = s.get(Message, message_id)
        if not msg or msg.thread_id != thread_id:
            raise HTTPException(404, "message không tồn tại")
        thread = s.get(Thread, thread_id)
        _check_thread_tenant(thread, tenant)
        if msg.status in ("draft", "sending", "review"):
            raise HTTPException(
                409, "message chưa ở trạng thái ready_for_delivery")
        if msg.status == "sent":
            if req.status == "sent":
                return {"message_id": msg.id, "status": "sent",
                        "idempotent_replay": True}
            _flag_delivery_conflict(s, msg, req)
            raise HTTPException(
                409, "message đã xác nhận sent, không thể báo lại "
                     f"{req.status} — xem trace.delivery_conflict")
        if msg.status == "error":
            if req.status == "failed":
                return {"message_id": msg.id, "status": "error",
                        "idempotent_replay": True}
            _flag_delivery_conflict(s, msg, req)
            raise HTTPException(
                409, "message đã xác nhận failed, không thể báo lại "
                     f"{req.status} — xem trace.delivery_conflict")
        # ready_for_delivery
        if req.status == "unknown":
            _record_delivery_report(s, msg, req)
            return {"message_id": msg.id, "status": "ready_for_delivery"}
        if req.status == "sent":
            _confirm_sent(s, msg, thread, req)
            _record_delivery_report(s, msg, req)
            return {"message_id": msg.id, "status": "sent"}
        _confirm_failed(s, msg, thread, req)
        _record_delivery_report(s, msg, req)
        return {"message_id": msg.id, "status": "error"}


def _iso_utc(dt):
    """created_at ra ISO có múi giờ: _now() luôn là UTC, nhưng SQLite
    trả về naive nên phải gắn Z, nếu không trình duyệt hiểu nhầm thành
    giờ local và thời gian tương đối lệch đúng bằng lệch múi giờ."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    return dt.isoformat()


@app.get("/threads/{thread_id}/messages")
def thread_messages(thread_id: str):
    with _session() as s:
        rows = s.execute(
            select(Message).where(Message.thread_id == thread_id)
            .order_by(Message.created_at)).scalars().all()
        return [{"id": r.id, "role": r.role, "actor_id": r.actor_id,
                 "text": r.text, "status": r.status, "trace": r.trace,
                 "created_at": _iso_utc(r.created_at)}
                for r in rows]


class ReactionRequest(BaseModel):
    actor_id: str
    emoji: str


@app.get("/threads/{thread_id}/reactions")
def thread_reactions(thread_id: str, actor_id: str | None = None):
    """Tổng hợp reactions cả thread + reaction của actor đang xem."""
    with _session() as s:
        t = s.get(Thread, thread_id)
        if not t:
            raise HTTPException(404, "thread không tồn tại")
        return get_reactions(s, thread_id, actor_id or "")


@app.post("/threads/{thread_id}/messages/{message_id}/reactions")
def react_to_message(thread_id: str, message_id: str, req: ReactionRequest):
    """Bấm reaction: thêm / đổi / gỡ (mỗi actor 1 reaction/message)."""
    with _session() as s:
        try:
            return toggle_reaction(s, thread_id, message_id,
                                   req.actor_id, req.emoji)
        except LookupError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))


@app.post("/personas/{persona_id}/stories/{event_id}/revoke")
def revoke_story(persona_id: str, event_id: str):
    """Thu hồi story theo quyền sandbox: status -> revoked, lịch sử giữ nguyên."""
    with _session() as s:
        st = s.get(PersonaStory, event_id)
        if not st or st.persona_id != persona_id:
            raise HTTPException(404, "story không thuộc persona này")
        st.status = "revoked"
        s.commit()
        return {"event_id": event_id, "status": "revoked"}


@app.post("/personas/{persona_id}/facts/{fact_id}/revoke")
def revoke_fact(persona_id: str, fact_id: str):
    with _session() as s:
        f = s.get(PersonaFact, fact_id)
        if not f or f.persona_id != persona_id:
            raise HTTPException(404, "fact không thuộc persona này")
        f.status = "revoked"
        s.commit()
        return {"fact_id": fact_id, "status": "revoked"}


# ---- Admin persona-depth (§11): xem profile/timeline/story/tags,
# draft enrichment, validator, retrieval log — không cần đọc JSON dài.
@app.get("/admin/taxonomy")
def admin_taxonomy():
    return {"version": TAXONOMY_VERSION, "categories": CATEGORIES, "tags": TAGS}


@app.get("/admin/personas/{persona_id}")
def admin_persona(persona_id: str):
    with _session() as s:
        p = s.get(Persona, persona_id)
        if not p:
            raise HTTPException(404, "persona không tồn tại")
        profile = s.get(PersonaProfile, persona_id)
        persps = s.execute(
            select(PersonaPerspective).where(
                PersonaPerspective.persona_id == persona_id,
                PersonaPerspective.status == "active")).scalars().all()
        style = s.get(PersonaStyleGuide, persona_id)
        cats = s.execute(
            select(PersonaCategory).where(
                PersonaCategory.persona_id == persona_id,
                PersonaCategory.status == "active")).scalars().all()
        facts = s.execute(
            select(PersonaFact).where(
                PersonaFact.persona_id == persona_id,
                PersonaFact.status == "active")).scalars().all()
        stories = s.execute(
            select(PersonaStory).where(
                PersonaStory.persona_id == persona_id,
                PersonaStory.status == "active")).scalars().all()
        # Timeline: purchased_at + story theo ngày (unknown ở cuối).
        timeline = []
        for f in facts:
            if f.fact_key == "purchased_at":
                timeline.append({"date": f.value, "kind": "purchase",
                                 "label": f"Mua xe ({f.value})"})
        for st in stories:
            timeline.append({"date": st.event_date or "unknown",
                             "kind": "story", "event_id": st.event_id,
                             "label": st.title or st.summary})
        timeline.sort(key=lambda d: (d["date"] == "unknown", d["date"]))
        return {
            "id": p.id, "display_name": p.display_name, "age": p.age,
            "region": p.region, "stance": p.stance,
            "is_fictional": p.is_fictional,
            "profile": None if not profile else {
                "occupation": profile.occupation,
                "daily_rhythm": profile.daily_rhythm,
                "family_structure": profile.family_structure,
                "ownership_status": profile.ownership_status,
                "purchase_context": profile.purchase_context,
                "spending_limit_note": profile.spending_limit_note},
            "perspectives": [{"aspect": x.aspect, "content": x.content,
                              "prior_content": x.prior_content}
                             for x in persps],
            "style_guide": None if not style else {
                "address_form": style.address_form,
                "answer_length": style.answer_length,
                "intimacy": style.intimacy, "emoji_use": style.emoji_use,
                "storytelling": style.storytelling,
                "proactive_question_level": style.proactive_question_level,
                "avoid_patterns": style.avoid_patterns,
                "style_examples": style.style_examples},
            "categories": [c.category_code for c in cats],
            "facts": [{"id": f.id, "fact_key": f.fact_key, "value": f.value,
                       "source": f.source,
                       "tags": [t.tag for t in s.execute(
                           select(TagAssignment).where(
                               TagAssignment.entity_type == "fact",
                               TagAssignment.entity_id == f.id,
                               TagAssignment.status == "active")).scalars().all()]}
                      for f in facts],
            "stories": [canonical_view(s, st) for st in stories],
            "timeline": timeline,
            "validator": validate_persona(s, persona_id),
            "gaps": find_gaps(s, persona_id),
        }


class DraftRequest(BaseModel):
    kind: str
    payload: dict
    reason: str = ""
    anchor_refs: list = []
    proposed_tags: list[str] = []
    proposed_categories: list[str] = []
    conditions: list = []
    conflicts: list = []


@app.post("/admin/personas/{persona_id}/drafts")
def admin_create_draft(persona_id: str, req: DraftRequest):
    with _session() as s:
        if not s.get(Persona, persona_id):
            raise HTTPException(404, "persona không tồn tại")
        d = EnrichmentDraft(
            persona_id=persona_id, kind=req.kind, payload=req.payload,
            reason=req.reason, anchor_refs=req.anchor_refs,
            proposed_tags=req.proposed_tags,
            proposed_categories=req.proposed_categories,
            conditions=req.conditions, conflicts=req.conflicts)
        s.add(d)
        s.commit()
        errs = validate_draft(s, d)
        d.validator_errors = errs
        s.commit()
        return {"draft_id": d.id, "status": d.status,
                "validator_errors": errs}


@app.get("/admin/personas/{persona_id}/drafts")
def admin_list_drafts(persona_id: str):
    with _session() as s:
        rows = s.execute(
            select(EnrichmentDraft).where(
                EnrichmentDraft.persona_id == persona_id)
            .order_by(EnrichmentDraft.created_at.desc())).scalars().all()
        return [{"draft_id": r.id, "kind": r.kind, "payload": r.payload,
                 "reason": r.reason, "proposed_tags": r.proposed_tags,
                 "proposed_categories": r.proposed_categories,
                 "status": r.status, "validator_errors": r.validator_errors}
                for r in rows]


@app.post("/admin/drafts/{draft_id}/publish")
def admin_publish_draft(draft_id: str):
    with _session() as s:
        out = publish_draft(s, draft_id)
        if not out["ok"]:
            raise HTTPException(400, "; ".join(out["errors"]))
        return out


@app.post("/admin/drafts/{draft_id}/reject")
def admin_reject_draft(draft_id: str):
    with _session() as s:
        d = s.get(EnrichmentDraft, draft_id)
        if not d:
            raise HTTPException(404, "draft không tồn tại")
        d.status = "rejected"
        s.commit()
        return {"draft_id": draft_id, "status": "rejected"}


@app.get("/admin/personas/{persona_id}/retrieval")
def admin_retrieval(persona_id: str):
    with _session() as s:
        rows = s.execute(
            select(RetrievalLog).where(
                RetrievalLog.persona_id == persona_id)
            .order_by(RetrievalLog.created_at.desc()).limit(50)).scalars().all()
        return [{"thread_id": r.thread_id, "actor_id": r.actor_id,
                 "entity_type": r.entity_type, "entity_id": r.entity_id,
                 "reason": r.reason, "score": r.score, "rank": r.rank,
                 "created_at": str(r.created_at)} for r in rows]


STATIC = Path(__file__).parent / "static"
if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/admin")
def admin_page():
    return FileResponse(STATIC / "admin.html")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
