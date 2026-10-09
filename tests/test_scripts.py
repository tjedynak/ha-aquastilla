"""Tests for the repository tooling in scripts/."""

from pathlib import Path

import pytest

from scripts import check_commit_msg, check_pr_metadata

GOOD_BODY = "Why this change.\n\nRefs: #46\n"


def test_valid_commit_message() -> None:
    assert check_commit_msg.check("ci(ci): add pipeline\n\n" + GOOD_BODY) == []


@pytest.mark.parametrize("line", ["Closes #12", "Closes: #12"])
def test_closing_reference_is_accepted(line: str) -> None:
    assert check_commit_msg.check(f"feat(api): add client\n\n{line}\n") == []


def test_refs_without_colon_rejected() -> None:
    assert check_commit_msg.check("feat(api): add client\n\nRefs #12\n")


def test_scope_is_optional() -> None:
    assert check_commit_msg.check("chore: tidy\n\nRefs: #1\n") == []


@pytest.mark.parametrize(
    "subject",
    ["Add pipeline", "ci(CI): upper scope", "wip: nope", "feat(api):no space", "feat: trailing."],
)
def test_bad_subjects_rejected(subject: str) -> None:
    assert check_commit_msg.check(f"{subject}\n\nRefs: #1\n")


def test_missing_reference_rejected() -> None:
    problems = check_commit_msg.check("feat(api): add client\n\nNo ref here.\n")
    assert any("Refs" in p for p in problems)


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


def test_pr_metadata_valid() -> None:
    assert check_pr_metadata.check("ci(ci): add pipeline", "Closes #46\n\nDetails") == []


def test_pr_metadata_accepts_refs_for_partial_work() -> None:
    assert check_pr_metadata.check("ci(ci): add pipeline", "Refs #46\n\nOnly part of it.") == []


def test_pr_metadata_requires_an_issue_link() -> None:
    problems = check_pr_metadata.check("ci(ci): add pipeline", "No link here, see #46")
    assert any("Closes" in p for p in problems)


def test_pr_metadata_requires_conventional_title() -> None:
    assert check_pr_metadata.check("Add pipeline", "Closes #46")


def test_pr_metadata_main_uses_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PR_TITLE", "ci(ci): add pipeline")
    monkeypatch.setenv("PR_BODY", "Closes #46")
    assert check_pr_metadata.main() == 0
    monkeypatch.setenv("PR_BODY", "nothing")
    assert check_pr_metadata.main() == 1
