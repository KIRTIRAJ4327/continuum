# Langfuse observability (C4) — optional, self-hosted, Canada-resident

Continuum can export real traces, per-stage latency, cost, and an Evidence-Stack
score per run to [Langfuse](https://langfuse.com). Langfuse is **MIT-licensed and
self-hostable**, so traces stay on infrastructure you control (e.g. a
Canada-resident VM) — which is why it is the default sink over a proprietary,
Enterprise-self-host-licensed alternative.

Integration is **strictly opt-in**: with the `LANGFUSE_*` env vars unset,
`integrations/langfuse_tracer.py` is a complete no-op and the SDK is never
imported. The offline verify suite is unaffected.

## 1. Self-host Langfuse (docker compose)

```yaml
# docker-compose.langfuse.yml
services:
  langfuse-db:
    image: postgres:16
    environment:
      POSTGRES_USER: langfuse
      POSTGRES_PASSWORD: langfuse
      POSTGRES_DB: langfuse
    volumes: ["langfuse_pgdata:/var/lib/postgresql/data"]
  langfuse:
    image: langfuse/langfuse:latest
    depends_on: ["langfuse-db"]
    ports: ["3000:3000"]
    environment:
      DATABASE_URL: postgresql://langfuse:langfuse@langfuse-db:5432/langfuse
      NEXTAUTH_URL: http://localhost:3000
      NEXTAUTH_SECRET: change-me
      SALT: change-me
volumes:
  langfuse_pgdata:
```

```bash
docker compose -f docker-compose.langfuse.yml up -d
# open http://localhost:3000, create a project, copy the API keys
```

## 2. Configure Continuum

```bash
export LANGFUSE_HOST="http://localhost:3000"
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
pip install "langfuse>=2.0"      # only needed when the keys are set
```

With all three set and the SDK installed, every run:
- opens a `continuum_run` trace (`id = run_id`, `metadata.tenant_id`), and
- records an `evidence_stack` score = (passing evidence layers ÷ 6).

## 3. View traces

In the Langfuse dashboard, filter by `metadata.tenant_id` to see one tenant's
runs. The `evidence_stack` score surfaces merge-readiness at a glance and can be
charted over time to drive the gate-removal ladder.

## Notes

- All Langfuse calls are wrapped in `try/except` — a tracing failure never breaks
  a run.
- This sits alongside the M10 OpenTelemetry hook (`CONTINUUM_OTEL`): OTel → App
  Insights for infra telemetry, Langfuse for LLM-trace/eval telemetry. Both are
  opt-in and independent.
