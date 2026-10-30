"""FakeProvider: thay thế LLM thật trong test/sandbox offline.

QUAN TRỌNG: đây là mô phỏng có chủ ý đơn giản, KHÔNG phải bằng chứng
hệ thống tự nhiên với khách thật (§19). Logic pattern ở đây chỉ thuộc
về provider giả; engine không dùng keyword để quyết định (§3). Khi dùng
Luna thật, planner/tool call do model sinh, engine chỉ validate.

Fake tuân cùng contract tool như Luna: plan_turn trước, rồi retrieval,
rồi proposal, rồi submit_final_reply. Mọi claim đều lấy từ tool result.
"""
from __future__ import annotations

import json
import re
import unicodedata

from .base import ProviderResult, ToolCall


def _strip(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _has(text: str, *words: str) -> bool:
    """Khớp theo chuỗi token liên tiếp (không phải substring), để tránh
    'gia đình' khớp 'giá', 'em' khớp 'êm', v.v. khi đã strip dấu."""
    tokens = re.findall(r"\w+", _strip(text))
    for w in words:
        wt = re.findall(r"\w+", _strip(w))
        if not wt:
            continue
        if any(tokens[i:i + len(wt)] == wt
               for i in range(len(tokens) - len(wt) + 1)):
            return True
    return False


def _has_orig(text: str, *words: str) -> bool:
    """Khớp trên text gốc còn dấu: dùng cho từ ngắn dễ nhầm khi strip
    dấu, VD 'giá' (có dấu) không được khớp 'gia đình' (không dấu)."""
    s = text.lower()
    return any(w in s for w in words)


class FakeProvider:
    """Deterministic, không gọi mạng, không tốn phí."""

    # ---- phân tích lượt (mô phỏng planner của model) ----
    def analyze(self, text: str, context: str = "") -> dict:
        t = text.strip()
        s = _strip(t)
        intents: list[dict] = []
        needs: set[str] = set()
        goal = "answer_question"
        ask_followup = False
        followup_purpose = None
        followup_key = None

        def add(typ, subject="self", relation=None, norm="", source="stated",
                conf=0.85):
            intents.append({
                "type": typ, "subject": subject, "relation": relation,
                "normalized": norm or t, "evidence": t, "source": source,
                "confidence": conf,
            })

        if _has(t, "không muốn tiếp tục", "dừng lại", "đừng nhắn nữa",
                 "không cần nữa", "thôi nhé", "dừng đi"):
            add("opt_out", norm="khách muốn dừng")
            goal = "stop"
        elif _has(t, "đặt cọc", "cọc ngay", "thanh toán ngay", "chuyển khoản mua"):
            add("transaction", norm="khách muốn đặt cọc/thanh toán")
            goal = "handoff"
        elif _has(t, "có phải ai không", "người thật hay ai", "là bot", "là robot"):
            add("ai_identity", norm="khách hỏi có phải AI không")
        # Câu hỏi về persona (có thể nhiều câu trong một lượt)
        if _has(t, "màu gì", "mau gi", "xe màu"):
            add("ask_persona", subject="persona", norm="hỏi màu xe của persona")
            needs.add("persona_facts")
        if _has(t, "đang đi xe gì", "đi xe gì", "xe gì thế", "xe gì vậy"):
            add("ask_persona", subject="persona", norm="hỏi xe persona đang đi")
            needs.add("persona_facts")
        if _has(t, "mua lúc nào", "mua xe lúc nào", "mua khi nào",
                 "mua xe từ bao giờ", "lúc nào mua"):
            add("ask_persona", subject="persona", norm="hỏi thời điểm mua xe")
            needs.add("persona_facts")
        if _has(t, "đường dài có mệt", "đi xa có mệt", "đi đường dài"):
            add("ask_persona", subject="persona", norm="hỏi trải nghiệm đi đường dài")
            needs.add("persona_stories")
        if _has(t, "kỷ niệm", "chuyến đi", "trải nghiệm đáng nhớ", "nhớ nhất"):
            add("ask_persona", subject="persona", norm="hỏi kỷ niệm/chuyến đi")
            needs.add("persona_stories")
        if _has(t, "cùng ai", "đi cùng ai", "đi với ai"):
            add("ask_persona", subject="persona",
                norm="hỏi người đi cùng trong story")
            needs.add("persona_stories")
        # Câu hỏi về sản phẩm
        if _has_orig(t, "giá", "bao nhiêu tiền", "lăn bánh"):
            add("ask_product", norm="hỏi giá")
            needs.add("product_facts")
        if _has(t, "bảo hành"):
            add("ask_product", norm="hỏi bảo hành")
            needs.add("product_facts")
        if _has(t, "đại lý", "còn xe không", "tồn kho", "ưu đãi", "khuyến mãi"):
            add("ask_product", norm="hỏi đại lý/ưu đãi/tồn kho")
            needs.add("product_facts")
        if _has(t, "thông số", "động cơ", "mã lực"):
            add("ask_product", norm="hỏi thông số")
            needs.add("product_facts")
        # Người thứ ba: chủ thể phải là third_person, không gán cho self.
        # Lưu ý: s đã strip dấu, nên pattern cũng phải ở dạng không dấu.
        m = re.search(r"(cho|ho|giup)\s+(bo|me|vo|chong|ban|anh|chi|em)\b", s)
        if m or _has(t, "hỏi hộ", "cho bố", "cho mẹ", "cho vợ", "cho chồng"):
            rel = {"bo": "bố", "me": "mẹ", "vo": "vợ", "chong": "chồng",
                   "ban": "bạn"}.get(m.group(2), "người khác") if m else "người khác"
            add("third_party", subject="third_person", relation=rel,
                norm=f"người dùng xe dự kiến là {rel}")
            needs.add("customer_state")
        # Chia sẻ nhu cầu
        if _has(t, "muốn mua", "đang tìm hiểu", "cân nhắc", "đang xem xe"):
            add("share_need", norm="quan tâm mua xe (chưa phải giao dịch)")
            needs.add("customer_state")
            # Fake cũng phải tuân contract claim như Luna: nếu final
            # sẽ khẳng định mẫu xe persona đang đi thì phải truy xuất
            # persona fact trước để có ref_id, không khẳng định trần.
            needs.add("persona_facts")
            ask_followup, followup_purpose = True, "hiểu điểm khách thích ở mẫu xe"
            followup_key = "like_point"
        if _has(t, "muốn đổi xe", "đổi xe"):
            add("share_need", norm="muốn đổi xe")
            needs.add("customer_state")
        if _has(t, "chở gia đình", "gia đình đi xa", "đi xa", "cho gia đình"):
            add("share_need", norm="nhu cầu chở gia đình đi xa")
            needs.add("customer_state")
        if _has(t, "đã có xe", "mình đang đi"):
            add("share_need", norm="khách đã có xe")
            needs.add("customer_state")
        if _has(t, "xe êm", "xe rộng", "tiết kiệm", "dễ đỗ"):
            add("share_need", norm="ưu tiên của khách")
            needs.add("customer_state")
        if _has(t, "không phải", "sửa lại", "thực ra", "nhầm rồi"):
            add("correction", norm="khách đính chính")
            needs.add("customer_state")
        if s in {"u", "um", "dung roi", "dung", "ok", "oke", "vang", "the"}:
            add("social", norm="phản hồi ngắn đồng ý/theo context")
            needs.add("customer_state")
            needs.add("question_history")
        if not intents:
            add("social", norm="xã giao/chia sẻ chung")
        if not needs:
            needs.add("none")
        # Câu hỏi cũ được trả lời? Chỉ khi nội dung khớp chủ đề câu hỏi
        # (engine vẫn là bên đánh dấu cuối cùng, §10).
        answered_keys: list[str] = []
        mm = re.search(r"Câu hỏi đang chờ:\s*([^\n]+)", context or "")
        if mm and "(không có)" not in mm.group(1):
            for part in mm.group(1).split(","):
                key = part.strip().split(" ")[0]
                if s in {"u", "um", "dung roi", "dung", "ok", "oke", "vang"}:
                    answered_keys.append(key)
                elif key == "budget" and _has(t, "triệu", "ngân sách", "tầm tiền"):
                    answered_keys.append(key)
                elif key == "like_point" and _has(t, "thích", "rộng", "êm",
                                                  "tiết kiệm", "đẹp"):
                    answered_keys.append(key)
        # Tham chiếu: "xe này/chiếc đó/như vậy/con này" -> context bài viết.
        resolved: dict = {}
        if _has(t, "xe này", "chiếc đó", "như vậy", "con này", "mẫu này"):
            # Context do engine cấp chứa "Xe trong bài viết: A, B".
            mm = re.search(r"Xe trong bài viết:\s*([^\n]+)", context or "")
            if mm:
                models = [x.strip() for x in mm.group(1).split(",") if x.strip()]
                if len(models) == 1:
                    resolved = {"text": "xe này", "resolved_to": models[0],
                                "source": "inferred_context", "confidence": 0.7}
                elif len(models) > 1:
                    resolved = {"text": "xe này", "resolved_to": None,
                                "source": "inferred_context", "confidence": 0.4,
                                "ambiguous_models": models}
        return {
            "intents": intents, "goal": goal, "data_needs": sorted(needs),
            "answered_question_keys": answered_keys,
            "ask_followup": ask_followup, "followup_purpose": followup_purpose,
            "followup_key": followup_key, "resolved_reference": resolved,
        }

    # ---- vòng tool calling ----
    def chat(self, messages: list[dict], tools: list[dict]) -> ProviderResult:
        user_text = ""
        context = ""
        tool_results: list[dict] = []
        for m in messages:
            if m.get("role") == "system":
                context = m.get("content", "")
            if m.get("role") == "user":
                user_text = m.get("content", "")
            if m.get("role") == "tool":
                try:
                    tool_results.append(json.loads(m.get("content", "{}")))
                except Exception:
                    pass
        if any("VALIDATOR_FEEDBACK" in (m.get("content") or "")
               for m in messages if m.get("role") == "user"):
            # Vòng sửa: trả bản an toàn, không claim mới, không hỏi thêm.
            return ProviderResult(usage={"cost_usd": 0.0, "http_attempts": 0}, tool_calls=[
                ToolCall("submit_final_reply", {
                    "text": "Mình chia sẻ theo những gì mình đã trải nghiệm thôi, "
                    "còn thông tin nào chưa chắc thì mình không khẳng định nhé.",
                    "claims": [], "goal": "empathize",
                })])
        analysis = self.analyze(user_text, context)
        called = [r.get("tool") for r in tool_results]
        if "plan_turn" not in called:
            return ProviderResult(usage={"cost_usd": 0.0, "http_attempts": 0}, tool_calls=[ToolCall("plan_turn", {
                "intents": analysis["intents"],
                "pending_questions": [],
                "answered_question_keys": analysis["answered_question_keys"],
                "known_customer": {}, "known_persona": {},
                "data_needs": analysis["data_needs"],
                "goal": analysis["goal"],
                "response_direction": {
                    "answer_first": True,
                    "ask_followup": analysis["ask_followup"],
                    "followup_purpose": analysis["followup_purpose"],
                    "followup_key": analysis["followup_key"],
                },
                "resolved_reference": analysis["resolved_reference"],
            })])
        retrieval_done = any(c in called for c in (
            "get_persona_facts", "get_persona_stories",
            "get_product_facts", "get_customer_state"))
        if not retrieval_done and analysis["data_needs"] != ["none"]:
            calls = []
            if "persona_facts" in analysis["data_needs"]:
                calls.append(ToolCall("get_persona_facts", {}))
            if "persona_stories" in analysis["data_needs"]:
                calls.append(ToolCall("get_persona_stories", {}))
            if "product_facts" in analysis["data_needs"]:
                model = analysis["resolved_reference"].get("resolved_to") \
                    or "Hyundai Creta"
                calls.append(ToolCall("get_product_facts", {"model": model}))
            calls.append(ToolCall("get_customer_state", {}))
            return ProviderResult(usage={"cost_usd": 0.0, "http_attempts": 0}, tool_calls=calls)
        # Sau retrieval: đề xuất (chỉ khi chưa đề xuất).
        if not any(c.startswith("propose_") for c in called):
            proposals = self._proposals(analysis, tool_results)
            if proposals:
                return ProviderResult(usage={"cost_usd": 0.0, "http_attempts": 0}, tool_calls=proposals)
        return ProviderResult(usage={"cost_usd": 0.0, "http_attempts": 0}, tool_calls=[
            ToolCall("submit_final_reply",
                     self._final(analysis, tool_results, context))])

    def _retrieved(self, tool_results, name):
        for r in tool_results:
            if r.get("tool") == name:
                return r
        return {}

    def _proposals(self, analysis, tool_results):
        calls: list[ToolCall] = []
        types = [i["type"] for i in analysis["intents"]]
        # Customer facts từ các ý share_need / third_party.
        if "share_need" in types or "third_party" in types:
            for it in analysis["intents"]:
                if it["type"] == "third_party":
                    calls.append(ToolCall("propose_customer_fact", {
                        "subject": "third_person", "relation": it.get("relation"),
                        "predicate": "intended_user",
                        "value_raw": it.get("relation") or "người khác",
                        "evidence": it["evidence"], "source": "stated",
                        "confidence": 0.9}))
                if it["type"] == "share_need":
                    pred = "buying_interest" if "mua" in _strip(it["normalized"]) \
                        or "quan tâm mua" in it["normalized"] else "usage_need"
                    if "gia đình" in it["normalized"]:
                        pred = "usage_need"
                    calls.append(ToolCall("propose_customer_fact", {
                        "subject": "self", "relation": None, "predicate": pred,
                        "value_raw": it["normalized"], "evidence": it["evidence"],
                        "source": "stated", "confidence": 0.85}))
        facts = self._retrieved(tool_results, "get_persona_facts").get("data") or []
        fact_keys = {f.get("fact_key") for f in facts if isinstance(f, dict)}
        if any(i["type"] == "ask_persona" and "màu xe" in i["normalized"]
               for i in analysis["intents"]) and "vehicle_color" not in fact_keys:
            calls.append(ToolCall("propose_persona_fact", {
                "fact_key": "vehicle_color", "value": "trắng",
                "evidence": "khách hỏi màu xe, fact chưa có, đề xuất theo policy",
                "source": "generated"}))
        stories = self._retrieved(tool_results, "get_persona_stories").get("data") or []
        if any(i["type"] == "ask_persona" and "kỷ niệm" in i["normalized"]
               for i in analysis["intents"]) and not stories:
            calls.append(ToolCall("propose_story", {
                "title": "Chuyến về Nam Định cuối tuần",
                "summary": "Đi Creta về Nam Định cuối tuần, đường trường êm, "
                "cả nhà ngồi thoải mái.",
                "body": "Cuối tuần mình lái Creta từ Cầu Giấy về Nam Định thăm nhà. "
                "Đường trường xe chạy êm, người ngồi sau không bị mệt, "
                "cốp đủ đồ cho cả nhà.",
                "event_type": "trip", "event_date": None,
                "event_date_precision": "unknown",
                "entities": {"vehicle": "Hyundai Creta",
                             "places": ["Cầu Giấy", "Nam Định"],
                             "persons": []}}))
        if stories and any(i["type"] == "ask_persona" and
                           ("cung ai" in _strip(i["normalized"]) or
                            "cung" in _strip(i["evidence"]))
                           for i in analysis["intents"]):
            calls.append(ToolCall("propose_addendum", {
                "event_id": stories[0].get("event_id", ""),
                "content": "Chuyến đó mình đi cùng cả nhà, bố mẹ ngồi sau khen xe êm.",
                "entities": {"persons": ["gia đình"]}}))
        return calls

    def _final(self, analysis, tool_results, context):
        types = [i["type"] for i in analysis["intents"]]
        goal = analysis["goal"]
        facts = self._retrieved(tool_results, "get_persona_facts").get("data") or []
        by_key = {f.get("fact_key"): f for f in facts if isinstance(f, dict)}
        stories = self._retrieved(tool_results, "get_persona_stories").get("data") or []
        products = self._retrieved(tool_results, "get_product_facts").get("data") or []
        proposed_facts = [r for r in tool_results
                          if r.get("tool") == "propose_persona_fact"
                          and r.get("status") == "FOUND"]
        proposed_stories = [r for r in tool_results
                            if r.get("tool") == "propose_story"
                            and r.get("status") == "FOUND"]
        if goal == "stop":
            return {"text": "Ok bạn, mình dừng ở đây nhé. Khi nào cần thì nhắn lại.",
                    "claims": [], "goal": "stop"}
        if goal == "handoff":
            return {"text": "Phần đặt cọc thì mình không xác nhận được đâu bạn ạ, "
                    "để mình chuyển ý này cho bên xử lý nhé.",
                    "claims": [], "goal": "handoff"}
        if "ai_identity" in types:
            return {"text": "Mình là persona hư cấu do hệ thống tạo ra để chia sẻ "
                    "trải nghiệm, không phải người thật. Mình đang đi Creta "
                    "trong câu chuyện này thôi nhé.",
                    "claims": [], "goal": "answer_question"}
        parts: list[str] = []
        claims: list[dict] = []
        if any(i["type"] == "ask_persona" and "màu xe" in i["normalized"]
               for i in analysis["intents"]):
            f = by_key.get("vehicle_color")
            if f:
                parts.append(f"Mình đang đi xe màu {f['value']} đó bạn.")
                claims.append({"kind": "persona", "ref_id": f["id"],
                               "value": f["value"]})
            elif proposed_facts:
                pf = proposed_facts[0].get("data") or {}
                parts.append(f"Mình đang đi xe màu {pf.get('value', 'trắng')} đó bạn.")
                claims.append({"kind": "persona", "ref_id": pf.get("id"),
                               "value": pf.get("value", "trắng")})
            else:
                parts.append("Màu xe thì mình chưa chắc, để mình xem lại đã nhé.")
        if any(i["type"] == "ask_persona" and "xe persona đang đi" in i["normalized"]
               for i in analysis["intents"]):
            f = by_key.get("vehicle_model")
            if f:
                parts.append(f"Mình đang đi {f['value']} đó bạn.")
                claims.append({"kind": "persona", "ref_id": f["id"],
                               "value": f["value"]})
        if any(i["type"] == "ask_persona" and "thời điểm mua" in i["normalized"]
               for i in analysis["intents"]):
            f = by_key.get("purchased_at")
            if f:
                parts.append(f"Mình mua xe tháng {f['value']} đó.")
                claims.append({"kind": "persona", "ref_id": f["id"],
                               "value": f["value"]})
        if any(i["type"] == "ask_persona" and "kỷ niệm" in i["normalized"]
               for i in analysis["intents"]):
            if proposed_stories:
                d = proposed_stories[0].get("data") or {}
                parts.append(d.get("summary", ""))
                claims.append({"kind": "story",
                               "ref_id": d.get("event_id"),
                               "value": d.get("summary", "")})
            elif stories:
                st = stories[0]
                parts.append(st.get("summary", ""))
                claims.append({"kind": "story", "ref_id": st.get("event_id"),
                               "value": st.get("summary", "")})
        if any(i["type"] == "ask_product" for i in analysis["intents"]):
            if products:
                p = products[0]
                label = " (dữ liệu mẫu, không phải giá thực tế)" \
                    if p.get("is_sample") else ""
                parts.append(f"Theo dữ kiện mình có: {p.get('value_text')}{label}.")
                claims.append({"kind": "product", "ref_id": p.get("id"),
                               "value": p.get("value_text")})
            else:
                parts.append("Phần này mình không có dữ kiện đã xác nhận nên "
                             "không khẳng định được, bạn hỏi thêm bên bán nhé.")
        if any("quan tâm mua" in i["normalized"] for i in analysis["intents"]):
            if not parts:
                # Xe persona lấy từ fact đã truy xuất, KHÔNG lấy từ bài viết
                # và KHÔNG khẳng định nếu không có fact (uncovered check
                # sẽ chặn khẳng định không claim).
                f = by_key.get("vehicle_model")
                if f:
                    parts.append(f"Mình đang đi {f['value']} đó bạn.")
                    claims.append({"kind": "persona", "ref_id": f["id"],
                                   "value": f["value"]})
                else:
                    parts.append("Ừ, mình hiểu ý bạn rồi.")
        proposed_addenda = [r for r in tool_results
                            if r.get("tool") == "propose_addendum"
                            and r.get("status") == "FOUND"]
        if proposed_addenda:
            d = proposed_addenda[0].get("data") or {}
            addenda = d.get("addenda") or []
            if addenda:
                parts.append(addenda[-1])
                claims.append({"kind": "story", "ref_id": d.get("event_id"),
                               "value": addenda[-1]})
        if not parts:
            parts.append("Ừ, mình hiểu ý bạn rồi.")
        text = " ".join(parts)
        out: dict = {"text": text, "claims": claims, "goal": goal}
        # Không hỏi lặp câu hỏi đang pending (lấy từ get_customer_state).
        cust = self._retrieved(tool_results, "get_customer_state").get("data") or {}
        pending_keys = {q.get("question_key") for q in cust.get("questions", [])
                        if q.get("status") == "pending"}
        if analysis["ask_followup"] and goal not in ("stop", "handoff") \
                and analysis["followup_key"] not in pending_keys:
            out["followup_question"] = "Bạn thích mẫu này ở điểm nào nhất?"
            out["followup_key"] = analysis["followup_key"]
            text = text + " Bạn thích mẫu này ở điểm nào nhất?"
            out["text"] = text
        return out
