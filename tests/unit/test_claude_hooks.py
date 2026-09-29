"""The Claude Code hooks in .claude/hooks/ do what CLAUDE.md says, offline.

Each hook runs as a subprocess with the JSON Claude Code sends on stdin, in a throwaway git repository;
`gh` is replaced by a small fake script on PATH.
"""

import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / ".claude" / "hooks"
GIT = shutil.which("git") or "git"


def git(repo: Path, *args: str) -> None:
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True)


def run_hook(
    name: str, project: Path, tool_input: dict[str, str], **env: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(HOOKS / name)],
        input=json.dumps({"tool_input": tool_input}),
        capture_output=True,
        text=True,
        env={
            **{k: v for k, v in os.environ.items() if k != "HONE_ALLOW_MAIN"},
            "CLAUDE_PROJECT_DIR": str(project),
            **env,
        },
        check=False,
    )


def make_repo(path: Path, branch: str = "main") -> Path:
    """A git repository on ``branch`` with one commit and an ignored file."""
    path.mkdir()
    git(path, "init", "-q", "-b", branch)
    (path / ".gitignore").write_text("local.json\n")
    git(path, "add", ".")
    git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "repo")


# guard-main.sh


def test_guard_blocks_edits_on_main(repo: Path) -> None:
    result = run_hook("guard-main.sh", repo, {"file_path": str(repo / "src" / "new.py")})
    assert result.returncode == 2
    assert "start-task" in result.stderr


def test_guard_allows_edits_on_a_branch(repo: Path) -> None:
    git(repo, "switch", "-q", "-c", "feat/x")
    assert run_hook("guard-main.sh", repo, {"file_path": str(repo / "a.py")}).returncode == 0


def test_guard_allows_the_override(repo: Path) -> None:
    result = run_hook("guard-main.sh", repo, {"file_path": str(repo / "a.py")}, HONE_ALLOW_MAIN="1")
    assert result.returncode == 0


def test_guard_allows_files_outside_the_repository(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "memory" / "note.md"
    assert run_hook("guard-main.sh", repo, {"file_path": str(outside)}).returncode == 0


def test_guard_allows_files_of_another_repository(repo: Path, tmp_path: Path) -> None:
    other = make_repo(tmp_path / "other")  # also on main
    assert run_hook("guard-main.sh", repo, {"file_path": str(other / "a.py")}).returncode == 0


def test_guard_blocks_edits_on_master(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo", branch="master")
    assert run_hook("guard-main.sh", repo, {"file_path": str(repo / "a.py")}).returncode == 2


def test_guard_allows_a_detached_head(repo: Path) -> None:
    git(repo, "switch", "-q", "--detach")
    assert run_hook("guard-main.sh", repo, {"file_path": str(repo / "a.py")}).returncode == 0


def test_guard_allows_ignored_files(repo: Path) -> None:
    assert run_hook("guard-main.sh", repo, {"file_path": str(repo / "local.json")}).returncode == 0


def test_guard_reads_the_branch_of_a_worktree(repo: Path, tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    git(repo, "worktree", "add", "-q", "-b", "feat/y", str(worktree))
    assert run_hook("guard-main.sh", repo, {"file_path": str(worktree / "a.py")}).returncode == 0


def test_guard_checks_notebooks(repo: Path) -> None:
    assert run_hook("guard-main.sh", repo, {"notebook_path": str(repo / "n.ipynb")}).returncode == 2


# format-python.sh (runs ruff from this repository's environment)


@pytest.fixture
def inside() -> Iterator[Path]:
    """A scratch folder inside this repository: untracked, not ignored, removed afterwards."""
    folder = ROOT / "tests" / "unit" / f"_hook_scratch_{uuid.uuid4().hex[:8]}"
    folder.mkdir()
    yield folder
    shutil.rmtree(folder)


def test_format_ignores_other_files(inside: Path) -> None:
    notes = inside / "notes.md"
    notes.write_text("import  os\n")
    assert run_hook("format-python.sh", ROOT, {"file_path": str(notes)}).returncode == 0
    assert notes.read_text() == "import  os\n"


def test_format_is_silent_for_a_clean_file(inside: Path) -> None:
    clean = inside / "clean.py"
    clean.write_text("X = 1\n")
    result = run_hook("format-python.sh", ROOT, {"file_path": str(clean)})
    assert (result.returncode, result.stderr) == (0, "")


def test_format_fixes_the_file_and_tells_claude_to_reread_it(inside: Path) -> None:
    messy = inside / "messy.py"
    messy.write_text("import hashlib\nX=1\n")
    result = run_hook("format-python.sh", ROOT, {"file_path": str(messy)})
    assert messy.read_text() == "X = 1\n"  # formatted, and the unused import is gone
    assert result.returncode == 2
    assert "Re-read the file" in result.stderr


def test_format_leaves_files_outside_the_repository_alone(tmp_path: Path) -> None:
    other = make_repo(tmp_path / "other") / "script.py"  # another repository, and a scratch folder
    scratch = tmp_path / "scratch.py"
    for path in (other, scratch):
        path.write_text("import hashlib\nX=1\n")
        result = run_hook("format-python.sh", ROOT, {"file_path": str(path)})
        assert (result.returncode, path.read_text()) == (0, "import hashlib\nX=1\n")


def test_format_leaves_ignored_files_alone(repo: Path) -> None:
    ignored = repo / "local.json.py"
    (repo / ".gitignore").write_text("local.json\nlocal.json.py\n")
    ignored.write_text("import hashlib\nX=1\n")
    result = run_hook("format-python.sh", repo, {"file_path": str(ignored)})
    assert (result.returncode, ignored.read_text()) == (0, "import hashlib\nX=1\n")


# after-push.sh (with a fake gh)

FAKE_GH = """#!/usr/bin/env bash
if [ "$1 $2" = "pr view" ]; then
  case "$FAKE_PR" in
    none) echo 'no pull requests found for branch "feat/x"' >&2; exit 1 ;;
    error) echo "error connecting to api.github.com" >&2; exit 1 ;;
    closed) exit 0 ;;
    *) echo "OPEN" ;;
  esac
fi
"""


def run_after_push(repo: Path, tmp_path: Path, pr: str) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    git(repo, "switch", "-q", "-c", "feat/x")
    path = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    result = run_hook("after-push.sh", repo, {}, PATH=path, FAKE_PR=pr)
    assert result.returncode == 0
    return result.stdout


@pytest.mark.parametrize("pr", ["none", "closed"])
def test_after_push_asks_for_a_pull_request(repo: Path, tmp_path: Path, pr: str) -> None:
    out = json.loads(run_after_push(repo, tmp_path, pr=pr))
    assert "open-pr" in out["hookSpecificOutput"]["additionalContext"]


def test_after_push_is_silent_with_an_open_pull_request(repo: Path, tmp_path: Path) -> None:
    assert run_after_push(repo, tmp_path, pr="open") == ""


def test_after_push_is_silent_when_github_fails(repo: Path, tmp_path: Path) -> None:
    assert run_after_push(repo, tmp_path, pr="error") == ""
