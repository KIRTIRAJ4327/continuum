"""
emit_contract skill — generates an OpenAPI 3.0 contract from the story + spec.

Live path:  Azure AI call for a proper, domain-accurate contract.
Offline path: Derives the contract from the story title and spec entities.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def emit_contract(story: Dict[str, Any], spec: Dict[str, Any]) -> str:
    """
    Generate an OpenAPI 3.0 YAML contract for the feature.

    Args:
        story: BSA story dict (title, description, …).
        spec:  Detailed spec (functional, api_endpoints, data_entities, …).

    Returns:
        OpenAPI 3.0 YAML string (ready for gate_contract_validate).
    """
    title = story.get("title", "Feature API")
    description = story.get("description", "")
    entities: List[str] = spec.get("data_entities") or []
    resource = _resource_name(title, entities)

    # Try live LLM
    llm_yaml = await _azure_llm(
        prompt=(
            f"Generate a complete OpenAPI 3.0 YAML contract for:\n\n"
            f"Feature title: {title}\n"
            f"Description: {description[:400]}\n"
            f"Primary resource: {resource}\n"
            f"Data entities: {', '.join(entities[:5])}\n\n"
            "Requirements:\n"
            "- openapi: '3.0.0'\n"
            "- Include GET /resource, POST /resource, GET/PUT/DELETE /resource/{id}\n"
            "- Include components/schemas with proper types\n"
            "- Return ONLY the YAML — no prose, no markdown fences."
        ),
        system=(
            "You are an API design expert. Generate valid, complete OpenAPI 3.0 YAML specs. "
            "Return ONLY the YAML content — no explanation, no markdown code blocks."
        ),
    )
    if llm_yaml and llm_yaml.strip().startswith("openapi"):
        logger.info("[emit_contract] LLM contract generated for: %s", title)
        return llm_yaml.strip()

    # Deterministic fallback
    return _build_contract(title, resource, entities)


# --------------------------------------------------------------------------- #
# Deterministic contract builder
# --------------------------------------------------------------------------- #
def _resource_name(title: str, entities: List[str]) -> str:
    """Derive the primary REST resource name from title or entities."""
    if entities:
        return re.sub(r"[^a-z0-9]", "-", entities[0].lower()).strip("-") or "resource"
    stop = {
        "build", "create", "add", "implement", "make", "with", "and", "the",
        "a", "an", "for", "to", "of", "in", "on", "at", "by", "feature",
    }
    words = re.sub(r"[^a-z0-9\s]", "", title.lower()).split()
    meaningful = [w for w in words if w not in stop and len(w) > 2]
    return (meaningful[-1] if meaningful else "resource").replace(" ", "-")


def _build_contract(title: str, resource: str, entities: List[str]) -> str:
    """Build a full CRUD OpenAPI 3.0 YAML contract."""
    res = resource
    res_cap = "".join(w.capitalize() for w in re.split(r"[-_]", res))
    res_pl = res + "s"

    # Build additional entity schemas
    extra_schemas = ""
    for ent in entities[1:4]:
        ent_cap = "".join(w.capitalize() for w in re.split(r"[-_\s]", ent.lower()))
        extra_schemas += f"""
    {ent_cap}:
      type: object
      properties:
        id:
          type: string
          format: uuid
        name:
          type: string
"""

    return f"""openapi: "3.0.0"
info:
  title: "{title} API"
  version: "1.0.0"
  description: "Auto-generated OpenAPI contract for {title}"
  contact:
    name: Continuum Auto-Generator

servers:
  - url: https://api.example.com/v1
    description: Production
  - url: http://localhost:8000
    description: Local development

tags:
  - name: {res}
    description: "{res_cap} operations"

paths:
  /{res_pl}:
    get:
      tags: [{res}]
      summary: "List {res_pl}"
      operationId: "list_{res.replace('-', '_')}"
      parameters:
        - name: page
          in: query
          schema:
            type: integer
            default: 1
        - name: page_size
          in: query
          schema:
            type: integer
            default: 20
            maximum: 100
      responses:
        "200":
          description: "Paginated list of {res_pl}"
          content:
            application/json:
              schema:
                type: object
                properties:
                  items:
                    type: array
                    items:
                      $ref: "#/components/schemas/{res_cap}"
                  total:
                    type: integer
                  page:
                    type: integer
                  page_size:
                    type: integer
        "401":
          $ref: "#/components/responses/Unauthorized"
    post:
      tags: [{res}]
      summary: "Create a {res}"
      operationId: "create_{res.replace('-', '_')}"
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/{res_cap}Input"
      responses:
        "201":
          description: "Created"
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/{res_cap}"
        "400":
          $ref: "#/components/responses/BadRequest"
        "401":
          $ref: "#/components/responses/Unauthorized"

  /{res_pl}/{{id}}:
    parameters:
      - name: id
        in: path
        required: true
        schema:
          type: string
          format: uuid
    get:
      tags: [{res}]
      summary: "Get {res} by ID"
      operationId: "get_{res.replace('-', '_')}"
      responses:
        "200":
          description: "{res_cap} found"
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/{res_cap}"
        "404":
          $ref: "#/components/responses/NotFound"
    put:
      tags: [{res}]
      summary: "Update {res}"
      operationId: "update_{res.replace('-', '_')}"
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/{res_cap}Input"
      responses:
        "200":
          description: "Updated"
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/{res_cap}"
        "404":
          $ref: "#/components/responses/NotFound"
    delete:
      tags: [{res}]
      summary: "Delete {res}"
      operationId: "delete_{res.replace('-', '_')}"
      responses:
        "204":
          description: "Deleted"
        "404":
          $ref: "#/components/responses/NotFound"

components:
  schemas:
    {res_cap}:
      type: object
      required:
        - id
        - name
      properties:
        id:
          type: string
          format: uuid
          readOnly: true
        name:
          type: string
          maxLength: 255
        description:
          type: string
          nullable: true
        created_at:
          type: string
          format: date-time
          readOnly: true
        updated_at:
          type: string
          format: date-time
          readOnly: true
    {res_cap}Input:
      type: object
      required:
        - name
      properties:
        name:
          type: string
          minLength: 1
          maxLength: 255
        description:
          type: string
          nullable: true
{extra_schemas}
  responses:
    BadRequest:
      description: Invalid request payload
      content:
        application/json:
          schema:
            type: object
            properties:
              detail:
                type: string
    Unauthorized:
      description: Missing or invalid authentication
    NotFound:
      description: Resource not found
      content:
        application/json:
          schema:
            type: object
            properties:
              detail:
                type: string

  securitySchemes:
    BearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT

security:
  - BearerAuth: []
"""


# --------------------------------------------------------------------------- #
# Azure AI helper
# --------------------------------------------------------------------------- #
async def _azure_llm(prompt: str, system: str = "") -> Optional[str]:
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
            max_tokens=3000,
        )
        msgs = (
            ([SystemMessage(content=system)] if system else [])
            + [HumanMessage(content=prompt)]
        )
        resp = await model.ainvoke(msgs)
        return getattr(resp, "content", None)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[emit_contract] LLM call skipped: %s", exc)
        return None
