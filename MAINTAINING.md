# Maintaining ferris

Notes for the Ferris team members who review, merge and release this project.

## Triage

- Answer new issues within a few working days, even if only to say when we'll look.
- Label each issue: `bug`, `enhancement`, `docs` or `question`; add `good first issue` to small, well-defined ones and `help wanted` when we'd take an outside pull request.
- Ask for `ferris doctor` output and the agent when a bug report lacks them. Close issues that can't be reproduced after two weeks without a reply, with a note that they can be reopened.
- If a report contains client data or a security problem, hide or delete it and follow [SECURITY.md](SECURITY.md).

## Reviewing pull requests

- CI must be green, and a change in behavior comes with a test.
- Check agent-facing output (`ferris state`, `ferris watch`) stays plain off a terminal.
- No network calls and no runtime dependencies.
- Changes to the skill's method (prompts, the checks in `engine.py` and `flatstore.py`, the steps in `references/`) change what every project's state looks like. Agree on them in an issue before merging, and test them on a real project, not only the sample.
- Merge with a clean commit message in the `feat:` / `fix:` / `docs:` / `chore:` form.

## Releasing

Versions follow [semantic versioning](https://semver.org): a patch for fixes, a minor for new commands or options, a major for a change that breaks existing workspaces, flags or output the agent relies on. While the version is 0.x, breaking changes bump the minor.

1. Move the "Unreleased" entries in `CHANGELOG.md` under the new version and date.
2. Bump the version: `npm version <patch|minor|major> --no-git-tag-version`.
3. Run `npm test` and check the package contents with `npm pack --dry-run` (only `bin`, `src`, `skill`, `README.md` and `LICENSE`; no `__pycache__`).
4. Commit (`chore: release vX.Y.Z`), merge to `main`, then tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.
5. Publish from a clean checkout of the tag: `npm publish` (the package is public through `publishConfig`; npm asks for your two-factor code).
6. Create a GitHub release for the tag with the changelog entry.
7. Check the install: `npm install -g @ferris-ai/cli@X.Y.Z && ferris doctor`.

If a release is broken, publish a fixed patch. Use `npm deprecate @ferris-ai/cli@X.Y.Z "<reason>"` for the broken one; don't unpublish.
