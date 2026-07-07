## Summary

Briefly describe what this PR changes and why.

Closes #<!-- issue number, if applicable -->

## Changes

- <!-- bullet the key changes -->

## Testing

Describe how you tested this change and paste relevant command output.

```
uv run pytest
uv run ruff check .
uv run pyright
```

## Checklist

- [ ] Tests added or updated for the change
- [ ] `uv run pytest` passes locally
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pyright` passes
- [ ] **Output remains byte-identical / determinism tests pass** (`uv run pytest -m invariants`)
- [ ] **`verify()` remains the sole locator source** — no other code path emits a page citation
- [ ] The LLM JSON schema still has zero page-like fields
- [ ] No `anthropic` / `claude_agent_sdk` imports added to `src/`
- [ ] Docs / `CHANGELOG.md` updated if user-facing behavior changed
- [ ] I understand my contribution is licensed under **AGPL-3.0-only**
