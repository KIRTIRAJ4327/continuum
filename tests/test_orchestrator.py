"""Tests for the orchestrator."""
import pytest
from orchestrator.state import ContinuumState

@pytest.mark.asyncio
async def test_orchestrator_routes_to_bsa():
    """Test that orchestrator routes to BSA for a new request."""
    state = ContinuumState(request="Build a user dashboard")
    assert state.request
    # TODO: assert orchestrator routes to BSA

@pytest.mark.asyncio
async def test_gate_passes_on_clean_code():
    """Test that a gate passes when code is clean."""
    # TODO: Test gate_local_verify with clean code
    pass

@pytest.mark.asyncio
async def test_gate_fails_on_bad_code():
    """Test that a gate fails when code has issues."""
    # TODO: Test gate_local_verify with bad code
    pass
