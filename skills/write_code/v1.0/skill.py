"""
write_code skill — generates a working FastAPI application from the contract + schema.

Live path:  Azure AI generates the full implementation.
Offline path: Deterministic FastAPI + SQLAlchemy scaffold derived from the contract.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, Optional

logger = logging.getLogger(__name__)


async def write_code(
    contract: str,
    schema: str,
    language: str = "python",
) -> Dict[str, str]:
    """
    Generate implementation code from the OpenAPI contract and SQL schema.

    Args:
        contract: OpenAPI 3.0 YAML string.
        schema:   PostgreSQL DDL string.
        language: Target language (default "python").

    Returns:
        {"<file_path>": "<file_content>", ...}
    """
    resource = _extract_resource(contract)

    # Try live LLM
    llm_files = await _azure_llm_code(contract, schema, resource, language)
    if llm_files:
        logger.info("[write_code] LLM generated %d files for resource '%s'", len(llm_files), resource)
        return llm_files

    # Deterministic scaffold
    logger.info("[write_code] Offline scaffold for resource '%s'", resource)
    return _build_scaffold(resource, contract, schema)


# --------------------------------------------------------------------------- #
# Deterministic scaffold generator
# --------------------------------------------------------------------------- #
def _extract_resource(contract: str) -> str:
    """Parse the first path segment from the OpenAPI contract."""
    m = re.search(r"^\s{2}/([a-z][a-z0-9_-]+)s?:", contract, re.MULTILINE)
    if m:
        return m.group(1).rstrip("s")
    m = re.search(r"title:\s*[\"']?([^\"'\n]+)[\"']?", contract)
    if m:
        words = re.sub(r"[^a-z0-9\s]", "", m.group(1).lower()).split()
        stop = {"api", "service", "application", "app", "the", "a", "an"}
        candidates = [w for w in words if w not in stop and len(w) > 2]
        if candidates:
            return candidates[-1]
    return "item"


def _build_scaffold(resource: str, contract: str, schema: str) -> Dict[str, str]:
    """Generate a complete FastAPI project scaffold."""
    res = resource
    res_cap = res.capitalize()
    res_pl = res + "s"

    # --- pyproject.toml / requirements ---
    requirements = (
        "fastapi>=0.115.0\n"
        "uvicorn[standard]>=0.29.0\n"
        "pydantic>=2.5.0\n"
        "sqlalchemy>=2.0.0\n"
        "asyncpg>=0.29.0\n"
        "alembic>=1.13.0\n"
        "python-dotenv>=1.0.0\n"
        "httpx>=0.27.0   # test client\n"
        "pytest>=7.4.0\n"
        "pytest-asyncio>=0.23.0\n"
    )

    # --- src/config.py ---
    config_py = '''\
"""Application configuration (12-factor: all config from env)."""
from __future__ import annotations
import os
from functools import lru_cache
from pydantic import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Continuum API"
    debug: bool = False
    database_url: str = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost/app")
    api_version: str = "1.0.0"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
'''

    # --- src/database.py ---
    database_py = '''\
"""Async SQLAlchemy engine + session factory."""
from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from .config import get_settings


class Base(DeclarativeBase):
    pass


def _engine():
    return create_async_engine(
        get_settings().database_url,
        echo=get_settings().debug,
        pool_size=10,
        max_overflow=20,
    )


_engine_instance = None


def get_engine():
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = _engine()
    return _engine_instance


SessionLocal = async_sessionmaker(bind=None, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    """FastAPI dependency — yields a DB session."""
    async with AsyncSession(get_engine()) as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
'''

    # --- src/models.py ---
    models_py = f'''\
"""SQLAlchemy ORM models — generated from contract schema."""
from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base


class {res_cap}(Base):
    """ORM model for the {res_cap} resource."""
    __tablename__ = "{res_pl}"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def to_dict(self) -> dict:
        return {{
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }}
'''

    # --- src/schemas.py ---
    schemas_py = f'''\
"""Pydantic schemas for request/response validation."""
from __future__ import annotations
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field


class {res_cap}Input(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="{res_cap} name")
    description: Optional[str] = Field(None, description="Optional description")


class {res_cap}Out(BaseModel):
    id: str
    name: str
    description: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class Paginated{res_cap}(BaseModel):
    items: List[{res_cap}Out]
    total: int
    page: int
    page_size: int
'''

    # --- src/repository.py ---
    repository_py = f'''\
"""Data access layer for {res_cap}."""
from __future__ import annotations
from typing import List, Optional, Tuple
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from .models import {res_cap}
from .schemas import {res_cap}Input


class {res_cap}Repository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list(self, page: int = 1, page_size: int = 20) -> Tuple[List[{res_cap}], int]:
        offset = (page - 1) * page_size
        items_q = await self.db.execute(
            select({res_cap}).order_by({res_cap}.created_at.desc()).offset(offset).limit(page_size)
        )
        count_q = await self.db.execute(select(func.count({res_cap}.id)))
        return items_q.scalars().all(), count_q.scalar_one()

    async def get(self, item_id: str) -> Optional[{res_cap}]:
        result = await self.db.execute(select({res_cap}).where({res_cap}.id == item_id))
        return result.scalar_one_or_none()

    async def create(self, data: {res_cap}Input) -> {res_cap}:
        item = {res_cap}(name=data.name, description=data.description)
        self.db.add(item)
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def update(self, item_id: str, data: {res_cap}Input) -> Optional[{res_cap}]:
        item = await self.get(item_id)
        if item is None:
            return None
        item.name = data.name
        item.description = data.description
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def delete(self, item_id: str) -> bool:
        result = await self.db.execute(
            delete({res_cap}).where({res_cap}.id == item_id)
        )
        return result.rowcount > 0
'''

    # --- src/router.py ---
    router_py = f'''\
"""FastAPI router for {res_cap} endpoints."""
from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from .database import get_db
from .repository import {res_cap}Repository
from .schemas import {res_cap}Input, {res_cap}Out, Paginated{res_cap}

router = APIRouter(prefix="/{res_pl}", tags=["{res}"])
DB = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=Paginated{res_cap})
async def list_{res}(
    db: DB,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List all {res_pl} (paginated)."""
    repo = {res_cap}Repository(db)
    items, total = await repo.list(page, page_size)
    return {{"items": items, "total": total, "page": page, "page_size": page_size}}


@router.post("", response_model={res_cap}Out, status_code=201)
async def create_{res}(body: {res_cap}Input, db: DB):
    """Create a new {res}."""
    repo = {res_cap}Repository(db)
    return await repo.create(body)


@router.get("/{{item_id}}", response_model={res_cap}Out)
async def get_{res}(item_id: str, db: DB):
    """Get a {res} by ID."""
    repo = {res_cap}Repository(db)
    item = await repo.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"{res_cap} not found")
    return item


@router.put("/{{item_id}}", response_model={res_cap}Out)
async def update_{res}(item_id: str, body: {res_cap}Input, db: DB):
    """Update a {res}."""
    repo = {res_cap}Repository(db)
    item = await repo.update(item_id, body)
    if item is None:
        raise HTTPException(status_code=404, detail=f"{res_cap} not found")
    return item


@router.delete("/{{item_id}}", status_code=204)
async def delete_{res}(item_id: str, db: DB):
    """Delete a {res}."""
    repo = {res_cap}Repository(db)
    deleted = await repo.delete(item_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"{res_cap} not found")
'''

    # --- src/main.py ---
    main_py = '''\
"""FastAPI application entry point — generated by Continuum write_code skill."""
from __future__ import annotations
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .config import get_settings
from .router import router

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.api_version,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": settings.api_version}
'''

    # --- tests/conftest.py ---
    conftest_py = f'''\
"""Pytest fixtures for {res_cap} API tests."""
import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from src.main import app
from src.database import Base, get_db

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def engine():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db(engine):
    async with AsyncSession(engine) as session:
        yield session
        await session.rollback()


@pytest.fixture
async def client(db):
    app.dependency_overrides[get_db] = lambda: db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
'''

    # --- tests/test_api.py ---
    test_api_py = f'''\
"""Integration tests for {res_cap} CRUD API."""
import pytest

pytestmark = pytest.mark.asyncio


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_list_empty(client):
    resp = await client.get("/{res_pl}")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data and "total" in data


async def test_create_{res}(client):
    resp = await client.post("/{res_pl}", json={{"name": "Test {res_cap}"}})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Test {res_cap}"
    assert "id" in data


async def test_get_{res}(client):
    created = (await client.post("/{res_pl}", json={{"name": "Get Test"}})).json()
    resp = await client.get(f"/{res_pl}/{{created['id']}}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


async def test_update_{res}(client):
    created = (await client.post("/{res_pl}", json={{"name": "Old Name"}})).json()
    resp = await client.put(f"/{res_pl}/{{created['id']}}", json={{"name": "New Name"}})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"


async def test_delete_{res}(client):
    created = (await client.post("/{res_pl}", json={{"name": "To Delete"}})).json()
    resp = await client.delete(f"/{res_pl}/{{created['id']}}")
    assert resp.status_code == 204


async def test_not_found(client):
    resp = await client.get("/{res_pl}/nonexistent-id")
    assert resp.status_code == 404
'''

    return {
        "requirements.txt":       requirements,
        "src/__init__.py":        "",
        "src/config.py":          config_py,
        "src/database.py":        database_py,
        "src/models.py":          models_py,
        "src/schemas.py":         schemas_py,
        "src/repository.py":      repository_py,
        "src/router.py":          router_py,
        "src/main.py":            main_py,
        "tests/__init__.py":      "",
        "tests/conftest.py":      conftest_py,
        "tests/test_api.py":      test_api_py,
    }


# --------------------------------------------------------------------------- #
# Azure AI helper — generate full implementation via LLM
# --------------------------------------------------------------------------- #
async def _azure_llm_code(
    contract: str, schema: str, resource: str, language: str
) -> Optional[Dict[str, str]]:
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    endpoint = (
        os.getenv("AZURE_AI_ENDPOINT") or os.getenv("AZURE_OPENAI_ENDPOINT") or ""
    ).rstrip("/")
    deployment = (
        os.getenv("AZURE_DEPLOYMENT_STRONG") or os.getenv("AZURE_DEPLOYMENT_ID") or ""
    )
    if not (api_key and endpoint and deployment):
        return None

    prompt = (
        f"Generate a production-ready {language} FastAPI application from this spec.\n\n"
        f"OpenAPI contract (excerpt):\n{contract[:1500]}\n\n"
        f"Database schema (excerpt):\n{schema[:800]}\n\n"
        "Return a JSON object where keys are file paths and values are file contents.\n"
        "Include: src/main.py, src/models.py, src/schemas.py, src/router.py, tests/test_api.py.\n"
        "Use SQLAlchemy 2.x async ORM, Pydantic v2, FastAPI 0.115+.\n"
        "Return ONLY the JSON object — no prose, no markdown."
    )

    try:
        from langchain_azure_ai.chat_models import AzureChatCompletions  # type: ignore
        from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore

        model = AzureChatCompletions(
            azure_endpoint=endpoint,
            azure_deployment=deployment,
            api_key=api_key,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
            max_tokens=4096,
        )
        resp = await model.ainvoke(
            [
                SystemMessage(
                    content=(
                        "You are a senior backend engineer. Generate complete, working FastAPI code. "
                        "Return ONLY valid JSON with file paths as keys and file contents as values."
                    )
                ),
                HumanMessage(content=prompt),
            ]
        )
        raw = (getattr(resp, "content", "") or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"```$", "", raw).strip()
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and parsed:
            return parsed
    except Exception as exc:  # noqa: BLE001
        logger.debug("[write_code] LLM code gen skipped: %s", exc)
    return None
