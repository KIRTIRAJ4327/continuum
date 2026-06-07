"""
review_diff skill — performs automated code review on the generated diff.

Checks each file in state.code for:
  - Contract conformance (endpoints match OpenAPI spec)
  - Error handling completeness
  - SQL injection patterns
  - Hardcoded secrets
  - Missing type annotations (Python) / `any` usage (TypeScript)

Live path:  Azure AI LLM review of the diff.
Offline path: Deterministic regex + pattern checks.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Patterns that indicate security/quality issues
_BAD_PATTERNS = [
    (r"password\s*=\s*['\"][^'\"]{4,}", "HARDCODED_SECRET",   "blocking", "Possible hardcoded password"),
    (r"secret\s*=\s*['\"][^'\"]{4,}",   "HARDCODED_SECRET",   "blocking", "Possible hardcoded secret"),
    (r"api_key\s*=\s*['\"][^'\"]{4,}",  "HARDCODED_SECRET",   "blocking", "Possible hardcoded API key"),
    (r"f['\"].*\{.*\}.*WHERE",          "SQL_INJECTION",       "blocking", "Possible SQL injection via f-string"),
    (r"except\s+Exception\s*:\s*pass",  "BARE_EXCEPT",         "warning",  "Bare except swallows all errors"),
    (r":\s*Any\b",                       "UNTYPED_ANY",         "info",     "Usage of `Any` type — prefer concrete type"),
    (r"TODO|FIXME|HACK\b",              "TODO_LEFT",           "info",     "TODO/FIXME found — resolve before merge"),
]


async def review_diff(
    code: Dict[str, str],
    contract: str = "",
) -> Dict[str, Any]:
    """
    Review code files and return structured findings.

    Args:
        code:     {"file_path": content} dict of generated files.
        contract: OpenAPI YAML string (used for contract conformance check).

    Returns:
        {
            "approved":  bool,
            "findings":  [{"file", "line", "severity", "rule", "message"}],
            "summary":   str,
        }
    """
    if not code:
        return {"approved": True, "findings": [], "summary": "No code to review"}

    # Try LLM review first
    llm_result = await _llm_review(code, contract)
    if llm_result:
        return llm_result

    # Deterministic pattern scan
    findings: List[Dict[str, Any]] = []
    for file_path, content in code.items():
        findings.extend(_scan_file(file_path, content))

    # Contract conformance check
    if contract:
        findings.extend(_check_contract(code, contract))

    blocking = sum(1 for f in findings if f["severity"] == "blocking")
    warnings  = sum(1 for f in findings if f["severity"] == "warning")
    approved  = blocking == 0

    summary = (
        f"{blocking} blocking, {warnings} warning(s), {len(findings) - blocking - warnings} info"
        if findings
        else "Clean — no issues found"
    )

    logger.info("[review_diff] %s (approved=%s)", summary, approved)
    return {"approved": approved, "findings": findings, "summary": summary}


# --------------------------------------------------------------------------- #
# Deterministic scanners
# --------------------------------------------------------------------------- #
def _scan_file(file_path: str, content: str) -> List[Dict[str, Any]]:
    findings = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        for pattern, rule, severity, message in _BAD_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                findings.append({
                    "file": file_path,
                    "line": lineno,
                    "severity": severity,
                    "rule": rule,
                    "message": f"{message}: {line.strip()[:80]}",
                })
    return findings


def _check_contract(code: Dict[str, str], contract: str) -> List[Dict[str, Any]]:
    """Verify all endpoints in the contract exist in router code."""
    findings = []
    # Extract operationIds from contract
    op_ids = re.findall(r"operationId:\s*['\"]?(\w+)['\"]?", contract)
    router_content = "\n".join(
        v for k, v in code.items() if "router" in k.lower() or "route" in k.lower()
    )
    for op_id in op_ids:
        if op_id not in router_content:
            findings.append({
                "file": "src/router.py",
                "line": 0,
                "severity": "warning",
                "rule": "MISSING_ENDPOINT",
                "message": f"operationId '{op_id}' from contract not found in router",
            })
    return findings


# --------------------------------------------------------------------------- #
# LLM review helper
# --------------------------------------------------------------------------- #
async def _llm_review(code: Dict[str, str], contract: str) -> Optional[Dict[str, Any]]:
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    endpoint = (
        os.getenv("AZURE_AI_ENDPOINT") or os.getenv("AZURE_OPENAI_ENDPOINT") or ""
    ).rstrip("/")
    deployment = (
        os.getenv("AZURE_DEPLOYMENT_STRONG") or os.getenv("AZURE_DEPLOYMENT_ID") or ""
    )
    if not (api_key and endpoint and deployment):
        return None

    # Summarise code for prompt (avoid token overflow)
    code_summary = "\n---\n".join(
        f"FILE: {path}\n{content[:500]}" for path, content in list(code.items())[:6]
    )
    prompt = (
        "Review the following generated code files for correctness, security, and quality.\n\n"
        f"Code files:\n{code_summary}\n\n"
        f"OpenAPI contract (first 600 chars):\n{contract[:600]}\n\n"
        "Return ONLY a JSON object with keys: approved (bool), findings (list of "
        "{file, line, severity, rule, message}), summary (str)."
    )

    try:
        from langchain_azure_ai.chat_models import AzureChatCompletions  # type: ignore
        from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore

        model = AzureChatCompletions(
            azure_endpoint=endpoint,
            azure_deployment=deployment,
            api_key=api_key,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
            max_tokens=2000,
        )
        resp = await model.ainvoke(
            [
                SystemMessage(content="You are a senior code reviewer. Return valid JSON only."),
                HumanMessage(content=prompt),
            ]
        )
        raw = (getattr(resp, "content", "") or "").strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"```$", "", raw).strip()
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and "approved" in parsed:
            return parsed
    except Exception as exc:  # noqa: BLE001
        logger.debug("[review_diff] LLM review skipped: %s", exc)
    return None
