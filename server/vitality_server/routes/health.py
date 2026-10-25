"""`GET /health`: how much disk the server has left. Open to any tailnet peer."""

from fastapi import APIRouter, Request
from vitality_protocol.messages import Health
from vitality_protocol.routes import HEALTH_PATH

router = APIRouter()


@router.get(HEALTH_PATH)
async def health(request: Request) -> Health:
    return Health(disk_free_bytes=request.app.state.store.disk_free_bytes())
