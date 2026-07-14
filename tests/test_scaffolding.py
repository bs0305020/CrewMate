"""Scaffolding verification for task 1.1.

Confirms the package structure, test framework, shared import path (Lambda Layer
substitute), and shared stub modules are wired correctly. This only proves the
foundation is in place - it does not implement any later task's functionality.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_test_dependencies_are_importable():
    import hypothesis
    import pydantic

    assert hypothesis.__version__
    assert pydantic.__version__


@pytest.mark.parametrize(
    "module_name",
    [
        "agent",
        "agent.tools",
        "backend",
        "functions",
        "functions.agent_invoke",
        "functions.gap_event",
        "tests.mocks.shared_stubs",
    ],
)
def test_scaffolding_packages_importable(module_name):
    assert importlib.import_module(module_name) is not None


def test_agent_package_is_single_shared_contract_source():
    # After unifying with 담당자 A's packaging, the Agent package lives at backend/agent and
    # is imported as ``agent.*`` from the ``backend/`` root (put on sys.path by A's
    # tests/conftest.py, and bundled under CodeUri=backend/ in deployment). ``agent.schemas``
    # therefore resolves to the SAME module from both function packages.
    agent = importlib.import_module("agent")
    agent_dir = Path(agent.__file__).resolve().parent
    assert agent_dir == ROOT / "backend" / "agent"
    # Re-importing yields the identical package object (a single shared-contract source).
    assert importlib.import_module("agent") is agent
    # The workspace root stays on sys.path so the ``tests.*`` helper packages still resolve.
    assert str(ROOT) in sys.path


def test_backend_shared_present_after_merge():
    # 담당자 A owns backend/shared/* and it is now merged into the workspace.
    # 담당자 B still consumes these helpers via the tests/mocks stubs at this
    # checkpoint, but the real package is expected to exist post-merge.
    assert (ROOT / "backend" / "shared").is_dir()


# --- shared stub behaviour --------------------------------------------------------


def test_response_helpers_match_shared_contract():
    from tests.mocks import shared_stubs

    assert shared_stubs.ok({"x": 1}) == {"success": True, "data": {"x": 1}}
    assert shared_stubs.ok() == {"success": True, "data": {}}
    err = shared_stubs.error("STATE_CONFLICT", "busy")
    assert err == {"success": False, "error": {"code": "STATE_CONFLICT", "message": "busy"}}


def test_state_constants_present():
    from tests.mocks import shared_stubs

    stubs = shared_stubs.build_shared_stubs()
    assert stubs.state.READY == "READY"
    assert stubs.state.RequestStatus.COMPOSING == "COMPOSING"
    assert stubs.state.GapStatus.RECOMPOSING == "RECOMPOSING"
    assert stubs.state.Source.AGENT == "AGENT"
    assert stubs.state.ErrorCode.AGENT_OUTPUT_INVALID == "AGENT_OUTPUT_INVALID"


def test_fake_db_conditional_transition_semantics():
    from tests.mocks import shared_stubs

    db = shared_stubs.FakeSharedDB()
    db.add_work_request("REQ1", status="REQUESTED")
    # Correct expected state -> transition succeeds and mutates.
    assert db.transition_request_status("REQ1", "REQUESTED", "COMPOSING") is True
    assert db.work_requests["REQ1"]["status"] == "COMPOSING"
    # Stale expected state -> transition fails and does not mutate (models STATE_CONFLICT).
    assert db.transition_request_status("REQ1", "REQUESTED", "PROPOSED") is False
    assert db.work_requests["REQ1"]["status"] == "COMPOSING"
    assert len(db.status_transitions) == 2


def test_fake_db_query_ready_workers_scope():
    from tests.mocks import shared_stubs

    db = shared_stubs.FakeSharedDB()
    db.add_worker("W1", office_id="OFFICE001", state="READY", trade="FORMWORK")
    db.add_worker("W2", office_id="OFFICE001", state="RUNNING", trade="FORMWORK")
    db.add_worker("W3", office_id="OFFICE002", state="READY", trade="FORMWORK")
    db.add_worker("W4", office_id="OFFICE001", state="READY", trade="REBAR")

    ready = db.query_ready_workers("OFFICE001")
    assert {w["worker_id"] for w in ready} == {"W1", "W4"}  # office + READY only
    formwork = db.query_ready_workers("OFFICE001", trades=["FORMWORK"])
    assert {w["worker_id"] for w in formwork} == {"W1"}


def test_stub_auth_allow_and_deny():
    from tests.mocks import shared_stubs

    office = shared_stubs.StubAuth(role="OFFICE", office_id="OFFICE001")
    identity = office.require_role({}, ["OFFICE"])
    assert identity["role"] == "OFFICE"
    assert identity["office_id"] == "OFFICE001"

    company = shared_stubs.StubAuth(role="COMPANY")
    with pytest.raises(shared_stubs.ForbiddenError):
        company.require_role({}, ["OFFICE"])


def test_install_shared_stubs_registers_backend_shared(monkeypatch):
    from tests.mocks import shared_stubs

    stubs = shared_stubs.install_shared_stubs(monkeypatch)

    # Both `from shared import db` and attribute access resolve the stub.
    from shared import db as db_mod  # type: ignore
    from shared import response as response_mod  # type: ignore

    db_mod.instance.add_work_request("REQ9", status="REQUESTED")
    assert db_mod.transition_request_status("REQ9", "REQUESTED", "COMPOSING") is True
    assert response_mod.ok({"n": 1})["success"] is True
    # The module functions are bound to the same instance returned by the installer.
    assert stubs.db.work_requests["REQ9"]["status"] == "COMPOSING"


def test_shared_fixture_available(shared):
    # The `shared` fixture (from root conftest) provides a fresh stub set.
    assert shared.state.READY == "READY"
    assert shared.response.ok()["success"] is True
    assert shared.auth.role == "OFFICE"
