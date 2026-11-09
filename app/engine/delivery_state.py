"""Persist and restore the state associated with a delivery attempt."""
import hashlib
import json

from sqlalchemy import select

from ..models import (CustomerFact, Message, PersonaFact, PersonaStory,
                      ProductFact, QuestionState, RagChunk, StoryRevision,
                      StoryTold, TagAssignment)


_CANDIDATES = {
    "pending_persona_fact_ids": PersonaFact,
    "pending_story_ids": PersonaStory,
    "pending_addendum_ids": StoryRevision,
    "pending_customer_fact_ids": CustomerFact,
    "pending_tag_assignment_ids": TagAssignment,
}


def _row_state(row, *, candidate=False):
    if row is None:
        return None
    data = {}
    for column in row.__table__.columns:
        if column.name in ("created_at", "embedding"):
            continue
        value = getattr(row, column.name)
        if candidate and column.name == "status" and value in ("pending", "rejected"):
            value = "candidate"
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        data[column.name] = value
    return data


def _state_digest(state):
    payload = json.dumps(state, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _preconditions(session, snapshot, persona_id, thread_id, actor_id):
    candidates = {}
    for field, model in _CANDIDATES.items():
        candidates[field] = [_row_state(session.get(model, item), candidate=True)
                             for item in snapshot[field]]

    fact_keys = {row["fact_key"] for row in candidates["pending_persona_fact_ids"]
                 if row is not None}
    predicates = {row["predicate"] for row in candidates["pending_customer_fact_ids"]
                  if row is not None}
    facts = session.execute(select(PersonaFact).where(
        PersonaFact.persona_id == persona_id, PersonaFact.status == "active",
        PersonaFact.fact_key.in_(fact_keys))).scalars().all()
    customers = session.execute(select(CustomerFact).where(
        CustomerFact.thread_id == thread_id, CustomerFact.actor_id == actor_id,
        CustomerFact.status == "current", CustomerFact.predicate.in_(predicates)
    )).scalars().all()
    questions = session.execute(select(QuestionState).where(
        QuestionState.thread_id == thread_id,
        QuestionState.actor_id == actor_id)).scalars().all()

    refs = {"persona": PersonaFact, "story": PersonaStory,
            "product": ProductFact, "rag": RagChunk, "company": RagChunk}
    evidence = []
    story_ids = set(snapshot["saved_story_ids"])
    for claim in snapshot["final"].get("claims", []) or []:
        model = refs.get(claim.get("kind"))
        if model and claim.get("ref_id"):
            item = claim["ref_id"]
            if model is PersonaStory:
                story_ids.add(item)
            candidate = (model is PersonaFact and item in snapshot["pending_persona_fact_ids"]
                         or model is PersonaStory and item in snapshot["pending_story_ids"])
            evidence.append(_row_state(session.get(model, item), candidate=candidate))
    # An addendum depends on the story's status and version even if the
    # final reply does not explicitly claim that story.
    for revision in candidates["pending_addendum_ids"]:
        if revision:
            event_id = revision["event_id"]
            story_ids.add(event_id)
            evidence.append(_row_state(session.get(PersonaStory, event_id),
                                       candidate=event_id in snapshot["pending_story_ids"]))
    for event_id in snapshot["saved_story_ids"]:
        evidence.append(_row_state(session.get(PersonaStory, event_id),
                                   candidate=event_id in snapshot["pending_story_ids"]))
    revisions = session.execute(select(StoryRevision).where(
        StoryRevision.event_id.in_(story_ids),
        StoryRevision.status == "active")).scalars().all()

    return {"candidates": candidates,
            "facts": sorted((_row_state(row) for row in facts), key=lambda row: row["id"]),
            "customers": sorted((_row_state(row) for row in customers), key=lambda row: row["id"]),
            "questions": sorted((_row_state(row) for row in questions), key=lambda row: row["id"]),
            "evidence": evidence,
            "revisions": sorted((_row_state(row) for row in revisions), key=lambda row: row["id"])}


def make_snapshot(session, executor, final, opted_out):
    snapshot = {field: list(getattr(executor, field)) for field in _CANDIDATES}
    snapshot.update({
        "version": 1,
        "correction_customer_fact_ids": sorted(executor.correction_customer_fact_ids),
        "saved_story_ids": [row["event_id"] for row in executor.saved_stories],
        "final": final,
        "opted_out": opted_out,
    })
    snapshot["preconditions"] = _state_digest(_preconditions(
        session, snapshot, executor.persona_id, executor.thread_id, executor.actor_id))
    return snapshot


def has_later_messages(session, message):
    return session.execute(select(Message.id).where(
        Message.thread_id == message.thread_id,
        Message.created_at >= message.created_at,
        Message.id != message.id).limit(1)).first() is not None


def can_recover(session, snapshot, message, persona_id, actor_id):
    if not isinstance(snapshot, dict) or snapshot.get("version") != 1:
        return False
    try:
        if has_later_messages(session, message):
            return False
        current = _preconditions(session, snapshot, persona_id, message.thread_id, actor_id)
        if _state_digest(current) != snapshot["preconditions"]:
            return False
        from .tools import _today
        for claim in snapshot["final"].get("claims", []) or []:
            if claim.get("kind") != "product":
                continue
            product = session.get(ProductFact, claim.get("ref_id"))
            if product is None or (product.valid_to and _today() > product.valid_to):
                return False
            related = session.execute(select(ProductFact).where(
                ProductFact.model == product.model, ProductFact.trim == product.trim,
                ProductFact.fact_type == product.fact_type,
                ProductFact.status == "active")).scalars().all()
            if len({row.value_text for row in related}) > 1:
                return False
        return all(row is not None and row.get("status") == "candidate"
                   for rows in current["candidates"].values() for row in rows)
    except (KeyError, TypeError, ValueError):
        return False


def finish_delivery(session, executor, snapshot, message, *, recovered=False):
    if recovered:
        for field, model in _CANDIDATES.items():
            setattr(executor, field, list(snapshot[field]))
            for item in snapshot[field]:
                row = session.get(model, item)
                row.status = "pending"
        executor.correction_customer_fact_ids = set(snapshot["correction_customer_fact_ids"])
    executor.activate_pending(commit=False)
    final = snapshot["final"]
    for event_id in snapshot["saved_story_ids"]:
        story = session.get(PersonaStory, event_id)
        if story and not story.told_in_message_id:
            story.told_in_message_id = message.id
    seen = set()
    for claim in final.get("claims", []) or []:
        event_id = claim.get("ref_id")
        if claim.get("kind") != "story" or not event_id or event_id in seen:
            continue
        seen.add(event_id)
        story = session.get(PersonaStory, event_id)
        if story is not None:
            # Story kể từ canon (không phải tạo trong lượt) cũng ghi lần
            # kể đầu tiên — trước đây chỉ saved_story_ids mới được ghi.
            if not story.told_in_message_id:
                story.told_in_message_id = message.id
            if not session.execute(select(StoryTold).where(
                StoryTold.event_id == event_id, StoryTold.actor_id == executor.actor_id,
                StoryTold.message_id == message.id)).scalars().first():
                session.add(StoryTold(event_id=event_id, persona_id=executor.persona_id,
                                      actor_id=executor.actor_id, thread_id=executor.thread_id,
                                      message_id=message.id))
    if final.get("followup_question") and final.get("followup_key"):
        exists = session.execute(select(QuestionState).where(
            QuestionState.thread_id == executor.thread_id,
            QuestionState.actor_id == executor.actor_id,
            QuestionState.question_key == final["followup_key"],
            QuestionState.status == "pending")).scalars().first()
        if not exists:
            session.add(QuestionState(thread_id=executor.thread_id,
                actor_id=executor.actor_id, question_key=final["followup_key"],
                question_text=final["followup_question"], asked_message_id=message.id))
