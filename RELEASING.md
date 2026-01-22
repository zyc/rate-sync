# Releasing Rate-Sync

This document describes the release process for rate-sync maintainers.

## Overview

Rate-sync uses **tag-based automated releases** following industry best practices:

1. **Tag push** triggers the CI/CD workflow
2. **Tests run** to ensure quality
3. **Package is built** and published to PyPI via Trusted Publishing (OIDC)
4. **GitHub release** is auto-created with release notes

## Prerequisites

### One-Time Setup: PyPI Trusted Publishing

Trusted Publishing (OIDC) allows GitHub Actions to publish to PyPI without API tokens.

**Steps:**

1. Go to https://pypi.org/manage/account/publishing/
2. Add a new "pending publisher":
   - **PyPI Project Name**: `rate-sync`
   - **Owner**: `zyc` (or organization name)
   - **Repository name**: `rate-sync`
   - **Workflow name**: `publish.yml`
   - **Environment name**: `pypi`
3. Click "Add"

Once configured, the workflow will automatically authenticate using OIDC tokens.

**Note:** The `PYPI_API_TOKEN` secret is no longer needed with Trusted Publishing.

## Release Process

### 1. Prepare the Release

**Update version in `pyproject.toml`:**

```toml
[project]
version = "0.3.0"  # Bump version
```

**Update CHANGELOG.md:**

Move unreleased changes to a new version section:

```markdown
## [0.3.0] - 2026-01-22

### Added
- New feature X
- New feature Y

### Fixed
- Bug fix Z

## [Unreleased]

(empty for now)
```

**Commit changes:**

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore: bump version to 0.3.0"
git push origin main
```

### 2. Create and Push Tag

**Create annotated tag:**

```bash
git tag -a v0.3.0 -m "Release v0.3.0"
```

**Push tag to GitHub:**

```bash
git push origin v0.3.0
```

**That's it!** The CI/CD workflow will automatically:
- Run all tests
- Build the package
- Publish to PyPI
- Create a GitHub release with auto-generated notes

### 3. Monitor the Workflow

Go to: https://github.com/zyc/rate-sync/actions

Watch the "Publish to PyPI" workflow for the tag you just pushed.

**Expected steps:**
1. ✅ `test` job - runs pytest
2. ✅ `build` job - builds wheel and sdist
3. ✅ `publish` job - publishes to PyPI and creates release

### 4. Verify Publication

**Check PyPI:**

```bash
# Should show the new version
pip index versions rate-sync
```

Or visit: https://pypi.org/project/rate-sync/

**Check GitHub Release:**

Visit: https://github.com/zyc/rate-sync/releases

The release should be auto-created with:
- Release notes generated from commits
- Wheel and source distribution attached

## Versioning

Rate-sync follows [Semantic Versioning](https://semver.org/):

- **Major (X.0.0)**: Breaking changes
- **Minor (0.X.0)**: New features (backward compatible)
- **Patch (0.0.X)**: Bug fixes (backward compatible)

**Examples:**

- `0.2.0 → 0.2.1`: Bug fix
- `0.2.1 → 0.3.0`: New feature (no breaking changes)
- `0.3.0 → 1.0.0`: Breaking API changes

## Pre-releases

For alpha/beta/rc releases:

```bash
# Update pyproject.toml
version = "0.3.0a1"  # or 0.3.0b1, 0.3.0rc1

# Tag with pre-release suffix
git tag -a v0.3.0a1 -m "Release v0.3.0a1 (alpha)"
git push origin v0.3.0a1
```

Pre-releases are published to PyPI but not marked as "latest" by default.

## Manual Testing (TestPyPI)

To test the workflow without publishing to production:

**Option 1: Workflow Dispatch**

1. Go to: https://github.com/zyc/rate-sync/actions/workflows/publish.yml
2. Click "Run workflow"
3. Select environment: `testpypi`
4. Click "Run workflow"

**Option 2: Create test tag**

```bash
git tag -a v0.3.0-test -m "Test release"
git push origin v0.3.0-test
```

Then manually trigger workflow dispatch to publish to TestPyPI.

**Verify on TestPyPI:**

```bash
pip install --index-url https://test.pypi.org/simple/ rate-sync
```

## Rollback

If a release has critical issues:

**Option 1: Yank the release on PyPI**

1. Go to https://pypi.org/manage/project/rate-sync/releases/
2. Find the problematic version
3. Click "Options" → "Yank release"
4. Provide a reason

Yanked releases can still be installed explicitly but won't be chosen by default.

**Option 2: Publish a hotfix**

```bash
# Bump patch version
version = "0.3.1"

# Commit, tag, push
git add pyproject.toml CHANGELOG.md
git commit -m "fix: critical issue in 0.3.0"
git push origin main
git tag -a v0.3.1 -m "Release v0.3.1 (hotfix)"
git push origin v0.3.1
```

## Troubleshooting

### Workflow fails on "Publish to PyPI" step

**Possible causes:**

1. **Trusted Publishing not configured** - Follow "One-Time Setup" above
2. **Version already exists on PyPI** - Bump version in `pyproject.toml`
3. **Invalid package metadata** - Check `pyproject.toml` for syntax errors

### GitHub release not created

**Check:**

1. Workflow has `contents: write` permission (already configured)
2. Tag follows `v*` pattern (e.g., `v0.3.0`, not `0.3.0`)
3. Workflow completed successfully

### Tests fail during release

**Do not force the release.** Fix the tests first:

```bash
# Delete the tag locally and remotely
git tag -d v0.3.0
git push origin :refs/tags/v0.3.0

# Fix tests, commit, and try again
```

## Questions?

For questions about the release process:

- Open a [Discussion](https://github.com/zyc/rate-sync/discussions)
- Contact maintainers in the #rate-sync channel

---

**References:**

- [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
- [Semantic Versioning](https://semver.org/)
- [Keep a Changelog](https://keepachangelog.com/)
