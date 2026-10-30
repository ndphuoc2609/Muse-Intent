"""Regression guard cho cột Conversation Planner trong sandbox UI.

UI không có test trình duyệt tự động ở đây, nên test này khóa cấu trúc:
file index.html phải có cột planner, hàm renderPlanner đọc đúng các
field response.planner (goal, response_direction, intents + source,
answered_question_keys, pending_questions, data_needs, resolved_reference)
và sendTurn phải gọi nó sau mỗi lượt. Nếu ai đó sửa UI mà làm mất cột
hoặc đổi tên field, test này fail trước khi giao.
"""
from pathlib import Path

HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


def _html():
    return HTML.read_text(encoding="utf-8")


def test_planner_column_exists():
    html = _html()
    assert 'id="plannerCol"' in html
    assert "Conversation Planner" in html
    assert 'id="planner"' in html


def test_render_planner_uses_planner_fields():
    html = _html()
    assert "function renderPlanner(d)" in html
    for field in ("response_direction", "answered_question_keys",
                  "pending_questions", "data_needs", "resolved_reference"):
        assert field in html, f"thiếu field planner.{field} trong UI"
    # Intent source phải hiển thị rõ, không chỉ dump JSON.
    assert "sourceLabel" in html
    assert "Khách nói rõ" in html
    assert "Suy luận từ context" in html
    assert "Định hướng tiếp theo" in html


def test_send_turn_renders_planner():
    html = _html()
    assert "renderPlanner(d)" in html
    # renderPlanner phải được gọi trong sendTurn sau khi nhận response,
    # trước khi render reply (đặt ngay trước replyText).
    assert html.index("renderPlanner(d)") < html.index("const replyText")
