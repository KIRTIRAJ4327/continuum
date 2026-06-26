# `.pdlc/config.yml` — per-app onboarding config (C3)

Continuum is the **platform**; each target application is **data**. To onboard a
new app you add a `.pdlc/config.yml` to that app's repo — no Continuum code
changes. This is the D33 two-layer split (M8) taken to its conclusion: the
platform repo holds the agents/gates/skills, the app repo holds its spec,
mappings, and build commands.

## Location

```
<target-repo>/.pdlc/config.yml
```

`CONTINUUM_TARGET_REPO` points at `<target-repo>`. `orchestrator/agent_runner.py`
`_load_repo_config(target_repo)` reads this file; when it is absent the Python
defaults below are used, so Continuum's own repo and the offline verify suite are
unaffected.

## Schema

```yaml
app_name: "Retail Banking App"        # human label (shown in reports)
stack: "java-spring-angular"          # python | node | java-spring-angular | ...
lint_cmd: "mvn checkstyle:check"      # how to lint this app
typecheck_cmd: null                   # null when the stack has no separate typecheck
test_cmd: "mvn test -q"               # how to run this app's tests
business_mappings:                    # M7 scope-guard inputs for this app
  - {code: "BR",  label: "Branch"}
  - {code: "WEB", label: "Web"}
ado_project: "RetailBankingApp"       # optional — ADO webhook routing
ado_repo: "retail-banking-app"        # optional
```

### Defaults (when the file is absent)

| Key | Default |
|---|---|
| `stack` | `python` |
| `lint_cmd` | `ruff check .` |
| `typecheck_cmd` | `mypy . --ignore-missing-imports` |
| `test_cmd` | `pytest -q` |
| `business_mappings` | `[]` |

## How it is used

- **Gate commands** — `lint_cmd` / `typecheck_cmd` / `test_cmd` feed the Box Lite
  suite (P0.2) so the local-verify gates run *this app's* toolchain. (Wiring the
  split gates to consume these per-app commands instead of the built-in Python
  trio is the active follow-up; the loader and schema are in place today.)
- **Scope guard** — `business_mappings` seed the M7 scope-guard when a run is
  triggered without explicit mappings (e.g. from a webhook).
- **Webhook routing** — `ado_project` / `ado_repo` let `POST /webhooks/ado`
  associate an incoming work item with the right app.

## Offline-safe

`_load_repo_config` never raises: a missing file, missing PyYAML, or malformed
YAML all degrade to the defaults. No network, no required dependency.
