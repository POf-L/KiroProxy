import uuid

import httpx
from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse

from ..config import MODELS_URL
from ..core import state
from ..core.auth_middleware import require_api_auth
from ..credential import get_kiro_version
from ..handlers import anthropic, gemini, openai
from ..handlers import responses as responses_handler

router = APIRouter()


@router.get("/v1/models")
async def models(_: bool = Depends(require_api_auth)):
    try:
        account = state.get_available_account()
        if not account:
            raise Exception("No available account")

        token = account.get_token()
        machine_id = account.get_machine_id()
        kiro_version = get_kiro_version()

        headers = {
            "content-type": "application/json",
            "x-amz-user-agent": f"aws-sdk-js/1.0.0 KiroIDE-{kiro_version}-{machine_id}",
            "amz-sdk-invocation-id": str(uuid.uuid4()),
            "Authorization": f"Bearer {token}",
        }
        async with httpx.AsyncClient(verify=False, timeout=30) as client:
            resp = await client.get(MODELS_URL, headers=headers, params={"origin": "AI_EDITOR"})
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "object": "list",
                    "data": [
                        {
                            "id": m["modelId"],
                            "object": "model",
                            "owned_by": "kiro",
                            "name": m["modelName"],
                        }
                        for m in data.get("models", [])
                    ],
                }
    except Exception:
        pass

    return {
        "object": "list",
        "data": [
            {"id": "auto", "object": "model", "owned_by": "kiro", "name": "Auto"},
            {
                "id": "claude-sonnet-4.5",
                "object": "model",
                "owned_by": "kiro",
                "name": "Claude Sonnet 4.5",
            },
            {"id": "claude-sonnet-4", "object": "model", "owned_by": "kiro", "name": "Claude Sonnet 4"},
            {
                "id": "claude-haiku-4.5",
                "object": "model",
                "owned_by": "kiro",
                "name": "Claude Haiku 4.5",
            },
        ],
    }


@router.post("/v1/messages")
async def anthropic_messages(request: Request, _: bool = Depends(require_api_auth)):
    print(f"[Main] Received /v1/messages request from {request.client.host}")
    return await anthropic.handle_messages(request)


@router.post("/v1/messages/count_tokens")
async def anthropic_count_tokens(request: Request, _: bool = Depends(require_api_auth)):
    return await anthropic.handle_count_tokens(request)


@router.post("/v1/complete")
async def anthropic_complete(request: Request, _: bool = Depends(require_api_auth)):
    print(f"[Main] Received /v1/complete request from {request.client.host}")
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "type": "invalid_request_error",
                "message": "KiroProxy currently only supports /v1/messages. Please check if your client can be configured to use Messages API.",
            }
        },
    )


@router.post("/v1/chat/completions")
async def openai_chat(request: Request, _: bool = Depends(require_api_auth)):
    return await openai.handle_chat_completions(request)


@router.post("/v1/responses")
async def openai_responses(request: Request, _: bool = Depends(require_api_auth)):
    return await responses_handler.handle_responses(request)


@router.post("/v1beta/models/{model_name}:generateContent")
@router.post("/v1/models/{model_name}:generateContent")
async def gemini_generate(model_name: str, request: Request, _: bool = Depends(require_api_auth)):
    return await gemini.handle_generate_content(model_name, request)
