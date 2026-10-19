"""deploy/ops/watch_eval.py: alert rules, consecutive-check gating, de-duplication, destinations."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "watch_eval", Path(__file__).resolve().parents[2] / "deploy" / "ops" / "watch_eval.py")
we = importlib.util.module_from_spec(_spec)
sys.modules["watch_eval"] = we
_spec.loader.exec_module(we)


def snap(**over):
    base = {
        "collected_at": 1_000_000.0, "deploy_in_progress": False,
        "host": {"disks": [{"path": "/var/lib/docker", "used_pct": 40}], "mem_available_pct": 50, "swap_used_pct": 0},
        "containers": [{"service": "gateway", "state": "running", "health": "healthy", "oom": False, "restarts": 0}],
        "expected_services": ["gateway"],
        "backup": {"dir": "/b", "newest_age_hours": 1.0, "unverified": 0},
        "edge": {"url": "https://x/api/v1/health", "code": 200},
        "metrics": [], "metrics_error": None,
        "thresholds": {"ai_daily_budget_inr": None, "backup_max_age_hours": 8},
    }
    base.update(over)
    return base


def m(name, value, **labels):
    return {"name": name, "labels": labels, "value": value}


def keys(findings):
    return {f.key: f.severity for f in findings}


def test_healthy_snapshot_has_no_findings():
    assert we.evaluate(snap(), {}) == []


@pytest.mark.parametrize("used,sev", [(79, None), (80, "warning"), (90, "critical")])
def test_disk_thresholds(used, sev):
    f = keys(we.evaluate(snap(host={"disks": [{"path": "/d", "used_pct": used}], "mem_available_pct": 50}), {}))
    assert f.get("disk:/d") == sev


def test_backup_missing_old_and_unverified():
    assert keys(we.evaluate(snap(backup={"dir": "/b", "newest_age_hours": None, "unverified": 0}), {}))["backup:age"] == "critical"
    assert "backup:age" in keys(we.evaluate(snap(backup={"dir": "/b", "newest_age_hours": 9, "unverified": 0}), {}))
    assert keys(we.evaluate(snap(backup={"dir": "/b", "newest_age_hours": 1, "unverified": 2}), {}))["backup:unverified"] == "warning"


def test_container_down_oom_and_restart_increase():
    st = {"restarts": {"gateway": 1}}
    c = [{"service": "gateway", "state": "exited", "health": "unhealthy", "oom": True, "restarts": 3}]
    f = keys(we.evaluate(snap(containers=c), st))
    assert f["container:gateway"] == "critical" and f["oom:gateway"] == "critical" and f["restart:gateway"] == "warning"
    assert st["restarts"] == {"gateway": 3}
    assert keys(we.evaluate(snap(containers=[]), {}))["container:gateway"] == "critical"


def test_deploy_in_progress_suppresses_disruptive_checks_only():
    s = snap(deploy_in_progress=True, containers=[], edge={"url": "u", "code": 502}, metrics=None,
             backup={"dir": "/b", "newest_age_hours": None, "unverified": 0})
    assert keys(we.evaluate(s, {})) == {"backup:age": "critical"}


def test_dlq_alerts_on_growth_not_on_standing_total():
    st = {}
    s = snap(metrics=[m("zuvo_kafka_dlq_messages", 2, topic="a.DLQ")])
    assert "dlq:a.DLQ" in keys(we.evaluate(s, st))
    assert "dlq:a.DLQ" not in keys(we.evaluate(s, st))  # same total next run
    s2 = snap(metrics=[m("zuvo_kafka_dlq_messages", 3, topic="a.DLQ")])
    assert "dlq:a.DLQ" in keys(we.evaluate(s2, st))


def test_metric_rules():
    ms = [m("zuvo_kafka_consumer_lag", 150, group="g", topic="t"), m("zuvo_outbound_unknown", 1),
          m("zuvo_outbound_sending_stuck", 1), m("zuvo_outbound_pending_retry", 150), m("zuvo_rag_failed_max_attempts", 4),
          m("zuvo_rag_documents", 1, state="failed"), m("zuvo_rag_documents", 9, state="indexed"),
          m("zuvo_pg_connections", 90), m("zuvo_pg_max_connections", 100), m("zuvo_collect_ok", 0, section="kafka"),
          m("zuvo_ai_cost_inr_today", 60)]
    s = snap(metrics=ms, thresholds={"ai_daily_budget_inr": 50, "backup_max_age_hours": 8})
    f = keys(we.evaluate(s, {}))
    assert f["lag:g:t"] == "warning"
    assert f["metric:zuvo_outbound_unknown"] == "warning"
    assert f["metric:zuvo_outbound_sending_stuck"] == "critical"
    assert f["metric:zuvo_outbound_pending_retry"] == "critical"
    assert f["metric:zuvo_rag_failed_max_attempts"] == "critical"
    assert f["metric:zuvo_rag_documents"] == "warning"
    assert f["metric:pg_connections"] == "warning"
    assert f["collect:kafka"] == "warning"
    assert f["metric:ai_cost"] == "warning"


def test_ratios_need_minimum_volume():
    small = [m("zuvo_messages_24h", 2, direction="OUTBOUND", status="sent"),
             m("zuvo_messages_24h", 1, direction="OUTBOUND", status="failed")]
    assert "ratio:outbound_failed" not in keys(we.evaluate(snap(metrics=small), {}))
    big = [m("zuvo_messages_24h", 90, direction="OUTBOUND", status="sent"),
           m("zuvo_messages_24h", 10, direction="OUTBOUND", status="failed"),
           m("zuvo_messages_24h", 500, direction="INBOUND", status="RECEIVED")]
    assert "ratio:outbound_failed" in keys(we.evaluate(snap(metrics=big), {}))


def test_metrics_unavailable_is_critical():
    f = keys(we.evaluate(snap(metrics=None, metrics_error="exit 1"), {}))
    assert f == {"metrics:collect": "critical"}


def test_decide_gating_dedup_escalation_reminder_and_resolve():
    st = {}
    w = we.Finding("k", "warning", "x", for_checks=2)
    assert we.decide([w], st, 0, 3600) == ([], [])            # 1st consecutive run: not yet
    assert we.decide([w], st, 300, 3600)[0] == [w]            # 2nd: alert
    assert we.decide([w], st, 600, 3600)[0] == []             # de-duplicated
    c = we.Finding("k", "critical", "x", for_checks=2)
    assert we.decide([c], st, 900, 3600)[0] == [c]            # escalation re-alerts
    assert we.decide([c], st, 1200, 3600)[0] == []
    assert we.decide([c], st, 900 + 3600, 3600)[0] == [c]     # reminder
    assert we.decide([], st, 5000, 3600) == ([], ["k"])       # resolved
    assert st["counts"] == {} and st["alerted"] == {}


def test_render_never_exceeds_sns_subject_limit():
    subject, body = we.render([we.Finding("k", "critical", "y" * 300, runbook="r")], ["old"], "h" * 200)
    assert len(subject) < 100 and "docs/runbooks/r.md" in body and "RESOLVED old" in body


def test_main_without_destination_exits_3_and_retries(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ZUVO_ALERT_SNS_TOPIC_ARN", raising=False)
    monkeypatch.delenv("ZUVO_ALERT_WEBHOOK_URL", raising=False)
    state = tmp_path / "s.json"
    s = snap(backup={"dir": "/b", "newest_age_hours": None, "unverified": 0})
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(s)))
    assert we.main(["--state", str(state)]) == 3
    assert "no alert destination configured" in capsys.readouterr().err
    assert json.loads(state.read_text())["alerted"] == {}   # will be sent once a destination exists


def test_main_status_view_does_not_advance_state(tmp_path, monkeypatch):
    state = tmp_path / "s.json"
    s = snap(metrics=[m("zuvo_kafka_dlq_messages", 5, topic="a.DLQ")])
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(s)))
    assert we.main(["--state", str(state), "--no-notify"]) == 1
    assert not state.exists()


def test_webhook_failure_reports_class_not_url(monkeypatch):
    monkeypatch.delenv("ZUVO_ALERT_SNS_TOPIC_ARN", raising=False)
    monkeypatch.setenv("ZUVO_ALERT_WEBHOOK_URL", "http://127.0.0.1:9/secret-path")
    errs = we.notify("s", "b")
    assert errs and "secret-path" not in errs[0]
