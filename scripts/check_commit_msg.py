"""Commit-msg hook: Conventional Commits subject plus an issue reference.

Rules come from CONTRIBUTING.md section 3.
"""

import re
import sys
from pathlib import Path

TYPES = "feat|fix|docs|style|refactor|perf|test|build|ci|chore"
SUBJECT = re.compile(rf"^(?:{TYPES})(?:\([a-z0-9-]+\))?!?: \S.*$")
REFERENCE = re.compile(r"^(?:Refs: |Closes:? )#\d+$", re.MULTILINE)
MAX_SUBJECT = 72
PASSTHROUGH = ("Merge ", "Revert ", "fixup! ", "squash! ")


def check(message: str) -> list[str]:
    """Return the list of problems with a commit message (empty when valid)."""
    lines = [line for line in message.splitlines() if not line.startswith("#")]
    subject = lines[0] if lines else ""
    if subject.startswith(PASSTHROUGH):
        return []
    problems = []
    if not SUBJECT.match(subject):
        problems.append(f"subject must be '<type>(<scope>): <subject>' with type in {TYPES}")
    if len(subject) > MAX_SUBJECT:
        problems.append(f"subject is {len(subject)} characters, limit is {MAX_SUBJECT}")
    if subject.endswith("."):
        problems.append("subject must not end with a full stop")
    if not REFERENCE.search("\n".join(lines)):
        problems.append("missing 'Refs: #<issue>' or 'Closes #<issue>' line")
    return problems


def main(argv: list[str]) -> int:
    problems = check(Path(argv[1]).read_text(encoding="utf-8"))
    for problem in problems:
        sys.stderr.write(f"commit-msg: {problem}\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
