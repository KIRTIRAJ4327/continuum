"""
gen_tests skill — generates missing tests for any uncovered API endpoints.

Inspects the OpenAPI contract and the existing test files to find endpoints
that lack coverage, then generates pytest test functions for each.

Live path:  Azure AI generates contextually appropriate tests.
Offline path: Template-based test generation from the contract.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def gen_tests(
    contract: str,
    code: Dict[str, str],
) -> Dict[str, str]:
    """
    Generate test files for any endpoints not already covered.

    Args:
        contract: OpenAPI 3.0 YAML string.
        code:     Existing code files {"file_path": content}.

    Returns:
        {"tests/<file>.py": "<content>", …}  — new / augmented test files.
    """
    if not contract:
        logger.warning("[gen_tests] No contract provided — cannot generate tests")
        return {}

    operations = _extract_operations(contract)
    if not operations:
        return {}

    existing_tests = "\n".join(
        v for k, v in code.items()
        if k.startswith("tests/") or k.startswith("test_")
    )

    # Find uncovered operations
    uncovered = [op for op in operations if op["operationId"] not in existing_tests]

    if not uncovered:
        logger.info("[gen_tests] All %d operations already covered", len(operations))
        return {}

    logger.info("[gen_tests] Generating tests for %d uncovered operations", len(uncovered))

    # Try LLM
    llm_tests = await _llm_gen(contract, uncovered, existing_tests)
    if llm_tests:
        return llm_tests

    # Deterministic template generation
    return _template_tests(uncovered, contract)


# --------------------------------------------------------------------------- #
# OpenAPI operation extractor
# --------------------------------------------------------------------------- #
def _extract_operations(contract: str) -> List[Dict[str, Any]]:
    """Parse method + path + operationId from the YAML contract."""
    ops = []
    current_path = ""
    current_method = ""

    for line in contract.splitlines():
        path_m = re.match(r"^\s{2}/([^\s:]+):", line)
        if path_m:
            current_path = "/" + path_m.group(1)
            continue

        method_m = re.match(r"^\s{4}(get|post|put|patch|delete|head):", line, re.IGNORECASE)
        if method_m:
            current_method = method_m.group(1).upper()
            continue

        op_id_m = re.match(r"^\s+operationId:\s*['\"]?(\w+)['\"]?", line)
        if op_id_m and current_path and current_method:
            ops.append({
                "operationId": op_id_m.group(1),
                "method": current_method,
                "path": current_path,
            })

    return ops


# --------------------------------------------------------------------------- #
# Template-based test generator
# --------------------------------------------------------------------------- #
def _template_tests(operations: List[Dict[str, Any]], contract: str) -> Dict[str, str]:
    """Generate a pytest file from operation templates."""
    resource = _resource_from_contract(contract)
    res_pl = resource + "s"

    lines = [
        '"""Auto-generated tests by Continuum gen_tests skill."""',
        "import pytest",
        "from httpx import AsyncClient, ASGITransport",
        "",
        "pytestmark = pytest.mark.asyncio",
        "",
        "",
        "async def _client():",
        "    from src.main import app",
        '    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:',
        "        yield c",
        "",
    ]

    for op in operations:
        method  = op["method"]
        path    = op["path"]
        op_id   = op["operationId"]
        fn_name = "test_" + op_id
        # Build a concrete test path: replace {id} placeholder with the variable
        path_with_id = path.replace("{id}", "' + str(created['id']) + '")

        if method == "GET" and "{" not in path:
            lines += [
                "async def " + fn_name + "():",
                "    async for c in _client():",
                "        resp = await c.get('" + path + "')",
                "        assert resp.status_code == 200",
                "",
            ]
        elif method == "POST":
            lines += [
                "async def " + fn_name + "():",
                "    async for c in _client():",
                "        resp = await c.post('" + path + "', json={'name': 'Test'})",
                "        assert resp.status_code in (200, 201)",
                "",
            ]
        elif method == "GET" and "{" in path:
            lines += [
                "async def " + fn_name + "():",
                "    async for c in _client():",
                "        created = (await c.post('/" + res_pl + "', json={'name': 'T'})).json()",
                "        resp = await c.get('" + path_with_id + "')",
                "        assert resp.status_code == 200",
                "",
            ]
        elif method in ("PUT", "PATCH"):
            lines += [
                "async def " + fn_name + "():",
                "    async for c in _client():",
                "        created = (await c.post('/" + res_pl + "', json={'name': 'T'})).json()",
                "        resp = await c." + method.lower() + "('" + path_with_id + "', json={'name': 'Updated'})",
                "        assert resp.status_code == 200",
                "",
            ]
        elif method == "DELETE":
            lines += [
                "async def " + fn_name + "():",
                "    async for c in _client():",
                "        created = (await c.post('/" + res_pl + "', json={'name': 'T'})).json()",
                "        resp = await c.delete('" + path_with_id + "')",
                "        assert resp.status_code in (200, 204)",
                "",
            ]

    return {"tests/test_generated.py": "\n".join(lines)}


def _resource_from_contract(contract: str) -> str:
    m = re.search(r"^\s{2}/([a-z][a-z0-9_-]+)s?:", contract, re.MULTILINE)
    if m:
        return m.group(1).rstrip("s")
    return "item"


# --------------------------------------------------------------------------- #
# LLM generator
# --------------------------------------------------------------------------- #
async def _llm_gen(
    contract: str,
    uncovered: List[Dict[str, Any]],
    existing_tests: str,
) -> Optional[Dict[str, str]]:
    api_key  = os.getenv("AZURE_OPENAI_API_KEY", "")
    endpoint = (os.getenv("AZURE_AI_ENDPOINT") or os.getenv("AZURE_OPENAI_ENDPOINT") or "").rstrip("/")
    deploy   = os.getenv("AZURE_DEPLOYMENT_STRONG") or os.getenv("AZURE_DEPLOYMENT_ID") or ""
    if not (api_key and endpoint and deploy):
        return None

    prompt = (
        "Generate pytest tests for the following uncovered API operations:\n\n"
        f"Uncovered: {json.dumps(uncovered, indent=2)}\n\n"
        f"Contract (excerpt):\n{contract[:800]}\n\n"
        f"Existing tests (excerpt):\n{existing_tests[:400]}\n\n"
        "Use httpx.AsyncClient + ASGITransport. Import app from src.main. "
        "Return ONLY a JSON object where keys are file paths and values are file contents."
    )
    try:
        from langchain_azure_ai.chat_models import AzureChatCompletions  # type: ignore
        from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore

        model = AzureChatCompletions(
            azure_endpoint=endpoint, azure_deployment=deploy, api_key=api_key,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
            max_tokens=3000,
        )
        resp = await model.ainvoke(
            [
                SystemMessage(content="You are a test engineer. Return valid JSON only."),
                HumanMessage(content=prompt),
            ]
        )
        raw = re.sub(r"^```[a-z]*\n?|```$", "", (getattr(resp, "content", "") or "").strip()).strip()
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception as exc:  # noqa: BLE001
        logger.debug("[gen_tests] LLM skipped: %s", exc)
    return None
