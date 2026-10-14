import pytest

from project_reader import ProjectReader
from terminal_runner import CommandNotAllowedError, SafeTerminalRunner


def test_terminal_runner_rejects_dangerous_command():
    with pytest.raises(CommandNotAllowedError):
        SafeTerminalRunner.validate_command("git reset --hard")


def test_terminal_runner_accepts_git_status():
    assert SafeTerminalRunner.validate_command("git status") == (
        "git",
        "status"
    )


def test_project_reader_rejects_path_traversal(tmp_path):
    project_folder = tmp_path / "project"
    project_folder.mkdir()
    outside_file = tmp_path / "outside.py"
    outside_file.write_text("print('outside')", encoding="utf-8")
    reader = ProjectReader(project_folder)

    with pytest.raises(ValueError, match="outside the project folder"):
        reader.read_file("../outside.py")
