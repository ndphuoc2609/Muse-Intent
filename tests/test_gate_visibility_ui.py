"""Gate decisions and intent confidence are displayed without inventing scores."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


NODE = shutil.which("node")
HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


def _render(payload, *, comments=False):
    if NODE is None:
        pytest.skip("Node.js is needed to execute the sandbox JavaScript")
    html = HTML.read_text(encoding="utf-8")
    escape = html[html.index("function esc("):html.index("\nfunction initials(")]
    planner = html[html.index("const GOAL_LABELS"):html.index("\nasync function sendTurn()")]
    render_comments = html[html.index("function renderComments("):html.index("\nlet lastMessages")]
    js = f"""
    {escape}
    const nodes = {{}};
    const document = {{getElementById: id => nodes[id] ||= {{innerHTML:'', textContent:''}}}};
    const postData = null, lastTrace = null;
    function postLiked() {{ return false; }}
    function actorLabel() {{ return 'Khách'; }}
    function actorInitials() {{ return 'K'; }}
    function initials() {{ return 'K'; }}
    function avatarColor() {{ return '#000'; }}
    function timeAgo() {{ return 'Vừa xong'; }}
    function bindCommentActions() {{}}
    function likeLabel() {{ return 'Thích'; }}
    function likeClass() {{ return ''; }}
    function pickerHTML() {{ return ''; }}
    {planner}
    {render_comments}
    const payload = {json.dumps(payload, ensure_ascii=True)};
    if ({str(comments).lower()}) {{
      renderComments(payload, {{counts: {{}}, mine: {{}}}});
      process.stdout.write(nodes.chat.innerHTML);
    }} else {{
      renderPlanner(payload);
      process.stdout.write(nodes.planner.innerHTML);
    }}
    """
    return subprocess.run([NODE, "-e", js], capture_output=True,
                          encoding="utf-8", check=True).stdout


@pytest.mark.parametrize(("action", "expected"), [
    ("pipeline", "Đưa vào xử lý"),
    ("bypass", "Bỏ qua"),
    ("faq", "Trả lời theo FAQ"),
])
def test_gate_action_and_reason_are_visible_on_latest_turn(action, expected):
    result = _render({"trace": {"gate": {"action": action, "reason": "Lý do kiểm thử"}}})
    assert expected in result
    assert "Lý do kiểm thử" in result
    assert "Chưa có điểm" in result


def test_planner_replay_uses_intent_confidence_from_trace_not_reply_score():
    result = _render({"planner": None, "trace": {
        "gate": {"action": "pipeline", "reason": "Cần xử lý"},
        "planner": {"intents": [{"type": "ask_persona", "confidence": 0.85,
                                 "normalized": "Hỏi xe", "evidence": "xe gì?"}]}}})
    assert "ask_persona" in result
    assert "Độ tin cậy nhận diện intent: 85%" in result
    assert "không phải điểm quyết định trả lời" in result


def test_recorded_gate_is_visible_on_comment_and_escapes_reason():
    result = _render([{"id": "m1", "role": "user", "actor_id": "actor-1",
                       "text": "ok", "status": "sent", "created_at": "",
                       "trace": {"gate": {"action": "bypass",
                                          "reason": "<script>bad</script>"}}}],
                     comments=True)
    assert "Bỏ qua" in result
    assert "&lt;script&gt;bad&lt;/script&gt;" in result
    assert "<script>bad</script>" not in result
    assert "Chưa có điểm" in result


def test_old_comment_without_gate_says_no_data():
    result = _render([{"id": "m1", "role": "user", "text": "cũ",
                       "status": "sent", "created_at": ""}], comments=True)
    assert "Không có dữ liệu" in result
    assert "Chưa có điểm" in result


@pytest.mark.parametrize("score", [0, 100])
def test_rule_score_boundaries_are_displayed_as_reference(score):
    result = _render({"trace": {"gate": {"action": "pipeline", "reason": "Cần xử lý"},
                                "intent_score": {"score": score, "factors": [],
                                                 "method": "rules_v1"}}})
    assert f"{score}/100" in result
    assert "theo luật" in result
    assert "không phải xác suất" in result
    assert "không điều khiển gate" in result


def test_score_factors_on_saved_comment_are_visible_and_escaped():
    result = _render([{"id": "m1", "role": "user", "text": "Cần tư vấn",
                       "status": "sent", "created_at": "", "trace": {
                           "gate": {"action": "pipeline", "reason": "Cần xử lý"},
                           "intent_score": {"score": 80, "method": "rules_v1",
                                            "factors": [{"label": "<img src=x>", "points": 30},
                                                        {"label": "Ngắn", "points": -10}]}}}],
                     comments=True)
    assert "80/100" in result
    assert "&lt;img src=x&gt;" in result and "<img src=x>" not in result
    assert "+30" in result and "-10" in result
    assert "không điều khiển gate" in result


@pytest.mark.parametrize("score", [None, -1, 101, "50"])
def test_invalid_score_is_not_displayed_as_a_real_score(score):
    result = _render({"trace": {"intent_score": {"score": score, "method": "rules_v1"}}})
    assert "Chưa có điểm" in result
