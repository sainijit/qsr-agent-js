"""Production-readiness guards: preflight config validation, runtime refusal, liveness heartbeat, graceful consumer stop."""
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from zuvo_common import bus as bus_mod
from zuvo_common import health, runtime
from zuvo_common.preflight import ERROR, WARN, check_env, runtime_errors
from zuvo_common.settings import Settings, UnsafeProductionConfig, get_settings

ROOT = Path(__file__).resolve().parents[2]

GOOD = {
    "APP_ENV": "production", "POSTGRES_PASSWORD": "Pg9fK2mQ7xL4vN8wR3tY", "ZUVO_APP_DB_PASSWORD": "Ap7hJ3nB9cV2mX5zQ8wE",
    "JWT_SECRET": "j" * 20 + "K8vQ2mX7nB4cL9zR5tW3yH6", "RAG_SERVICE_TOKEN": "r" * 20 + "T5yU8iO2pA6sD9fG3hJ7kL4",
    "META_VERIFY_TOKEN": "vT8kQ3mN7xB2cL5z", "META_APP_SECRET": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
    "META_SYSTEM_USER_TOKEN": "EAAG" + "Q" * 60, "GEMINI_API_KEY": "AIza" + "B" * 35,
    "PUBLIC_DOMAIN": "app.zuvocloud.in", "ACME_EMAIL": "ops@zuvocloud.in", "ALLOWED_ORIGINS": "https://app.zuvocloud.in",
    "AUTH_DEV_LOGIN": "false", "ENABLE_SIMULATOR": "false", "REQUIRE_HMAC_VERIFICATION": "true", "META_DRY_RUN": "false",
}


def names(issues, level=ERROR):
    return {i.name for i in issues if i.level == level}


def test_preflight_accepts_a_complete_production_env():
    assert [i for i in check_env(GOOD) if i.level == ERROR] == []


@pytest.mark.parametrize("override,expected", [
    ({"JWT_SECRET": ""}, "JWT_SECRET"),
    ({"JWT_SECRET": "short"}, "JWT_SECRET"),
    ({"POSTGRES_PASSWORD": "change-me-please-now-ok"}, "POSTGRES_PASSWORD"),
    ({"META_SYSTEM_USER_TOKEN": "mock_token"}, "META_SYSTEM_USER_TOKEN"),
    ({"AUTH_DEV_LOGIN": "true"}, "AUTH_DEV_LOGIN"),
    ({"ENABLE_SIMULATOR": "1"}, "ENABLE_SIMULATOR"),
    ({"REQUIRE_HMAC_VERIFICATION": "false"}, "REQUIRE_HMAC_VERIFICATION"),
    ({"APP_ENV": "development"}, "APP_ENV"),
    ({"ALLOWED_ORIGINS": "http://app.zuvocloud.in"}, "ALLOWED_ORIGINS"),
    ({"ALLOWED_ORIGINS": "*"}, "ALLOWED_ORIGINS"),
    ({"PUBLIC_DOMAIN": "https://app.zuvocloud.in"}, "PUBLIC_DOMAIN"),
    ({"PUBLIC_DOMAIN": "app.example.com"}, "PUBLIC_DOMAIN"),
    ({"ZUVO_ONBOARD_DEFAULT_PASSWORD": "Zuvo@2026"}, "ZUVO_ONBOARD_DEFAULT_PASSWORD"),
    ({"POSTGRES_PASSWORD": "<openssl rand -hex 32 output here>"}, "POSTGRES_PASSWORD"),
    ({"ALLOWED_ORIGINS": "https://<app.your-domain.in>"}, "ALLOWED_ORIGINS"),
])
def test_preflight_rejects_unsafe_values(override, expected):
    assert expected in names(check_env({**GOOD, **override}))


def test_preflight_warns_on_mutable_image_tag():
    assert "IMAGE_TAG" in names(check_env(GOOD), WARN)
    assert "IMAGE_TAG" in names(check_env({**GOOD, "IMAGE_TAG": "latest"}), WARN)
    assert "IMAGE_TAG" not in names(check_env({**GOOD, "IMAGE_TAG": "dd9f021"}), WARN)


def test_preflight_warns_on_dry_run_and_never_echoes_values():
    env = {**GOOD, "META_DRY_RUN": "true", "JWT_SECRET": "tiny-secret-value"}
    issues = check_env(env)
    assert "META_DRY_RUN" in names(issues, WARN)
    rendered = "\n".join(map(str, issues))
    for v in env.values():
        if len(v) > 5 and v not in ("production", "false", "true"):
            assert v not in rendered


def test_preflight_cli_exit_codes(tmp_path):
    good = tmp_path / "good.env"
    good.write_text("".join(f"{k}={v}\n" for k, v in GOOD.items()))
    good.chmod(0o600)
    bad = tmp_path / "bad.env"
    bad.write_text("APP_ENV=production\nJWT_SECRET=x\n")
    env = {k: v for k, v in os.environ.items() if k not in GOOD}
    cli = [sys.executable, str(ROOT / "scripts/preflight.py"), "--env-file"]
    ok = subprocess.run(cli + [str(good)], capture_output=True, text=True, env=env)
    assert ok.returncode == 0, ok.stdout
    ko = subprocess.run(cli + [str(bad)], capture_output=True, text=True, env=env)
    assert ko.returncode == 1 and "JWT_SECRET" in ko.stdout and "=x" not in ko.stdout


def test_preflight_env_parser_handles_inline_comments_and_quotes(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("pf_cli", ROOT / "scripts/preflight.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    f = tmp_path / "x.env"
    f.write_text('AUTH_DEV_LOGIN=true          # dev-only\nA="quoted # not comment"\nB=\'single\'  # c\nexport C=v#x\n')
    env = cli.parse_env_file(f)
    assert env == {"AUTH_DEV_LOGIN": "true", "A": "quoted # not comment", "B": "single", "C": "v#x"}
    assert "AUTH_DEV_LOGIN" in names(check_env({**GOOD, **env}))


def test_env_example_is_rejected_for_production():
    import importlib.util
    spec = importlib.util.spec_from_file_location("pf_cli2", ROOT / "scripts/preflight.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    bad = names(check_env(cli.parse_env_file(ROOT / ".env.example")))
    assert {"JWT_SECRET", "POSTGRES_PASSWORD", "AUTH_DEV_LOGIN", "ENABLE_SIMULATOR"} <= bad


def _settings(**kw):
    base = dict(auth_dev_login=False, enable_simulator=False, require_hmac_verification=True, jwt_secret="s" * 40,
                service_name="persister", meta_app_secret="", meta_verify_token="", meta_dry_run=True,
                meta_system_user_token="", rag_url="", rag_service_token="", origins=["https://app.zuvocloud.in"])
    return SimpleNamespace(**{**base, **kw})


def test_runtime_guard_per_service():
    assert runtime_errors(_settings()) == []
    assert runtime_errors(_settings(auth_dev_login=True))
    assert runtime_errors(_settings(service_name="gateway"))  # missing Meta secrets
    assert runtime_errors(_settings(service_name="dispatch", meta_dry_run=False))
    assert runtime_errors(_settings(service_name="agent-worker", rag_url="http://rag"))
    assert runtime_errors(_settings(service_name="dashboard-api", origins=["http://localhost:3000"]))


def test_get_settings_refuses_unsafe_production(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_DEV_LOGIN", "true")
    monkeypatch.setenv("JWT_SECRET", "s" * 40)
    try:
        with pytest.raises(UnsafeProductionConfig, match="AUTH_DEV_LOGIN"):
            get_settings()
        monkeypatch.setenv("APP_ENV", "development")
        assert get_settings().auth_dev_login is True  # dev stays permissive
    finally:
        get_settings.cache_clear()
    assert isinstance(Settings(), Settings)


def test_heartbeat_and_health_probe(tmp_path, monkeypatch):
    hb = tmp_path / "hb"
    monkeypatch.setenv(runtime.HEARTBEAT_ENV, str(hb))
    assert health.main([]) == 1  # no heartbeat yet
    runtime.heartbeat(force=True)
    assert health.main(["--max-age", "60"]) == 0
    old = time.time() - 600
    os.utime(hb, (old, old))
    assert health.main(["--max-age", "60"]) == 1


class _FakeMsg(SimpleNamespace):
    pass


class _FakeConsumer:
    """Mimics AIOKafkaConsumer.getmany/commit for the consume loop."""

    def __init__(self, *topics, **kw):
        self.queue = [_FakeMsg(topic="t", key=b"k", value=str(i).encode(), headers=[]) for i in range(3)]
        self.commits = 0
        self.stopped = False
        _FakeConsumer.last = self

    async def start(self):
        return None

    async def stop(self):
        self.stopped = True

    async def getmany(self, timeout_ms=0, max_records=None):
        if not self.queue:
            await asyncio.sleep(0.01)
            return {}
        return {"tp": [self.queue.pop(0)]}

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_consume_finishes_in_flight_message_then_stops(monkeypatch, tmp_path):
    monkeypatch.setenv(runtime.HEARTBEAT_ENV, str(tmp_path / "hb"))
    monkeypatch.setattr(bus_mod, "AIOKafkaConsumer", _FakeConsumer)
    kbus = bus_mod.KafkaEventBus("unused:9092", "test")
    handled = []

    async def handler(rec):
        handled.append(rec.value)
        if len(handled) == 2:
            kbus.request_stop()  # SIGTERM arrives while message #2 is in flight
            await asyncio.sleep(0.01)

    await asyncio.wait_for(kbus.consume(["t"], "g", handler), timeout=5)
    c = _FakeConsumer.last
    assert handled == [b"0", b"1"]  # #2 completed, #3 never started
    assert c.commits == 2  # the in-flight message was committed before exit
    assert c.stopped
    assert (tmp_path / "hb").exists()


def test_access_log_redaction_hides_tokens():
    import logging

    from zuvo_common.logging import install_access_log_redaction, redact_query_secrets

    assert redact_query_secrets("/s?access_token=eyJabc.def&x=1") == "/s?access_token=<redacted>&x=1"
    assert "secret" not in redact_query_secrets("/webhook?hub.mode=subscribe&hub.verify_token=secret&hub.challenge=1")
    install_access_log_redaction()
    install_access_log_redaction()  # idempotent
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4", "GET", "/api/v1/dashboard/stream?access_token=eyJ.secret.sig", "1.1", 200), None)
    for f in logging.getLogger("uvicorn.access").filters:
        f.filter(rec)
    assert "eyJ.secret.sig" not in rec.getMessage() and "<redacted>" in rec.getMessage()
