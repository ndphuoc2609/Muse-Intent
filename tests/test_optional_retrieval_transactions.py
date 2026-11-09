"""A failed optional database operation must not poison the turn's session."""
import logging

import pytest

from app.engine import pipeline, rag, retrieval
from app.models import Actor
from tests.conftest import turn
from tests.test_rag import _index_small


@pytest.mark.parametrize("step", ["guard", "index", "story", "rag", "company", "area"])
def test_optional_database_failure_does_not_abort_turn(pipe, monkeypatch, caplog, step):
    pipe.s.add(Actor(id="duplicate-actor", display_name="existing"))
    pipe.s.commit()

    def fail_with_database_error(*args, **kwargs):
        pipe.s.add(Actor(id="duplicate-actor", display_name="duplicate"))
        pipe.s.flush()

    if step == "guard":
        monkeypatch.setattr(pipeline, "guard_resolved_reference", fail_with_database_error)
    elif step == "index":
        monkeypatch.setattr(rag, "index_status", fail_with_database_error)
    elif step == "story":
        monkeypatch.setattr(retrieval, "retrieve_stories", fail_with_database_error)
    elif step == "rag":
        monkeypatch.setattr(rag, "index_status", lambda session: {
            "status": "ready", "chunk_count": 1,
            "persona_sha": None, "seed_persona_sha": None})
        monkeypatch.setattr(rag, "load_index_meta", fail_with_database_error)
    else:
        _index_small(pipe.s, "AUTO_029")
        monkeypatch.setattr(rag, "retrieve_company_chunks" if step == "company"
                            else "find_thread_area", fail_with_database_error)

    with caplog.at_level(logging.ERROR):
        response = turn(pipe, "Em đang tìm hiểu Creta, muốn mua xe đi phố tiết kiệm"
                        if step in {"company", "area"} else "Chào buổi sáng")

    assert response.status == "sent"
    assert "IntegrityError" in caplog.text
