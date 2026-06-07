import pytest
from .skill import create_story

@pytest.mark.asyncio
async def test_create_story():
    result = await create_story(request="Build a dashboard", jira_token="token")
    assert "story_id" in result
    assert "story_url" in result
