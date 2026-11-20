"""本地调试服务器：不装 EdgeOne CLI 也能跑。

它只做一件事——把平台注入的 context 造出来，然后把请求原样交给
agents/chat/index.py 里那个 handler。所以本地跑通的代码，部署上去
基本就是同一份。

    python local_dev.py            # http://127.0.0.1:8000
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from langgraph.checkpoint.memory import MemorySaver

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from agents.chat.index import handler as chat_handler  # noqa: E402

ENV = {k: v for k, v in os.environ.items() if k.startswith(("AI_GATEWAY_", "WSA_", "DASHSCOPE_", "IMAGE_"))}

# 会话持久化文件：重启服务历史不丢
DATA_FILE = ROOT / "data" / "conversations.json"


class LocalStore:
    """模仿 context.store：checkpointer 管「模型记得住」，messages 管「刷新看得见」。

    现在落盘到 data/conversations.json，重启服务后历史会话还在，可切换回来。
    """

    def __init__(self) -> None:
        self._saver = MemorySaver()
        self._messages: dict[str, list[dict]] = {}
        self._meta: dict[str, dict] = {}  # cid -> {title, updated_at}
        self._load()

    def _load(self) -> None:
        try:
            if DATA_FILE.exists():
                raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
                self._messages = raw.get("messages", {})
                self._meta = raw.get("meta", {})
        except Exception:  # noqa: BLE001 - 坏了就当没存过
            pass

    def _save(self) -> None:
        try:
            DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
            DATA_FILE.write_text(
                json.dumps({"messages": self._messages, "meta": self._meta}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass

    @property
    def langgraph_checkpointer(self):
        return self._saver

    async def append_message(self, conversation_id: str, role: str, content, **_kw) -> str:
        self._messages.setdefault(conversation_id, []).append({"role": role, "content": str(content)})
        meta = self._meta.setdefault(conversation_id, {})
        if role == "user" and "title" not in meta:
            meta["title"] = str(content)[:30]
        meta["updated_at"] = time.time()
        self._save()
        return f"msg_{uuid.uuid4().hex[:8]}"

    async def get_messages(self, conversation_id: str, limit: int = 50, **_kw) -> list[dict]:
        return self._messages.get(conversation_id, [])[-limit:]

    async def list_conversations(self, **_kw) -> list[dict]:
        out: list[dict] = []
        for cid, msgs in self._messages.items():
            if not msgs:
                continue
            meta = self._meta.get(cid, {})
            out.append(
                {
                    "conversation_id": cid,
                    "title": meta.get("title") or str(msgs[0]["content"])[:30],
                    "updated_at": meta.get("updated_at") or 0,
                    "count": len(msgs),
                }
            )
        out.sort(key=lambda x: x["updated_at"], reverse=True)
        return out

    async def delete_conversation(self, conversation_id: str, **_kw) -> bool:
        """删掉一段会话（消息 + 标题元信息），落盘文件同步更新。返回是否真删了东西。"""
        existed = self._messages.pop(conversation_id, None) is not None
        self._meta.pop(conversation_id, None)
        self._save()
        return existed


STORE = LocalStore()
app = FastAPI(title="OMNI · 全能智能体 · 本地调试")


class LocalContext:
    def __init__(self, body: dict, conversation_id: str) -> None:
        self.request = SimpleNamespace(body=body, headers={})
        self.conversation_id = conversation_id
        self.env = ENV
        self.store = STORE


@app.get("/health")
def health() -> dict:
    api_key = ENV.get("AI_GATEWAY_API_KEY", "")
    return {
        "status": "ok" if api_key else "degraded",
        "model": ENV.get("AI_GATEWAY_MODEL", "@makers/deepseek-v4-flash"),
        "framework": "langgraph",
    }


@app.post("/chat")
async def chat(request: Request):
    body: dict = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        pass
    conversation_id = request.headers.get("Makers-Conversation-Id") or uuid.uuid4().hex
    return await chat_handler(LocalContext(body, conversation_id))


@app.get("/history")
async def history(request: Request):
    conversation_id = request.headers.get("Makers-Conversation-Id") or ""
    messages = await STORE.get_messages(conversation_id) if conversation_id else []
    return JSONResponse({"messages": messages})


@app.get("/conversations")
async def conversations():
    """历史会话列表（按最近更新排序），给前端侧边栏用。"""
    items = await STORE.list_conversations()
    return JSONResponse({"conversations": items})


@app.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """删除一段历史会话：消息存储 + LangGraph 线程记忆一起清掉。"""
    existed = await STORE.delete_conversation(conversation_id)
    saver = getattr(STORE, "langgraph_checkpointer", None)
    try:
        if saver is not None and hasattr(saver, "delete_thread"):
            saver.delete_thread(conversation_id)
    except Exception:  # noqa: BLE001 - 记忆清不干净不影响存储删除
        pass
    return JSONResponse({"ok": True, "existed": existed})


@app.get("/")
def index():
    page = ROOT / "public" / "index.html"
    if page.exists():
        return FileResponse(page)
    return JSONResponse({"message": "缺少 public/index.html"}, status_code=404)


if __name__ == "__main__":
    import uvicorn

    if not ENV.get("AI_GATEWAY_API_KEY"):
        print("[警告] .env 里没有 AI_GATEWAY_API_KEY，对话会直接报错。")
    port = int(os.getenv("PORT", "8000"))
    print(f"本地地址：http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port)
