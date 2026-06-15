# Pull request

## Summary

<!-- What does this PR do and why? -->

## Type of change

- [ ] Bug fix
- [ ] New CLI adapter (OpenCode, Codex, ...)
- [ ] New judge provider / format
- [ ] Enhancement to existing behavior
- [ ] Documentation
- [ ] Other

## If this adds or changes a CLI adapter

<!-- Skip if not applicable. -->

- CLI: <!-- e.g. OpenCode -->
- Stop-equivalent hook event verified: <!-- name + link to current docs -->
- Neutral decision translated correctly (block → keep working / allow → stop): <!-- yes/no -->
- Fails open on every error path (never traps the agent): <!-- yes/no -->
- `core/` unchanged: <!-- yes/no -->

## If this adds or changes a judge provider

- Format/branch added to `build_request` / `parse_response`: <!-- yes/no -->
- Key referenced by env-var name only, never stored: <!-- yes/no -->
- Mocked-HTTP tests cover success / HTTP error / timeout / malformed JSON: <!-- yes/no -->

## Checklist

- [ ] `make test` passes (all tests green)
- [ ] `make coverage` shows 100% coverage
- [ ] No third-party runtime dependency added (`core/` stays stdlib-only)
- [ ] All hook scripts fail open (allow stop on any error)
- [ ] Updated docs / README / CHANGELOG where relevant

## Related issues

<!-- e.g. Closes #12 -->
