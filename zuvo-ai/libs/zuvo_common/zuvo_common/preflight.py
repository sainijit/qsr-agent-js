"""Configuration validation shared by the deploy-time CLI (`scripts/preflight.py`) and service startup.

Reports variable NAMES only — never values — so output is safe for terminals, CI logs and tickets.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

ERROR, WARN = "ERROR", "WARN"

# variable -> minimum length (0 = presence only)
REQUIRED_PRODUCTION: dict[str, int] = {
    "POSTGRES_PASSWORD": 16,
    "ZUVO_APP_DB_PASSWORD": 16,
    "JWT_SECRET": 32,
    "RAG_SERVICE_TOKEN": 32,
    "META_VERIFY_TOKEN": 16,
    "META_APP_SECRET": 16,
    "META_SYSTEM_USER_TOKEN": 0,
    "GEMINI_API_KEY": 0,
    "PUBLIC_DOMAIN": 0,
    "ACME_EMAIL": 0,
}
REQUIRED_DEVELOPMENT: dict[str, int] = {"POSTGRES_PASSWORD": 1, "ZUVO_APP_DB_PASSWORD": 8, "JWT_SECRET": 16}

_PLACEHOLDER_MARKERS = ("change-me", "changeme", "replace-me", "placeholder", "example", "your-", "your_", "xxxx", "todo", "generate-with", "generate_with")
_KNOWN_WEAK = {"zuvo@2026", "password", "admin", "secret", "123456", "change-me-now"}


@dataclass(frozen=True)
class Issue:
    level: str
    name: str
    problem: str

    def __str__(self) -> str:
        return f"{self.level:5} {self.name}: {self.problem}"


def _truthy(v: Optional[str]) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _looks_placeholder(v: str) -> bool:
    low = v.strip().lower()
    if "<" in low and ">" in low:  # template markers such as <openssl rand -hex 32>
        return True
    return any(m in low for m in _PLACEHOLDER_MARKERS) or low.startswith("mock_") or low in _KNOWN_WEAK


def check_env(env: Mapping[str, str], profile: str = "production") -> list[Issue]:
    """Validate a deployment environment (merged .env + process env). Pure function, unit-tested."""
    issues: list[Issue] = []
    prod = profile == "production"
    for name, min_len in (REQUIRED_PRODUCTION if prod else REQUIRED_DEVELOPMENT).items():
        v = (env.get(name) or "").strip()
        if not v:
            issues.append(Issue(ERROR, name, "missing or empty"))
        elif prod and _looks_placeholder(v):
            issues.append(Issue(ERROR, name, "looks like a placeholder or well-known weak value"))
        elif min_len and len(v) < min_len:
            issues.append(Issue(ERROR, name, f"too short (minimum {min_len} characters)"))

    if not prod:
        return issues

    if (env.get("APP_ENV") or "").strip().lower() != "production":
        issues.append(Issue(ERROR, "APP_ENV", "must be 'production' (enables the runtime safety guard)"))
    if _truthy(env.get("AUTH_DEV_LOGIN")):
        issues.append(Issue(ERROR, "AUTH_DEV_LOGIN", "must be false: dev login issues tokens without a password"))
    if _truthy(env.get("ENABLE_SIMULATOR")):
        issues.append(Issue(ERROR, "ENABLE_SIMULATOR", "must be false in production"))
    if "REQUIRE_HMAC_VERIFICATION" in env and not _truthy(env.get("REQUIRE_HMAC_VERIFICATION")):
        issues.append(Issue(ERROR, "REQUIRE_HMAC_VERIFICATION", "must be true: unsigned webhooks would be accepted"))
    if "META_DRY_RUN" not in env or _truthy(env.get("META_DRY_RUN")):
        issues.append(Issue(WARN, "META_DRY_RUN", "is true/unset: replies will NOT be sent to WhatsApp"))

    origins = [o.strip() for o in (env.get("ALLOWED_ORIGINS") or "").split(",") if o.strip()]
    if not origins:
        issues.append(Issue(ERROR, "ALLOWED_ORIGINS", "missing: set to https://<PUBLIC_DOMAIN>"))
    elif any(_looks_placeholder(o) for o in origins):
        issues.append(Issue(ERROR, "ALLOWED_ORIGINS", "looks like a placeholder"))
    elif any(not o.startswith("https://") or "localhost" in o or "127.0.0.1" in o or o == "*" for o in origins):
        issues.append(Issue(ERROR, "ALLOWED_ORIGINS", "must list only https origins (no localhost, no '*')"))

    dom = (env.get("PUBLIC_DOMAIN") or "").strip().lower()
    if dom and (dom.startswith("http") or "/" in dom or dom in ("localhost", "app.example.com") or dom.endswith(".example.com")):
        issues.append(Issue(ERROR, "PUBLIC_DOMAIN", "must be a bare real hostname (no scheme/path, not example.com)"))

    pw = (env.get("ZUVO_ONBOARD_DEFAULT_PASSWORD") or "").strip()
    if pw:
        if pw.lower() in _KNOWN_WEAK or _looks_placeholder(pw) or len(pw) < 12:
            issues.append(Issue(ERROR, "ZUVO_ONBOARD_DEFAULT_PASSWORD", "weak/well-known value; unset it and issue per-user passwords"))
        else:
            issues.append(Issue(WARN, "ZUVO_ONBOARD_DEFAULT_PASSWORD", "set: every NEW bundle user gets this same initial password"))

    tag = (env.get("IMAGE_TAG") or "").strip()
    if tag in ("", "local", "latest", "dev"):
        issues.append(Issue(WARN, "IMAGE_TAG", "unset or mutable: use an immutable tag (git commit) so rollback can restart the previous images"))

    for name in ("HTTP_PROXY", "HTTPS_PROXY"):
        if (env.get(name) or "").strip():
            issues.append(Issue(WARN, name, "set: confirm the production VM really needs an outbound proxy"))
    return issues


def runtime_errors(settings) -> list[str]:
    """Checks a running service can enforce itself (APP_ENV=production). Returns problem strings with names only."""
    errs: list[str] = []
    if settings.auth_dev_login:
        errs.append("AUTH_DEV_LOGIN must be false")
    if settings.enable_simulator:
        errs.append("ENABLE_SIMULATOR must be false")
    if not settings.require_hmac_verification:
        errs.append("REQUIRE_HMAC_VERIFICATION must be true")
    if len(settings.jwt_secret or "") < 32:
        errs.append("JWT_SECRET must be at least 32 characters")
    svc = settings.service_name
    if svc == "gateway" and not (settings.meta_app_secret and settings.meta_verify_token):
        errs.append("gateway needs META_APP_SECRET and META_VERIFY_TOKEN")
    if svc == "dispatch" and not settings.meta_dry_run and not settings.meta_system_user_token:
        errs.append("dispatch needs META_SYSTEM_USER_TOKEN when META_DRY_RUN=false")
    if svc == "agent-worker" and settings.rag_url and not settings.rag_service_token:
        errs.append("agent-worker needs RAG_SERVICE_TOKEN when RAG_URL is set")
    if svc == "dashboard-api" and any(o == "*" or not o.startswith("https://") for o in settings.origins):
        errs.append("ALLOWED_ORIGINS must list only https origins")
    return errs
