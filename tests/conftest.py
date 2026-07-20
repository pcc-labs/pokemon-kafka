"""Shared fixtures for Pokemon agent tests."""

from unittest.mock import MagicMock

import pytest


class FakeMemory:
    """Dict-backed memory that mimics pyboy.memory[addr] access."""

    def __init__(self):
        self._data: dict[int, int] = {}

    def __getitem__(self, addr: int) -> int:
        return self._data.get(addr, 0)

    def __setitem__(self, addr: int, value: int):
        self._data[addr] = value & 0xFF


@pytest.fixture
def fake_memory():
    return FakeMemory()


@pytest.fixture
def mock_pyboy(fake_memory):
    """PyBoy mock with dict-backed memory."""
    pyboy = MagicMock()
    pyboy.memory = fake_memory
    return pyboy


@pytest.fixture(autouse=True)
def _no_real_self_heal(monkeypatch):
    """Keep agent.main()'s automatic self-heal from spawning real healer subprocesses.

    Without this, CLI tests race variants against the repo's actual
    data/healer_state.json and notes.md. test_self_heal.py is unaffected:
    it imports run_self_heal directly and injects its own runner.
    """
    import agent

    monkeypatch.setattr(agent, "run_self_heal", lambda *a, **kw: False)
