"""Sample target-app entry point — Continuum will generate the real implementation."""
from fastapi import FastAPI

app = FastAPI(title="Target App", version="0.1.0")


@app.get("/health")
async def health():
    return {"status": "ok", "note": "Continuum will generate endpoints here"}
