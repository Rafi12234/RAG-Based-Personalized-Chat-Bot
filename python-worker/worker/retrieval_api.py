"""Authenticated, loopback-only internal retrieval API for local Phase 3.1.

Requires a shared secret configured independently in Node and Python env files.
This is *service authentication*, never a substitute for Express user/session
checks or Node's relational verification of each Qdrant candidate.
"""
from __future__ import annotations

from collections import OrderedDict
from hashlib import sha256
import hmac
import os
import re
from threading import BoundedSemaphore, Lock
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from dotenv import load_dotenv

load_dotenv()  # preserve the established python-worker/.env loading contract
from .retrieval_service import RetrievalInput, search_scoped_ready_points

router = APIRouter()
_TIMESTAMP_WINDOW_MS = 60_000
_seen_nonces: OrderedDict[str, int] = OrderedDict()
_nonce_lock = Lock()
_model_jobs = BoundedSemaphore(value=2)
_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_INVALID_CONTROLS = frozenset(range(0, 9)) | {11, 12} | frozenset(range(14, 32)) | {127}
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[a-z0-9]{10,128}$")  # CUID identifiers, not trusted auth


class InternalRetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organizationId: str = Field(min_length=10, max_length=128)
    knowledgeBaseId: str = Field(min_length=10, max_length=128)
    question: str = Field(min_length=1, max_length=1000)
    topK: int = Field(ge=1, le=10)

    @field_validator("organizationId", "knowledgeBaseId")
    @classmethod
    def validate_cuid(cls, value: str) -> str:
        if not _ID.fullmatch(value):
            raise ValueError("Invalid resource identifier")
        return value

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        text = value.strip()
        if not text or len(text) > 1000 or any(ord(ch) in _INVALID_CONTROLS for ch in text):
            raise ValueError("Invalid question")
        return text


def verify_signed_request(body: bytes, headers: dict[str, str], *, now_ms: int | None = None) -> bool:
    """Constant-time HMAC authentication with timestamp/nonce replay defense.

    Replay cache is intentionally process-local for a local single-process pilot;
    multi-replica production deployments must use a shared replay store or mTLS.
    """
    secret = os.getenv("DOCMIND_INTERNAL_RETRIEVAL_SECRET", "")
    if len(secret) < 32 or len(body) > 5000:
        return False
    timestamp = headers.get("x-docmind-internal-timestamp", "")
    nonce = headers.get("x-docmind-internal-nonce", "")
    signature = headers.get("x-docmind-internal-signature", "")
    if not timestamp.isascii() or not timestamp.isdecimal() or len(timestamp) > 16:
        return False
    if not _HEX_32.fullmatch(nonce) or not _HEX_64.fullmatch(signature):
        return False
    now = int(time.time() * 1000) if now_ms is None else now_ms
    try:
        when = int(timestamp)
        if abs(now - when) > _TIMESTAMP_WINDOW_MS:
            return False
    except (ValueError, OverflowError):
        return False
    signed = timestamp.encode() + b"." + nonce.encode() + b"." + body
    expected = hmac.new(secret.encode("utf-8"), signed, sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    # A validated request can only be used once per process during this window.
    with _nonce_lock:
        expired = now - _TIMESTAMP_WINDOW_MS
        while _seen_nonces and next(iter(_seen_nonces.values())) < expired:
            _seen_nonces.popitem(last=False)
        if nonce in _seen_nonces or len(_seen_nonces) >= 10_000:
            return False
        _seen_nonces[nonce] = now
    return True


@router.post("/internal/v1/retrieve")
async def internal_retrieve(request: Request):
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > 5000:
            raise HTTPException(status_code=413, detail="Retrieval request too large")
        chunks.append(chunk)
    raw = b"".join(chunks)
    headers = {key.lower(): value for key, value in request.headers.items()}
    if not verify_signed_request(raw, headers):
        raise HTTPException(status_code=401, detail="Unauthorized internal request")
    try:
        from pydantic import ValidationError
        data = InternalRetrievalRequest.model_validate_json(raw)
    except (ValidationError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid retrieval request") from None
    if not _model_jobs.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="Retrieval capacity unavailable")
    try:
        points = await run_in_threadpool(
            search_scoped_ready_points,
            RetrievalInput(data.organizationId, data.knowledgeBaseId, data.question, data.topK),
        )
        return {"points": points}
    except Exception:
        # Never return user questions, document contents, environment configuration or internals.
        raise HTTPException(status_code=503, detail="Retrieval service unavailable") from None
    finally:
        _model_jobs.release()
