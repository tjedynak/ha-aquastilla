"""CI check: pull request title and body follow CONTRIBUTING.md section 4."""

import os
import re
import sys

from scripts.check_commit_msg import SUBJECT

CLOSES = re.compile(r"\bCloses #\d+\b")


def check(title: str, body: str) -> list[str]:
    """Return the list of problems with a pull request (empty when valid)."""
    problems = []
    if not SUBJECT.match(title):
        problems.append("title must follow Conventional Commits: '<type>(<scope>): <subject>'")
    if not CLOSES.search(body):
        problems.append("body must contain 'Closes #<issue>'")
    return problems


def main() -> int:
    problems = check(os.environ.get("PR_TITLE", ""), os.environ.get("PR_BODY", ""))
    for problem in problems:
        sys.stderr.write(f"pr-metadata: {problem}\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
