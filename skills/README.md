# Skills — Versioned Atoms

A **skill** is a versioned, named function that an agent can call.

## Skill Structure

Each skill has a unique name, a version, a Python module, and tests.

## Skill Anatomy

```python
# skills/create_story/v1.0/skill.py

async def create_story(request: str, jira_token: str) -> Dict[str, Any]:
    """Create a Jira story from a plain-English request."""
    pass
```

## Adding a New Skill

1. Create `skills/{skill_name}/v1.0/`
2. Add `skill.py` with an `async def` function
3. Add `test.py` with pytest tests
4. Update the agent YAML `allowed_skills`
