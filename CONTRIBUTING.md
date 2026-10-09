# Working conventions

Every piece of work here is traceable end to end:
**issue → branch → commits → pull request → merge**. No step is optional, and
nothing lands on `main` without passing through all of them.

The point is not ceremony. Six months from now the only surviving explanation of
why something was built a particular way will be the issue that asked for it and
the pull request that answered. Commits that cannot be traced back to an issue
are archaeology waiting to happen.

## 1. Issues are the unit of work

Every story exists as an issue carrying a story ID (`AQ-1.2`, `F0.3`), a
milestone, labels, acceptance criteria and dependencies on other issues.

Work that is not in an issue does not get done. If something needs doing and has
no issue, open one first; it takes a minute and it is the only record of why.

The backlog is generated from the plan by an idempotent bootstrap script kept
alongside the project documentation, outside this repository.

### Milestones

| Milestone | Meaning |
|---|---|
| `M-1 Analysis` | distributed analysis of the vendor app |
| `M0 Capture` | real API responses and fixtures |
| `M1 Read-only MVP` | client, skeleton, read-only entities, HACS packaging |
| `M2 Control` | full device control — feature parity completes here |
| `M3 Push and history` | WebSocket push, long-term statistics |
| `M4 Presentation` | dashboard, alerts, automations |
| `Backlog` | cross-cutting and optional work with no fixed milestone |

### Labels

`area:*` says which part of the system, `type:*` says what kind of change,
`risk:destructive` flags anything irreversible, `blocked` means a dependency or
an external decision is in the way.

## 2. Branches

Conventional Branches, with the issue number attached:

```
<type>/<issue-number>-<short-kebab-description>
```

```
feature/12-http-client-auth
fix/31-websocket-reconnect-storm
docs/4-parity-matrix-merge
chore/2-ci-pipeline
```

Types: `feature`, `fix`, `docs`, `chore`, `refactor`, `test`, `release`.

One branch per issue. Branch from `main`, keep it short-lived, rebase rather
than merge `main` back in.

`main` is protected. Never commit to it directly, never force-push it.

## 3. Commits

[Conventional Commits](https://www.conventionalcommits.org/), in English:

```
<type>(<scope>): <subject>

<body — why, not what>

Refs: #<issue>
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`,
`ci`, `chore`.

Scopes follow the code: `api`, `coordinator`, `config-flow`, `sensor`,
`binary-sensor`, `button`, `switch`, `number`, `time`, `services`, `statistics`,
`ci`.

```
feat(sensor): expose salt level and days remaining

The app surfaces both a percentage and a predicted days-to-empty figure, and
they disagree often enough to be worth showing separately.

Refs: #15
```

Every commit carries `Refs: #<issue>`. The commit that completes the work says
`Closes #<issue>` instead, so Gitea closes the issue on merge.

Subject line: imperative mood, lower case, no trailing full stop, under
72 characters. The body explains **why** — the diff already shows what.

## 4. Pull requests

One PR per issue. Title follows the same Conventional Commits format. The body
must contain:

- `Closes #<issue>` so the link is machine-readable
- what changed and why, in prose
- a test plan — what was run, what was checked by hand
- anything a reviewer should look at sceptically

PRs are **never merged autonomously**. Merging is always an explicit,
in-the-moment decision by the repository owner, even when CI is green. An agent
opens the PR and stops there.

## 5. Dependencies between issues

Issue dependencies encode the real ordering. The analysis merge is blocked by
every analysis issue; everything in M1 is blocked by the fixture capture, because
writing entities before the response schemas are known means guessing at field
names.

Before starting an issue, check it has no open blockers.

## 6. What belongs in this repository

Code, tests, and the documents a user or contributor needs: this file, the
README, and `CLAUDE.md`.

Planning documents, the API map, the parity matrix and all reverse-engineering
material live **outside** the repository. No vendor code is redistributed here,
and the repository stays an engineering artefact rather than a record of how the
protocol was worked out. `CLAUDE.md` says where to find the rest.

## 7. Definition of done

An issue closes when:

- acceptance criteria in the issue body are all met
- tests cover the change and the suite passes
- the project changelog and, where relevant, the decision log are updated
- the PR is merged by the owner

## 8. Things that are never automated

Some operations are irreversible or reach outside our own infrastructure. These
always require an explicit human decision, every time:

- merging a pull request
- calling the water shut-off endpoint
- deleting a device from the vendor account
- any request to the vendor backend beyond what an approved task needs
- changing repository settings, branch protection or CI secrets
