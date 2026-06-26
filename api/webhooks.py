"""
C3: webhook trigger helpers — turn an external system event (Azure DevOps work
item) into a Continuum run intent.

Pure + offline-safe: parsing functions have no IO and no external deps. The
FastAPI endpoint that uses them lives in api/main.py so it shares the run store,
event bus and auth seams.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# ADO tag convention for business mappings:
#   "mapping:BR=Branch,WEB=Web"   →  [{code: BR, label: Branch}, {code: WEB, label: Web}]
# Tags arrive as a "; "-separated string in System.Tags; we scan every tag for
# the mapping: prefix so order / surrounding tags don't matter.
_MAPPING_TAG_RE = re.compile(r"mapping:\s*(.+)", re.IGNORECASE)


def parse_mapping_tags(tags: Optional[str]) -> List[Dict[str, str]]:
    """
    Parse business mappings out of an ADO System.Tags string.

    Accepts the whole tag string (e.g. "ready; mapping:BR=Branch,WEB=Web; ui").
    Returns [{code, label}, ...]; unknown / malformed pairs are skipped.
    """
    if not tags:
        return []
    mappings: List[Dict[str, str]] = []
    seen: set = set()
    for raw_tag in re.split(r"[;\n]", tags):
        m = _MAPPING_TAG_RE.search(raw_tag.strip())
        if not m:
            continue
        for pair in m.group(1).split(","):
            if "=" not in pair:
                continue
            code, _, label = pair.partition("=")
            code, label = code.strip(), label.strip()
            if code and label and code not in seen:
                seen.add(code)
                mappings.append({"code": code, "label": label})
    return mappings


def extract_intent(payload: Dict[str, Any]) -> str:
    """
    Build a plain-English intent from an ADO work-item webhook payload.
    Combines System.Title + System.Description (HTML stripped).
    """
    fields = (payload.get("resource", {}) or {}).get("fields", {}) or {}
    title = str(fields.get("System.Title", "")).strip()
    desc = str(fields.get("System.Description", "")).strip()
    desc = re.sub(r"<[^>]+>", " ", desc)          # strip HTML tags
    desc = re.sub(r"\s+", " ", desc).strip()
    return f"{title}. {desc}".strip(". ").strip() if (title or desc) else ""


def should_trigger(payload: Dict[str, Any]) -> bool:
    """
    Decide whether an ADO webhook should start a run.

    Triggers on:
      - workitem.created for a User Story / Task, OR
      - workitem.updated where System.State changed to "Ready for Dev".
    """
    event_type = str(payload.get("eventType", ""))
    fields = (payload.get("resource", {}) or {}).get("fields", {}) or {}
    if event_type == "workitem.created":
        wi_type = str(fields.get("System.WorkItemType", ""))
        return wi_type in ("User Story", "Task", "Bug")
    if event_type == "workitem.updated":
        # ADO sends changed fields under resource.fields as {"old":..,"new":..}
        # but also flattens System.State on the resource; accept either shape.
        state_field = fields.get("System.State")
        if isinstance(state_field, dict):
            return str(state_field.get("newValue", "")) == "Ready for Dev"
        return str(state_field or "") == "Ready for Dev"
    return False
