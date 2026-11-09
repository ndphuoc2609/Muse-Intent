"""Điểm tham khảo là luật minh bạch, không thay quyết định của gate."""
import socket

import pytest

from app.engine.intent_score import score_intent


def score(text, *, pending=False, lead=False, current=False, action="pipeline"):
    return score_intent(text, has_pending=pending, has_lead=lead,
                        current_lead=current, gate_action=action)


@pytest.mark.parametrize("text", ["", "   ", "👍✨", "???", "!!!"])
def test_no_alphanumeric_content_has_zero_score_even_with_context(text):
    result = score(text, pending=True, lead=True, current=True)
    assert result["score"] == 0
    assert result["method"] == "rules_v1"
    assert sum(f["points"] for f in result["factors"]) == 0


def test_question_words_and_question_mark_increase_score_once():
    result = score("Xe này giá bao nhiêu?")
    assert result["score"] == 70  # 40 base + 10 length + 20 question
    assert len([f for f in result["factors"] if f["points"] == 20]) == 1
    assert score("Bao nhiêu")['score'] == 60
    assert score("Đi phố ổn không?")["score"] == 70


def test_current_lead_takes_precedence_over_historical_lead():
    result = score("Tôi muốn mua Creta", lead=True, current=True)
    assert result["score"] == 75
    assert sorted(f["points"] for f in result["factors"]) == [10, 25, 40]
    assert score("Tôi muốn mua Creta", lead=True)["score"] == 65


def test_ok_is_lower_only_when_no_pending_question_or_lead():
    assert score("Ok nhé", action="bypass")["score"] == 5
    assert score("Ok nhé", pending=True)["score"] == 70
    assert score("Ok nhé", lead=True)["score"] == 55


def test_faq_and_explicit_stop_have_distinct_factors():
    assert score("Cảm ơn", action="faq")["score"] == 50
    result = score("Đừng nhắn nữa")
    assert result["score"] == 60
    assert any(f["points"] == 20 and f["label"] for f in result["factors"])
    assert score("Tiết kiệm thôi")["score"] == 40


def test_score_is_clamped_and_factors_remain_explainable():
    result = score("Dừng lại, tôi muốn mua xe ở đâu?", pending=True,
                   lead=True, current=True, action="faq")
    assert result["score"] == 100
    assert sum(f["points"] for f in result["factors"]) > 100
    assert all(isinstance(f["label"], str) and f["label"] and
               isinstance(f["points"], int) for f in result["factors"])


def test_scoring_is_local_and_deterministic(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("scoring must not access the network")

    monkeypatch.setattr(socket, "socket", forbidden)
    first = score("Đại lý ở đâu?")
    assert first == score("Đại lý ở đâu?")
    assert first["method"] == "rules_v1"
