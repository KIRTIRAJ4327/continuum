"""
component.py — deterministic component key derivation (M11 Spec Registry).

A "component" is the stable identity a spec is filed under in the Spec Registry.
Two runs describing the same feature must derive the *same* slug so their specs
link into one version chain. The slug is therefore a pure, deterministic function
of the request text (and the story title when present) — no randomness, no clock.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

# Filler words dropped so "Build a Branch Management API" and "branch management"
# collapse to the same key. Mirrors the spirit of write_spec._extract_entities.
_STOP = {
    "build", "create", "add", "implement", "make", "support", "new",
    "a", "an", "the", "for", "to", "of", "in", "on", "with", "and",
    "api", "system", "feature", "page", "endpoint", "service", "app",
}

_MAX_TOKENS = 4


def component_slug(request: str, story: Optional[Dict[str, Any]] = None) -> str:
    """
    Derive a stable component slug from a request (preferring the story title).

    'Build a Branch Management API' -> 'branch-management'
    'Add a products listing page'   -> 'products-listing'

    Always returns a non-empty slug; falls back to 'component' when the text has
    no usable tokens.
    """
    src = ""
    if story and isinstance(story, dict):
        src = (story.get("title") or "").strip()
    src = src or (request or "")

    tokens = re.findall(r"[a-z0-9]+", src.lower())
    kept = [t for t in tokens if t not in _STOP]
    # If stopword removal emptied the list, keep the raw tokens so we still get a key.
    chosen = (kept or tokens)[:_MAX_TOKENS]
    return "-".join(chosen) or "component"
