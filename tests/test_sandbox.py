import pytest
from sandbox.venv_manager import VenvManager
from pathlib import Path

@pytest.fixture
def sandbox():
    return VenvManager(Path("/sandbox_test"))

def test_venv_creation(sandbox):
    venv_path = sandbox.create_venv("test_agent")
    assert venv_path.exists()
    assert (venv_path / "bin" / "python").exists()

def test_code_execution(sandbox):
    result = sandbox.execute_in_venv(
        venv_path=Path("/sandbox_test/test_agent_venv"),
        code="print(2+2)",
        timeout=10
    )
    assert result['stdout'].strip() == "4"
