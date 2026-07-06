# Contributing to PINochIO

This repository follows **git-flow**. No strings get pulled directly on `main` — everything arrives through a pull request with green checks.

## Branching model

```
main ────────●───────────────●──────────►   releases only (tagged)
              \             / \
develop ───────●───●───●───●───●───●────►   integration branch
                \     /         \
feature/x        ●───●           \
hotfix/y                          ●───●──► PR into main AND develop
```

| Branch      | Created from | Merges into        | Purpose                          |
|-------------|--------------|--------------------|----------------------------------|
| `main`      | —            | —                  | production history, tagged releases (`v1.2.3`) |
| `develop`   | `main`       | `main` (via release) | integration of finished features |
| `feature/*` | `develop`    | `develop`          | new functionality                |
| `release/*` | `develop`    | `main` + `develop` | release stabilisation            |
| `hotfix/*`  | `main`       | `main` + `develop` | urgent production fixes          |

## Day-to-day workflow

```bash
# start a feature
git checkout develop && git pull
git checkout -b feature/pigpio-backend

# ...work, commit...
pytest                        # must stay at 100% coverage

git push -u origin feature/pigpio-backend
# open a PR: feature/pigpio-backend -> develop
```

Releases: branch `release/v1.1.0` from `develop`, open a PR into `main`, tag the merge commit (`git tag -a v1.1.0`), then merge back into `develop`. Hotfixes: branch from `main`, PR into `main`, tag, merge back into `develop`.

If you use the [git-flow tooling](https://github.com/nvie/gitflow), `git flow init` with all defaults matches this layout (production: `main`, development: `develop`).

## Rules of the marionette theatre

1. **No direct pushes to `main`.** Branch protection requires a pull request, and the PR can only merge when every CI check is green.
2. **100% coverage is the merge bar.** The [CI workflow](.github/workflows/ci.yml) runs `pytest` with `--cov-fail-under=100` on Python 3.9, 3.11, and 3.12. One untested line and the check goes red.
3. **Keep the DDD layering intact.** Business rules live in the `GpioBoard` aggregate; presentation and infrastructure stay behind their interfaces.
4. **Ubiquitous language.** Pins are switched on/off, PWM values are 0–255, aggregates are the only door to state changes.

## Branch protection (maintainers)

The protection rules live as importable rulesets in [`.github/rulesets/`](.github/rulesets/):

- [`protect-main.json`](.github/rulesets/protect-main.json) — `main`: PR required, all three CI checks required (strict/up-to-date), no force pushes, no deletion.
- [`protect-develop.json`](.github/rulesets/protect-develop.json) — `develop`: no force pushes, no deletion.

To (re-)apply them: **Settings → Rules → Rulesets → New ruleset ▾ → Import a ruleset** and pick each file. Enforcement applies to admins too; add a bypass actor in the ruleset if you want an emergency escape hatch.
