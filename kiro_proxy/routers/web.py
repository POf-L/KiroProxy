from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse

from ..resources import get_resource_path
from ..web.html import HTML_PAGE

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


@router.get("/assets/{path:path}")
async def serve_assets(path: str):
    file_path = get_resource_path("assets") / path
    if file_path.exists():
        content_type = "image/svg+xml" if path.endswith(".svg") else "application/octet-stream"
        return StreamingResponse(open(file_path, "rb"), media_type=content_type)
    raise HTTPException(status_code=404)
