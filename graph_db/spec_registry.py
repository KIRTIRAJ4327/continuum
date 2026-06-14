"""
spec_registry.py — M11 persistent, versioned Spec Registry.

A `Spec` is one immutable version of the agreed specification for a *component*.
Versioning is append-only (like the audit trail): a new spec that materially
differs from the current one SUPERSEDES it — there are no in-place edits.

Three-tier fallback, mirroring graph_db/driver.py's episodic-memory design:

    live Neo4j session  →  process-level in-memory store  →  pure-Python lookups

The in-memory store is a *module-level* list so a spec written by run N is visible
to run N+1 (and to the read-only API endpoints) within one process — this is what
gives the Registry cross-run persistence on the offline path, with no Neo4j and no
driver singleton plumbing. The live path is used only when a *connected* Neo4jDriver
is passed (``driver.driver is not None``); any error there falls back to the store.

Spec record shape (the dict returned by the read functions):
    {id, component, version, body, request_text, run_id,
     status ("current"|"superseded"), superseded_reason, supersedes_id, timestamp}
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Process-global offline store. Reset via _reset_registry() in tests/verifiers.
_IN_MEMORY_SPECS: List[Dict[str, Any]] = []

# Stable structural keys used to decide whether two specs "match". A change to any
# of these is a material change that requires an explicit supersession.
_STABLE_KEYS = ("api_endpoints", "data_entities", "out_of_scope")

# ── Cypher (live path) ────────────────────────────────────────────────────────
_WRITE_SPEC = """
MERGE (s:Spec {id: $spec_id})
ON CREATE SET s.component=$component, s.version=$version, s.body=$body,
              s.request_text=$request_text, s.run_id=$run_id,
              s.status='current', s.timestamp=$timestamp
WITH s
MERGE (c:Component {id: $component})
MERGE (c)-[:HAS_SPEC]->(s)
RETURN s.id AS id
"""
_CURRENT_VERSION = """
MATCH (s:Spec {component: $component})
RETURN coalesce(max(s.version), 0) AS v
"""
_GET_CURRENT_SPEC = """
MATCH (s:Spec {component: $component, status: 'current'})
RETURN s.id AS id, s.component AS component, s.version AS version,
       s.body AS body, s.request_text AS request_text, s.run_id AS run_id,
       s.status AS status, s.timestamp AS timestamp
ORDER BY s.version DESC LIMIT 1
"""
_GET_SPEC_HISTORY = """
MATCH (s:Spec {component: $component})
RETURN s.id AS id, s.component AS component, s.version AS version,
       s.body AS body, s.run_id AS run_id, s.status AS status,
       s.superseded_reason AS superseded_reason, s.timestamp AS timestamp
ORDER BY s.version ASC
"""
_MARK_SUPERSEDED = """
MATCH (old:Spec {id: $old_id}), (new:Spec {id: $new_id})
SET old.status='superseded', old.superseded_reason=$reason, new.supersedes_id=$old_id
MERGE (new)-[r:SUPERSEDES]->(old) SET r.reason=$reason
"""


# ── Public helpers ──────────────────────────────────────────────────────────────
def specs_match(a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]) -> bool:
    """
    Structural equality on the stable spec keys (order-insensitive).

    Single source of truth for "does this spec conform to that one?" — used both
    by the write skill (to decide if a supersession is needed) and by the
    scope-guard gate (to detect undeclared drift). Pure, offline.
    """
    a = a or {}
    b = b or {}
    for k in _STABLE_KEYS:
        if set(map(str, a.get(k, []) or [])) != set(map(str, b.get(k, []) or [])):
            return False
    return True


def _is_live(neo4j_driver: Any) -> bool:
    """True only when a *connected* Neo4jDriver was supplied."""
    return neo4j_driver is not None and getattr(neo4j_driver, "driver", None) is not None


def _reset_registry() -> None:
    """Clear the in-memory store (test / verifier hook)."""
    _IN_MEMORY_SPECS.clear()


# ── Write ────────────────────────────────────────────────────────────────────
async def write_spec(
    component: str,
    body: Dict[str, Any],
    request_text: str = "",
    run_id: str = "",
    spec_id: str = "",
    neo4j_driver: Any = None,
) -> Dict[str, Any]:
    """
    Persist a new Spec version for `component`. Version auto-increments from the
    component's existing history. Returns {spec_id, component, version, stub}.
    """
    sid = spec_id or f"spec-{uuid.uuid4().hex[:12]}"
    ts = time.time()

    if _is_live(neo4j_driver):
        try:
            async with neo4j_driver.driver.session() as session:
                res = await session.run(_CURRENT_VERSION, component=component)
                row = await res.single()
                version = int((row["v"] if row else 0)) + 1
                await session.run(
                    _WRITE_SPEC, spec_id=sid, component=component, version=version,
                    body=_json(body), request_text=request_text, run_id=run_id, timestamp=ts,
                )
            logger.info("[spec_registry] Spec %s v%d written to Neo4j (%s)", sid, version, component)
            return {"spec_id": sid, "component": component, "version": version, "stub": False}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[spec_registry] write_spec live failed, using in-memory: %s", exc)

    version = _offline_next_version(component)
    _IN_MEMORY_SPECS.append({
        "id": sid, "component": component, "version": version, "body": body,
        "request_text": request_text, "run_id": run_id, "status": "current",
        "superseded_reason": "", "supersedes_id": "", "timestamp": ts,
    })
    logger.debug("[spec_registry] Spec %s v%d stored in-memory (%d total)", sid, version, len(_IN_MEMORY_SPECS))
    return {"spec_id": sid, "component": component, "version": version, "stub": True}


async def mark_superseded(
    old_spec_id: str, new_spec_id: str, reason: str, neo4j_driver: Any = None
) -> bool:
    """Flag the old spec superseded and link new -[:SUPERSEDES]-> old."""
    if _is_live(neo4j_driver):
        try:
            async with neo4j_driver.driver.session() as session:
                await session.run(
                    _MARK_SUPERSEDED, old_id=old_spec_id, new_id=new_spec_id, reason=reason
                )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[spec_registry] mark_superseded live failed, using in-memory: %s", exc)

    changed = False
    for s in _IN_MEMORY_SPECS:
        if s["id"] == old_spec_id:
            s["status"] = "superseded"
            s["superseded_reason"] = reason
            changed = True
        if s["id"] == new_spec_id:
            s["supersedes_id"] = old_spec_id
    return changed


# ── Read ─────────────────────────────────────────────────────────────────────
async def get_current_spec(component: str, neo4j_driver: Any = None) -> Optional[Dict[str, Any]]:
    """The latest non-superseded spec for `component`, or None."""
    if _is_live(neo4j_driver):
        try:
            async with neo4j_driver.driver.session() as session:
                res = await session.run(_GET_CURRENT_SPEC, component=component)
                row = await res.single()
            return _from_row(dict(row)) if row else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("[spec_registry] get_current_spec live failed, using in-memory: %s", exc)
    return _offline_current(component)


async def get_spec_history(component: str, neo4j_driver: Any = None) -> List[Dict[str, Any]]:
    """All specs for `component`, oldest version first (the version chain)."""
    if _is_live(neo4j_driver):
        try:
            async with neo4j_driver.driver.session() as session:
                res = await session.run(_GET_SPEC_HISTORY, component=component)
                rows = await res.data()
            return [_from_row(r) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[spec_registry] get_spec_history live failed, using in-memory: %s", exc)
    return _offline_history(component)


def list_components(neo4j_driver: Any = None) -> List[Dict[str, Any]]:
    """
    Distinct components with their current version (offline store only — the
    read-only API list view). Returns [{component, current_version, versions}].
    """
    out: Dict[str, Dict[str, Any]] = {}
    for s in _IN_MEMORY_SPECS:
        c = s["component"]
        entry = out.setdefault(c, {"component": c, "current_version": 0, "versions": 0})
        entry["versions"] += 1
        if s["status"] == "current" and s["version"] > entry["current_version"]:
            entry["current_version"] = s["version"]
    return sorted(out.values(), key=lambda e: e["component"])


# ── Offline helpers ────────────────────────────────────────────────────────────
def _offline_next_version(component: str) -> int:
    versions = [s["version"] for s in _IN_MEMORY_SPECS if s["component"] == component]
    return (max(versions) + 1) if versions else 1


def _offline_current(component: str) -> Optional[Dict[str, Any]]:
    rows = [s for s in _IN_MEMORY_SPECS if s["component"] == component and s["status"] == "current"]
    return dict(max(rows, key=lambda s: s["version"])) if rows else None


def _offline_history(component: str) -> List[Dict[str, Any]]:
    rows = [dict(s) for s in _IN_MEMORY_SPECS if s["component"] == component]
    return sorted(rows, key=lambda s: s["version"])


# ── Serialization (Neo4j stores scalars; the body dict is JSON-encoded) ─────────
def _json(body: Dict[str, Any]) -> str:
    import json
    return json.dumps(body, default=str)


def _from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Decode a Neo4j row back into the standard spec record shape."""
    import json
    rec = dict(row)
    body = rec.get("body")
    if isinstance(body, str):
        try:
            rec["body"] = json.loads(body)
        except Exception:  # noqa: BLE001
            rec["body"] = {}
    rec.setdefault("superseded_reason", "")
    rec.setdefault("supersedes_id", "")
    return rec
