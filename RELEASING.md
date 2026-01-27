# Releasing

## Prerequisites

Configure [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (one-time):
1. Go to https://pypi.org/manage/account/publishing/
2. Add pending publisher: `rate-sync`, owner `zyc`, repo `rate-sync`, workflow `publish.yml`, environment `pypi`

## Release Checklist

### 1. Prepare

```bash
# Update version in pyproject.toml
# Update CHANGELOG.md (move Unreleased to new version)

git add pyproject.toml CHANGELOG.md
git commit -m "chore: bump version to X.Y.Z"
git push origin main
```

### 2. Tag and Release

```bash
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
```

The CI will automatically: run tests, build, publish to PyPI, create GitHub release.

### 3. Verify

```bash
pip index versions rate-sync
```

Check: https://pypi.org/project/rate-sync/

## Versioning

- **Major (X.0.0)**: Breaking changes
- **Minor (0.X.0)**: New features (backward compatible)
- **Patch (0.0.X)**: Bug fixes

## Pre-releases

```bash
# In pyproject.toml: version = "0.3.0a1"
git tag -a v0.3.0a1 -m "Release v0.3.0a1 (alpha)"
git push origin v0.3.0a1
```

## Rollback

**Yank release:** https://pypi.org/manage/project/rate-sync/releases/

**Or publish hotfix:**
```bash
# Bump patch, commit, tag, push
```
