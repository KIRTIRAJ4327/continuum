"""Emit database schema skill."""
from typing import Dict, Any

async def emit_schema(story: Dict[str, Any], spec: Dict[str, Any]) -> str:
    """
    Generate a SQL DDL schema from the story and spec.

    Returns:
        SQL DDL string
    """
    # TODO: Call LLM to generate schema
    return """
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE activities (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users(id),
    action VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""
