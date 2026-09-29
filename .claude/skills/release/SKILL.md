---
name: release
description: Release a new version of hone-lens - version bump, changelog, release PR, tag that publishes to PyPI. Only when the maintainer asks for a release.
disable-model-invocation: true
argument-hint: "<version>"
---

# Release hone-lens `$ARGUMENTS`

1. `main` is up to date and clean, and CI on `main` is green (`gh run list --branch main --limit 3`).
2. `start-task` with `chore/release-<version>`.
3. The version lives only in `src/hone_lens/__init__.py` (`pyproject.toml` reads it). SemVer; while on 0.x, a
   breaking change bumps the minor version.
4. `CHANGELOG.md`: the unreleased section becomes `## [<version>] - <YYYY-MM-DD>`; add an empty
   unreleased section above it. Change records built in this release: `implemented in <version>`.
5. `verify-before-done`, then `open-pr` (claude[bot] reviews it). The maintainer merges.
6. After the merge, on `main`: `git tag -a v<version> -m "hone-lens <version>"`. **Ask the maintainer
   before** `git push origin v<version>`: the tag runs `.github/workflows/release.yml`, which publishes
   to PyPI through trusted publishing (environment `pypi`). Watch it: `gh run watch`.
7. `gh release create v<version> --title "hone-lens <version>" --notes "<the CHANGELOG section>"`.
