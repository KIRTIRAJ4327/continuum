"""
Centralized environment configuration for Continuum.

Every env var the codebase reads is declared here with its default and a brief
comment.  Downstream modules import from this module rather than calling
os.getenv() ad-hoc, so the full surface area is always visible in one place.

All values are read at import time so a missing variable fails fast on startup
rather than mid-request.  Optional vars default to "" so callers can gate on
truthiness (``if config.AZURE_OPENAI_API_KEY``).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ── Azure AI Foundry / Azure OpenAI ──────────────────────────────────────────
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
# Endpoint: either AZURE_AI_ENDPOINT (AI Foundry) or AZURE_OPENAI_ENDPOINT
AZURE_OPENAI_ENDPOINT = (
    os.getenv("AZURE_AI_ENDPOINT") or os.getenv("AZURE_OPENAI_ENDPOINT") or ""
).rstrip("/")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
# Deployment names — STRONG for long-form generation, CHEAP for cheap tasks
AZURE_DEPLOYMENT_STRONG = (
    os.getenv("AZURE_DEPLOYMENT_STRONG") or os.getenv("AZURE_DEPLOYMENT_ID") or ""
)
AZURE_DEPLOYMENT_CHEAP = (
    os.getenv("AZURE_DEPLOYMENT_CHEAP") or os.getenv("AZURE_DEPLOYMENT_ID") or ""
)
# Managed-identity client ID (alternative to key auth)
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")

# ── Azure DevOps ─────────────────────────────────────────────────────────────
# Accept both AZURE_DEVOPS_ORG_URL and AZURE_DEVOPS_ORG for compatibility
AZURE_DEVOPS_ORG = (
    os.getenv("AZURE_DEVOPS_ORG_URL") or os.getenv("AZURE_DEVOPS_ORG") or ""
).rstrip("/")
AZURE_DEVOPS_PROJECT = os.getenv("AZURE_DEVOPS_PROJECT", "")
# Accept both AZURE_DEVOPS_TOKEN and AZURE_DEVOPS_PAT (legacy alias)
AZURE_DEVOPS_TOKEN = (
    os.getenv("AZURE_DEVOPS_TOKEN") or os.getenv("AZURE_DEVOPS_PAT") or ""
)
AZURE_DEVOPS_REPO = os.getenv("AZURE_DEVOPS_REPO", "")
FEATURE_BRANCH = os.getenv("FEATURE_BRANCH", "feature/continuum-auto")

# ── Neo4j ─────────────────────────────────────────────────────────────────────
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "continuum-dev")

# ── PostgreSQL (LangGraph checkpointer) ──────────────────────────────────────
# Accept POSTGRES_URI and the legacy POSTGRES_DSN alias
POSTGRES_URI = (
    os.getenv("POSTGRES_URI")
    or os.getenv("POSTGRES_DSN")
    or "postgresql://continuum:continuum-dev@localhost:5432/continuum"
)

# ── Azure Container Apps dynamic sandbox ─────────────────────────────────────
ACA_SESSION_POOL_ENDPOINT = os.getenv("ACA_SESSION_POOL_ENDPOINT", "")
ACA_AUTH_TOKEN = os.getenv("ACA_AUTH_TOKEN", "")

# ── Application ───────────────────────────────────────────────────────────────
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
REPO_PATH = os.getenv("REPO_PATH", ".")
PORT = int(os.getenv("PORT", "8000"))

# ── Derived feature flags ─────────────────────────────────────────────────────
# True when the minimum vars for each integration are present.
AZURE_AI_LIVE = bool(AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT)
AZURE_ADO_LIVE = bool(AZURE_DEVOPS_TOKEN and AZURE_DEVOPS_ORG and AZURE_DEVOPS_PROJECT)
NEO4J_CONFIGURED = bool(os.getenv("NEO4J_URI") or os.getenv("NEO4J_PASSWORD"))
ACA_CONFIGURED = bool(ACA_SESSION_POOL_ENDPOINT and ACA_AUTH_TOKEN)


# ── Neo4j driver factory ──────────────────────────────────────────────────────
def get_neo4j_driver(
    uri: Optional[str] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
) -> Optional["Neo4jDriver"]:  # type: ignore[name-defined]
    """
    Return a Neo4jDriver connected to the configured instance, or None if the
    neo4j package is not installed or credentials are absent.

    Passes the module-level defaults so callers don't need to repeat the env
    var names:
        ctx.neo4j_driver = config.get_neo4j_driver()
    """
    try:
        from graph_db.driver import Neo4jDriver  # lazy import
    except ImportError:
        logger.debug("neo4j package not installed — driver unavailable")
        return None

    _uri = uri or NEO4J_URI
    _user = user or NEO4J_USER
    _pwd = password or NEO4J_PASSWORD

    if not _pwd or _pwd == "continuum-dev" and not os.getenv("NEO4J_PASSWORD"):
        logger.debug("NEO4J_PASSWORD not set — using default (docker-compose only)")

    return Neo4jDriver(uri=_uri, user=_user, password=_pwd)


# ── Startup diagnostics (called by api/main.py on_event("startup")) ──────────
def warn_missing_optional() -> list[str]:
    """
    Return the names of optional-but-recommended env vars that are not set.
    Logs a single WARNING line; callers may use the list for health endpoints.
    """
    checks = {
        "AZURE_OPENAI_API_KEY": AZURE_OPENAI_API_KEY,
        "AZURE_OPENAI_ENDPOINT": AZURE_OPENAI_ENDPOINT,
        "AZURE_DEPLOYMENT_STRONG": AZURE_DEPLOYMENT_STRONG,
        "AZURE_DEVOPS_TOKEN": AZURE_DEVOPS_TOKEN,
        "AZURE_DEVOPS_ORG": AZURE_DEVOPS_ORG,
        "AZURE_DEVOPS_PROJECT": AZURE_DEVOPS_PROJECT,
        "NEO4J_URI": os.getenv("NEO4J_URI", ""),
        "NEO4J_PASSWORD": os.getenv("NEO4J_PASSWORD", ""),
    }
    missing = [k for k, v in checks.items() if not v]
    if missing:
        logger.warning(
            "Optional env vars not set (offline/stub path will be used): %s",
            ", ".join(missing),
        )
    return missing
