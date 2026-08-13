# Rules for Claude

## About this repo

h2r is a lightweight pub/sub middleware for robots built on raw HTTP/2 streaming (see
README.md). This repo is a pure-Python implementation: HTTP/2 streaming, length-delimited
framing, and the static YAML peer registry are all implemented natively in Python.

## Forbidden (strict)

- Never push directly to main (including bypassing branch protection with admin rights)
- Never merge a PR without the user's explicit instruction to merge
- Never open a PR without the user's explicit instruction to open one

## Git

- Commit messages: `type(scope): message`
- Only feature branches may be pushed

## Development environment

- No tooling other than Docker: ruff/ty/pytest/python always run via `docker compose run --rm
  dev ...` or the `make` targets that wrap it — never directly on the host (`pre-commit` itself
  and its generic file hooks are the exception; see `.pre-commit-config.yaml`)
- `make check` (ruff + ty --error all + pytest) runs automatically before `git push` via
  `.claude/scripts/pre-push-check.sh`; do not push if it fails

## Docs

- Don't write documentation beyond what's asked for. Prefer code (type signatures,
  docstrings stating the contract) over prose design docs
- Update README.md's directory structure when adding a new module/package
