"""Tests for the repository tooling in scripts/."""

from pathlib import Path

import pytest

from scripts import check_commit_msg

GOOD_BODY = "Why this change.\n\nRefs: #46\n"


def test_valid_commit_message() -> None:
    assert check_commit_msg.check("ci(ci): add pipeline\n\n" + GOOD_BODY) == []


@pytest.mark.parametrize("line", ["Closes #12", "Refs: #12", "No reference at all."])
def test_a_reference_is_allowed_and_not_required(line: str) -> None:
    assert check_commit_msg.check(f"feat(api): add client\n\n{line}\n") == []


def test_scope_is_optional() -> None:
    assert check_commit_msg.check("chore: tidy\n\nRefs: #1\n") == []


@pytest.mark.parametrize(
    "subject",
    ["Add pipeline", "ci(CI): upper scope", "wip: nope", "feat(api):no space", "feat: trailing."],
)
def test_bad_subjects_rejected(subject: str) -> None:
    assert check_commit_msg.check(f"{subject}\n\nRefs: #1\n")


def test_overlong_subject_rejected() -> None:
    subject = "feat(api): " + "x" * 80
    assert any("limit" in p for p in check_commit_msg.check(f"{subject}\n\nRefs: #1\n"))


def test_merge_commits_pass_through() -> None:
    assert check_commit_msg.check("Merge branch 'main' into feature/x\n") == []


def test_comment_lines_ignored() -> None:
    assert check_commit_msg.check("# comment first\nfix(api): handle 401\n\nRefs: #12\n") == []


def test_main_reads_file_and_returns_exit_code(tmp_path: Path) -> None:
    ok = tmp_path / "ok"
    ok.write_text("fix(api): handle 401\n\nRefs: #12\n", encoding="utf-8")
    bad = tmp_path / "bad"
    bad.write_text("nonsense\n", encoding="utf-8")
    assert check_commit_msg.main(["x", str(ok)]) == 0
    assert check_commit_msg.main(["x", str(bad)]) == 1
