"""HMAC-authenticated internal route; only a configured disposable local KB."""
from typing import Literal
from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field,field_validator,ValidationError
from starlette.concurrency import run_in_threadpool
import re

from .retrieval_api import verify_signed_request,InternalRetrievalRequest
from .shadow_reader import SessionScope
from .advanced_live_service import RequestScope,run

router=APIRouter()


class AdvancedRequest(InternalRetrievalRequest):
    model_config=ConfigDict(extra="forbid")
    userId:str=Field(min_length=10,max_length=128)
    sessionId:str=Field(min_length=10,max_length=128)
    tokenHash:str=Field(pattern=r"^[0-9a-f]{64}$")
    operation:Literal["answer","retrieve"]

    @field_validator("userId","sessionId")
    @classmethod
    def validate_session_id(cls,v):
        if not re.fullmatch(r"[a-z0-9]{10,128}",v):
            raise ValueError("Invalid session scope")
        return v


@router.post("/internal/v2/advanced")
async def advanced(request:Request):
    chunks=[]
    size=0
    async for chunk in request.stream():
        size+=len(chunk)
        if size>5000:
            raise HTTPException(status_code=413,detail="Request too large")
        chunks.append(chunk)
    raw=b"".join(chunks)
    if not verify_signed_request(raw,{k.lower():v for k,v in request.headers.items()}):
        raise HTTPException(status_code=401,detail="Unauthorized internal request")
    try:
        data=AdvancedRequest.model_validate_json(raw)
    except (ValidationError,ValueError):
        raise HTTPException(status_code=400,detail="Invalid advanced request") from None
    try:
        scope=RequestScope(SessionScope(data.userId,data.sessionId,data.tokenHash),
                           data.organizationId,data.knowledgeBaseId)
        return await run_in_threadpool(run,scope,data.question,data.topK,data.operation)
    except Exception:
        raise HTTPException(status_code=503,detail="Advanced local answer service unavailable") from None
