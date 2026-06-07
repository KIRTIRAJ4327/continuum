"""Emit OpenAPI contract skill."""
from typing import Dict, Any

async def emit_contract(story: Dict[str, Any], spec: Dict[str, Any]) -> str:
    """
    Generate an OpenAPI 3.0 contract from the story and spec.

    Returns:
        OpenAPI YAML string
    """
    # TODO: Call LLM to generate contract
    return """
openapi: 3.0.0
info:
  title: My API
  version: 1.0.0
paths:
  /dashboard:
    get:
      summary: Get dashboard
      responses:
        '200':
          description: Success
"""
