"""SSE 小工具：平台有 ctx.utils.stream_sse 就用平台的，本地没有就退回标准 Response。"""

from __future__ import annotations

import json


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def stream_response(context, agen):
    utils = getattr(context, "utils", None)
    stream_sse = getattr(utils, "stream_sse", None) if utils is not None else None
    if callable(stream_sse):
        result = stream_sse(agen)
        if hasattr(result, "__await__"):
            result = await result
        return result

    from starlette.responses import StreamingResponse

    return StreamingResponse(
        agen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
