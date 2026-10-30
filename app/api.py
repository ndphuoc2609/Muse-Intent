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
from .engine.enrichment import find_gaps, publish_draft, validate_draft
from .engine.fake_sender import FakeSender
from .engine.persona_validator import validate_persona
from .engine.pipeline import Pipeline
from .engine.taxonomy import CATEGORIES, TAGS, TAXONOMY_VERSION
from .engine.tools import canonical_view
from .models import (EnrichmentDraft, Message, Persona, PersonaCategory,
                     PersonaFact, PersonaPerspective, PersonaProfile,
                     PersonaStory, PersonaStyleGuide, RetrievalLog,
                     TagAssignment, Thread)
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


@app.post("/threads")
def create_thread(req: CreateThreadRequest):
    """Tạo bài viết (thread) cho một persona đã chọn — bước 2 của sandbox."""
    with _session() as s:
        p = s.get(Persona, req.persona_id)
        if not p:
            raise HTTPException(404, "persona không tồn tại — bấm Seed trước")
        t = Thread(persona_id=req.persona_id, post_id=req.post_id,
                   post_text=req.post_text,
                   post_vehicle_models=req.post_vehicle_models)
        s.add(t)
        s.commit()
        return {"thread_id": t.id, "persona_id": t.persona_id,
                "post_id": t.post_id, "post_text": t.post_text,
                "post_vehicle_models": t.post_vehicle_models}


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
                                              alias="Idempotency-Key")):
    # Thread trong body phải khớp thread trên URL (binding, §3).
    if req.thread_id and req.thread_id != thread_id:
        raise HTTPException(400, "thread_id trong body khác URL")
    req.thread_id = thread_id if thread_id != "new" else None
    with _session() as s:
        pipe = Pipeline(s, _require_provider(), SENDER)
        result = pipe.run(req, idempotency_key=idempotency_key)
        return JSONResponse(result.model_dump(),
                            status_code=200 if result.status != "error" else 502)


@app.post("/turns")
def turn_new(req: TurnRequest,
             idempotency_key: str | None = Header(default=None,
                                                  alias="Idempotency-Key")):
    with _session() as s:
        pipe = Pipeline(s, _require_provider(), SENDER)
        return pipe.run(req, idempotency_key=idempotency_key)


@app.get("/threads/{thread_id}/messages")
def thread_messages(thread_id: str):
    with _session() as s:
        rows = s.execute(
            select(Message).where(Message.thread_id == thread_id)
            .order_by(Message.created_at)).scalars().all()
        return [{"id": r.id, "role": r.role, "actor_id": r.actor_id,
                 "text": r.text, "status": r.status, "trace": r.trace}
                for r in rows]


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
