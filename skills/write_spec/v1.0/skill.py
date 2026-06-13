"""
write_spec skill — generates a detailed feature specification.

Live path:  Azure AI call to generate proper spec from request + story.
Offline path: deterministic spec derived from the request text.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def write_spec(request: str, story: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate a detailed feature specification from the request and BSA story.

    Args:
        request: Plain-English feature request.
        story:   Story dict from the BSA agent (title, description, …).

    Returns:
        {
            "overview": str,
            "functional": [str],
            "non_functional": [{"area": str, "requirement": str}],
            "api_endpoints": [str],
            "data_entities": [str],
            "out_of_scope": [str],
            "assumptions": [str],
        }
    """
    title = story.get("title", request[:80])

    # Try live LLM call first
    llm_result = await _azure_llm(
        prompt=(
            f"Write a detailed feature specification for:\n\n"
            f"Title: {title}\n"
            f"Request: {request}\n\n"
            "Return ONLY a JSON object with keys: overview, functional (list of strings), "
            "non_functional (list of {area, requirement} objects), api_endpoints (list), "
            "data_entities (list), out_of_scope (list), assumptions (list)."
        ),
        system=(
            "You are a senior business systems analyst. Generate precise, actionable "
            "feature specifications. Return valid JSON only — no prose, no markdown."
        ),
    )
    if llm_result:
        try:
            parsed = _safe_json(llm_result)
            if isinstance(parsed, dict) and parsed.get("overview"):
                logger.info("[write_spec] LLM spec generated for: %s", title)
                return parsed
        except Exception:  # noqa: BLE001
            pass

    # Deterministic fallback
    return _derive_spec(request, title)


# --------------------------------------------------------------------------- #
# Offline derivation
# --------------------------------------------------------------------------- #
def _derive_spec(request: str, title: str) -> Dict[str, Any]:
    """Build a spec by extracting nouns/verbs from the request text."""
    entities = _extract_entities(request)
    resource = entities[0] if entities else "Resource"
    resource_pl = resource.lower() + "s"

    functional: List[str] = [
        f"Users shall be able to view a {title.lower()}.",
        f"The system shall support creating, reading, updating, and deleting {resource_pl}.",
        "The system shall return paginated results for list endpoints (default page size: 20).",
        "All mutating operations shall require authentication.",
        "The system shall emit audit log entries for all write operations.",
    ]
    non_functional = [
        {"area": "Performance", "requirement": "p99 API response time < 300 ms under 1 000 rps"},
        {"area": "Availability", "requirement": "99.9% monthly uptime"},
        {"area": "Security",     "requirement": "OAuth 2.0 / JWT authentication; OWASP Top-10 compliant"},
        {"area": "Scalability",  "requirement": "Horizontal scale-out via Kubernetes HPA"},
    ]
    endpoints = [
        f"GET    /{resource_pl}",
        f"POST   /{resource_pl}",
        f"GET    /{resource_pl}/{{id}}",
        f"PUT    /{resource_pl}/{{id}}",
        f"DELETE /{resource_pl}/{{id}}",
    ]
    return {
        "overview": (
            f"{title}: {request[:300]}."
        ),
        "functional": functional,
        "non_functional": non_functional,
        "api_endpoints": endpoints,
        "data_entities": entities or [resource],
        "out_of_scope": [
            "Mobile app (handled by separate workstream)",
            "Third-party SSO integration (future iteration)",
        ],
        "assumptions": [
            "PostgreSQL 15 is the primary database.",
            "Deployment target is Azure Container Apps.",
            "Feature flags are managed via Azure App Configuration.",
        ],
    }


def _extract_entities(text: str) -> List[str]:
    """Naive noun extraction — picks capitalised words or domain keywords."""
    stop = {
        "build", "create", "add", "implement", "make", "with", "and", "the",
        "a", "an", "for", "to", "of", "in", "on", "at", "by", "is", "are",
    }
    words = re.findall(r"\b[A-Z][a-z]{2,}\b|\b[a-z]{4,}\b", text)
    seen: List[str] = []
    for w in words:
        lw = w.lower()
        if lw not in stop and lw not in [s.lower() for s in seen]:
            seen.append(w.capitalize())
        if len(seen) >= 4:
            break
    return seen or ["Resource"]


# --------------------------------------------------------------------------- #
# Shared Azure AI helper (lazy import, no crash on missing deps)
# --------------------------------------------------------------------------- #
async def _azure_llm(prompt: str, system: str = "") -> Optional[str]:
    """Try an Azure AI completion; return None if unconfigured or on error."""
    import os

    api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    endpoint = (
        os.getenv("AZURE_AI_ENDPOINT") or os.getenv("AZURE_OPENAI_ENDPOINT") or ""
    ).rstrip("/")
    deployment = (
        os.getenv("AZURE_DEPLOYMENT_STRONG") or os.getenv("AZURE_DEPLOYMENT_ID") or ""
    )
    if not (api_key and endpoint and deployment):
        return None
    try:
        from langchain_azure_ai.chat_models import AzureChatCompletions  # type: ignore
        from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore

        model = AzureChatCompletions(
            azure_endpoint=endpoint,
            azure_deployment=deployment,
            api_key=api_key,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
            max_tokens=2048,
        )
        msgs = (
            ([SystemMessage(content=system)] if system else [])
            + [HumanMessage(content=prompt)]
        )
        resp = await model.ainvoke(msgs)
        return getattr(resp, "content", None)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[write_spec] LLM call skipped: %s", exc)
        return None


def _safe_json(text: str) -> Any:
    """Strip markdown fences and parse JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"```$", "", text).strip()
    return json.loads(text)
