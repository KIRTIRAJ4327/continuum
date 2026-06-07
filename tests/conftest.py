"""Pytest fixtures and configuration."""
import pytest
import asyncio

@pytest.fixture
def event_loop():
    """Provide an event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
