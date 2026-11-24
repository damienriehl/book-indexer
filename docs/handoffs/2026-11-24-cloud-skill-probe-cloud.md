# Cloud handoff: README review probe

Rules stamp — `<quote the installed portable-rules stamp line in your first reply>`

Session link — `<record this cloud session's link in the pull request>`

This handoff came from a home session through the `/cloud` skill. It stands
alone and has no private copy by design.

## Objective

Review `README.md` for accuracy against the code in `src/` and for clarity,
then report up to five concrete fixes as a short review note. Do not change
code.

## State

- **Branch:** `claude/cloud-skill-probe`, cut from `main` with no other changes.
- **Purpose:** this run also tests the `/cloud` launch path end to end.

## Constraints

- **Docs only:** edit nothing outside `docs/`.
- **No merge:** open a pull request against `main` and leave it unmerged.
- **Cloud-lane rules:** no deploys, credentials, Codex, or default-branch pushes.

## Decisions

- **Answered** — none.
- **Provisional** — none.
- **Still open** — none.

## Home-lane

The home session reads the review note on the pull request and decides which
fixes to apply.

## Evidence

- **Preflight:** `/cloud` checks passed: allow list, task type, and credential-file scan (exit 0).
