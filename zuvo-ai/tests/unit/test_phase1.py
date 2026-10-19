"""Phase 1 pure logic: visit dates, price matching, agent proposals/decisions, learning gate, opt-out, bundles, dispatch."""
from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import pytest

from zuvo_common.bundle import LearningCfg, load_bundle
from zuvo_common.dates import parse_iso_date, resolve_visit
from zuvo_common.events import Extraction, Intent, PriceQuery, TemplateSpec
from zuvo_common.leads import is_opt_out
from zuvo_common.learning import APPROVED, EDITED, REJECTED, AutonomyState, edit_distance, record_outcome
from zuvo_common.onboarding import validate_bundle
from zuvo_common.outbound import outbound_envelope
from zuvo_common.pricing import match_prices, parse_pricing_csv
from zuvo_common.tenancy import TenantRouting

ROOT = Path(__file__).resolve().parents[2]
WED = date(2026, 10, 14)  # a Wednesday


@pytest.fixture(scope="module")
def furniture():
    return load_bundle(ROOT / "tenants" / "jaipur-furniture")


@pytest.fixture(scope="module")
def furniture_items(furniture):
    rows, errors = parse_pricing_csv(furniture.pricing_text)
    assert not errors
    return [r.__dict__ for r in rows]


# ------------------------------------------------------------------ dates
@pytest.mark.parametrize("text,expected,precision,window", [
    ("main Saturday ko showroom aaunga shaam ko", date(2026, 10, 17), "WEEKDAY", "evening"),
    ("kal subah aayenge", date(2026, 10, 15), "EXACT", "morning"),
    ("parso aa raha hoon", date(2026, 10, 16), "EXACT", None),
    ("I will come on 20th", date(2026, 10, 20), "EXACT", None),
    ("5 tarikh ko aaunga 5 baje", date(2026, 11, 5), "EXACT", "17:00"),
    ("20 oct ko aayenge", date(2026, 10, 20), "EXACT", None),
])
def test_resolve_visit(text, expected, precision, window):
    w = resolve_visit(text, WED)
    assert w is not None, text
    assert (w.date, w.precision) == (expected, precision)
    if window:
        assert w.time_window == window


def test_resolve_visit_next_week_and_none():
    w = resolve_visit("next week aayenge", WED)
    assert w and w.precision == "WEEK" and w.date > WED
    assert resolve_visit("sofa ka price batao", WED) is None


def test_parse_iso_date_bounds():
    assert parse_iso_date("2026-10-20", WED) == date(2026, 10, 20)
    assert parse_iso_date("2026-10-01", WED) is None  # past
    assert parse_iso_date("2028-01-01", WED) is None  # too far
    assert parse_iso_date("garbage", WED) is None


# ------------------------------------------------------------------ pricing
def test_match_prices_ignores_generic_words(furniture_items):
    top = match_prices("sofa set", furniture_items)
    assert {m.item["sku"] for m in top[:2]} == {"SOFA-L-FAB", "SOFA-3S-TEAK"} and top[0].score >= 0.7
    assert match_prices("dining set", furniture_items)[0].item["sku"].startswith("DINE")
    assert match_prices("sofa ka latest design", furniture_items)[0].score >= 0.7
    assert match_prices("L shape sofa", furniture_items)[0].item["sku"] == "SOFA-L-FAB"


# ------------------------------------------------------------------ agent logic
def _x(item: str, conf=0.95, discount=False):
    return Extraction(intent=Intent.INQUIRY_PRICE_STOCK, confidence=conf,
                      price_query=PriceQuery(item_text=item, discount_requested=discount))


def test_price_proposal_grounded(furniture, furniture_items):
    from agent_worker.logic import price_proposal

    p = price_proposal(furniture.config, furniture_items, _x("L shape sofa"), "L shape sofa price?", WED)
    assert p.task == "price" and p.topic == "price:sofa"
    assert "L-Shape Fabric Sofa" in p.text and "55,000" in p.text and "78,000" in p.text
    assert p.retrieved["skus"] == ["SOFA-L-FAB"] and p.confidence > 0.85
    assert "price_missing" not in p.reasons


def test_price_proposal_unknown_and_discount(furniture, furniture_items):
    from agent_worker.logic import price_proposal

    p = price_proposal(furniture.config, furniture_items, _x("rocket engine"), "rocket engine price", WED)
    assert "price_missing" in p.reasons and p.text == furniture.config.agent.holding_reply and p.confidence == 0
    d = price_proposal(furniture.config, furniture_items, _x("L shape sofa", discount=True), "discount milega?", WED)
    assert "discount_request" in d.reasons


def test_price_proposal_reuses_approved_example(furniture, furniture_items):
    from agent_worker.logic import price_proposal

    ex = [{"id": "e1", "topic": "price:sofa", "question_text": "L shape sofa price?", "answer_text": "Owner wording ₹55,000 – ₹78,000",
           "price_item_skus": ["SOFA-L-FAB"], "weight": 2.0}]
    p = price_proposal(furniture.config, furniture_items, _x("L shape sofa"), "L shape sofa price?", WED, ex)
    assert p.text == "Owner wording ₹55,000 – ₹78,000" and p.retrieved["example_id"] == "e1"


def test_decide_gates(furniture):
    from agent_worker.logic import Proposal, decide

    cfg = furniture.config
    good = Proposal("faq", "faq:x", "text", 0.95)
    assert decide(cfg, good, "off", flagged=False, topic_auto_enabled=True) == ("SKIPPED", ["mode_off"])
    assert decide(cfg, good, "auto", flagged=False, topic_auto_enabled=True) == ("AUTO_SENT", [])
    d, r = decide(cfg, good, "auto", flagged=False, topic_auto_enabled=False)
    assert d == "PENDING_APPROVAL" and r == ["learning_gate"]
    d, r = decide(cfg, Proposal("faq", "faq:x", "t", 0.3), "auto", flagged=True, topic_auto_enabled=True)
    assert d == "PENDING_APPROVAL" and "low_confidence" in r and any(x.startswith("confidence<") for x in r)


def test_choose_task():
    from agent_worker.logic import choose_task

    assert choose_task(_x("sofa"), "tier2") == "price"
    assert choose_task(Extraction(intent=Intent.FAQ, confidence=0.9), "tier2") == "faq"
    assert choose_task(Extraction(intent=Intent.VISIT_PLAN, confidence=0.9), "tier2") is None
    assert choose_task(Extraction(intent=Intent.GENERAL_CHAT, confidence=0.9), "tier0") == "greeting"
    assert choose_task(Extraction(intent=Intent.COMPLAINT_RETURN, confidence=0.9), "tier2") is None
    assert choose_task(None, "none") is None


# ------------------------------------------------------------------ learning
def test_learning_gate_promotes_and_demotes():
    cfg = LearningCfg(min_examples_before_auto=3, auto_promote_rate=0.9, window=20)
    s = AutonomyState([], 0, 0, 0, False)
    for _ in range(2):
        s = record_outcome(s, APPROVED, cfg)
    assert not s.auto_enabled
    s = record_outcome(s, APPROVED, cfg)
    assert s.auto_enabled and s.approved_unchanged == 3
    s = record_outcome(s, REJECTED, cfg)
    assert not s.auto_enabled and s.rejected == 1
    s = record_outcome(s, EDITED, cfg)
    assert not s.auto_enabled  # 3/5 approved < 90%


def test_edit_distance():
    assert edit_distance("kitten", "sitting") == 3
    assert edit_distance("same", "same") == 0


def test_opt_out_is_whole_message_only():
    kw = ["stop", "band karo", "mat bhejo"]
    assert is_opt_out("STOP", kw) and is_opt_out("  Band karo!! ", kw)
    assert not is_opt_out("stop by tomorrow", kw) and not is_opt_out("", kw)


# ------------------------------------------------------------------ bundles / outbound / dispatch
def test_all_bundles_validate():
    bundles = [load_bundle(p) for p in sorted((ROOT / "tenants").iterdir()) if p.is_dir() and not p.name.startswith("_")]
    assert len(bundles) >= 4
    for b in bundles:
        assert validate_bundle(b, [o for o in bundles if o is not b]) == [], b.slug


def test_outbound_envelope_is_deterministic_with_request_key():
    line = TenantRouting(str(ROOT / "config" / "tenant-routing.yaml")).resolve("PNID_JF_01")
    assert line is not None and line.tenant_slug == "jaipur-furniture"
    a = outbound_envelope(line.tenant_id, line, "919800000000", "hi", requested_by="t", sent_by="agent", request_key="run:1")
    b = outbound_envelope(line.tenant_id, line, "919800000000", "hi", requested_by="t", sent_by="agent", request_key="run:1")
    c = outbound_envelope(line.tenant_id, line, "919800000000", "hi", requested_by="t")
    assert a.payload["request_id"] == b.payload["request_id"] == a.event_id
    assert c.payload["request_id"] != a.payload["request_id"] and a.payload["sent_by"] == "agent"


def test_dispatch_template_payload_and_simulated_line():
    from dispatch.main import MetaWhatsAppClient

    class S:
        meta_dry_run, meta_system_user_token = False, "real-token"
        meta_graph_base_url, meta_api_version = "https://graph.invalid", "v25.0"

    c = MetaWhatsAppClient(S())
    sent = {}

    async def fake_post(pnid, to, body):
        sent.update(body)
        from dispatch.main import SendResult
        return SendResult(True, "wamid.x", False)

    c._post = fake_post
    asyncio.run(c.send_template("123", "9198", TemplateSpec(name="visit_reminder", language="en", body_params=["Ravi", "Sat"])))
    assert sent["type"] == "template" and sent["template"]["name"] == "visit_reminder"
    assert sent["template"]["components"][0]["parameters"] == [{"type": "text", "text": "Ravi"}, {"type": "text", "text": "Sat"}]
    r = asyncio.run(MetaWhatsAppClient(S()).send_text("PNID_JF_01", "9198", "hi"))
    assert r.success and r.dry_run and r.wamid.startswith("wamid.SIM.")
