# Rules for Claude

## About this repo

Python-native implementation of [h2r](../h2r) (Rust). Unlike `../h2r/sdk/python`, which wraps
the Rust shared library via ctypes, this repo implements HTTP/2 streaming, length-delimited
framing, and the static YAML peer registry natively in Python. See `../h2r/docs/architecture.md`.

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

- Update README.md's directory structure when adding a new module/package
