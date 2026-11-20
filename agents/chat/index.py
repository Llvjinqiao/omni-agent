"""POST /chat —— Agent 主入口。

平台规范：
- 入口签名是 async def handler(context)，不是 FastAPI 的 @app.post
- 入参在 context.request.body（已解析好），环境变量在 context.env
- 会话 id 用 context.conversation_id（来自请求头 Makers-Conversation-Id）
- 流式返回走 context.utils.stream_sse
- 这个目录里不许出现 os.environ

SSE 事件约定（前端 index.html 按这个解析）：
    start   {"conversation_id"}
    delta   {"content"}          token 级增量，前端逐字上屏
    delta_clear {}               模型转去调工具前，清掉已流出的半截文字
    thought {"content","tool_calls":[{name,args}]}
    tool    {"name","content","ok"}
    answer  {"content"}          最终全文（前端用它覆盖流式内容，保证一致）
    error   {"message"}
    done    {}
"""

from __future__ import annotations

import os
import re
import sys
import uuid

# 平台按「文件即路由」加载入口，不保证项目根在 sys.path 上。
# 这里手动补齐，保证 agents/_graph.py 这类私有模块能被 import 到。
_CHAT_DIR = os.path.dirname(os.path.abspath(__file__))
_AGENTS_DIR = os.path.dirname(_CHAT_DIR)
_ROOT_DIR = os.path.dirname(_AGENTS_DIR)
for _path in (_ROOT_DIR, _AGENTS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

try:  # 以 agents 包的形式导入（本地和平台都走这条）
    from agents._graph import build_graph, thread_config  # noqa: E402
    from agents._model import build_llm  # noqa: E402
    from agents._sse import sse, stream_response  # noqa: E402
    from agents._tools import make_tools  # noqa: E402
except ImportError:  # agents 目录本身被当成根的情况
    from _graph import build_graph, thread_config  # noqa: E402
    from _model import build_llm  # noqa: E402
    from _sse import sse, stream_response  # noqa: E402
    from _tools import make_tools  # noqa: E402


def _friendly(exc: Exception) -> str:
    text = str(exc)
    low = text.lower()
    if "api key" in low or "401" in low or "unauthorized" in low:
        return "模型网关拒绝了这次请求：AI_GATEWAY_API_KEY 无效或已过期。"
    if "timeout" in low or "timed out" in low:
        return "模型响应超时，稍等几秒再发一次。"
    if "connection" in low or "network" in low:
        return "连不上模型网关，检查网络后重试。"
    return f"{type(exc).__name__}: {text}"


# 防幻觉护栏：模型有时不调工具、自己编一个图片链接（pollinations/假 S3 之类）。
IMG_MD = re.compile(r"!\[([^\]]*)\]\((https?://[^)\s]+)\)")

# 图缓存：同一个模型配置 + 同一个 store 时复用编译好的图，
# 免掉每次请求重建 LLM 客户端（TCP/TLS 重新握手）和 bind_tools 的开销。
_GRAPH_CACHE: dict = {"key": None, "checkpointer": None, "graph": None, "tools": None}


def _get_graph(env, checkpointer):
    from agents._model import model_config

    cfg = model_config(env)
    key = (cfg["base_url"], cfg["model"], cfg["api_key"], cfg["wsa_key"], cfg["dash_key"], cfg["image_model"])
    if (
        _GRAPH_CACHE["graph"] is not None
        and _GRAPH_CACHE["key"] == key
        and _GRAPH_CACHE["checkpointer"] is checkpointer
    ):
        return _GRAPH_CACHE["graph"], _GRAPH_CACHE["tools"]
    llm = build_llm(env)
    tools = make_tools(env)
    graph = build_graph(llm, tools, checkpointer)
    _GRAPH_CACHE.update(key=key, checkpointer=checkpointer, graph=graph, tools=tools)
    return graph, tools


async def _seed_thread(graph, store, conversation_id: str, checkpointer) -> None:
    """服务重启后 checkpointer 是空的：把存储里的历史灌回当前线程，模型才记得之前聊的。

    只在会话有历史、但图线程为空时执行一次；失败不影响本轮对话。
    """
    if store is None or checkpointer is None:
        return
    try:
        config = {"configurable": {"thread_id": conversation_id}}
        state = await graph.aget_state(config)
        if (state.values or {}).get("messages"):
            return  # 线程里已有记忆，不用灌
        history = await store.get_messages(conversation_id)
        msgs = []
        for m in history[-16:]:
            role, content = m.get("role"), str(m.get("content") or "")
            if not content:
                continue
            if role == "user":
                msgs.append(HumanMessage(content=content))
            elif role == "assistant":
                msgs.append(AIMessage(content=content))
        if msgs:
            await graph.aupdate_state(config, {"messages": msgs}, as_node="agent")
    except Exception:  # noqa: BLE001
        pass


async def _real_image(gen_tool, prompt: str) -> str:
    """替偷懒的模型真调一次画图工具。"""
    result = await gen_tool.ainvoke({"prompt": prompt[:500]})
    if result.startswith("[成功]"):
        body = result[len("[成功] "):].replace("图片已生成：\n", "").strip()
        return (
            "图片生成好了👇\n\n"
            + body
            + "\n\n👉 还可以问我：换个风格 / 加点场景 / 换个尺寸再来一张"
        )
    return f"画图服务这会儿没成功：{result[:120]}。可以换个描述再试一次。"


async def handler(context):
    body = getattr(getattr(context, "request", None), "body", None) or {}
    if not isinstance(body, dict):
        body = {}

    message = str(body.get("message") or "").strip()
    conversation_id = str(getattr(context, "conversation_id", "") or "").strip() or uuid.uuid4().hex

    if not message:
        return {"error": "message 不能为空"}

    env = getattr(context, "env", None)
    store = getattr(context, "store", None)
    checkpointer = getattr(store, "langgraph_checkpointer", None) if store is not None else None

    try:
        graph, tools = _get_graph(env, checkpointer)
        await _seed_thread(graph, store, conversation_id, checkpointer)
    except Exception as exc:  # noqa: BLE001
        async def failed():
            yield sse({"type": "start", "conversation_id": conversation_id})
            yield sse({"type": "error", "message": _friendly(exc)})
            yield sse({"type": "done"})

        return await stream_response(context, failed())

    async def events():
        yield sse({"type": "start", "conversation_id": conversation_id})
        answer = ""
        gen_ran = False  # 本轮是否真的调过画图工具
        gen_tool = next((t for t in tools if getattr(t, "name", "") == "generate_image"), None)
        try:
            # 双通道流式：updates 拿节点级结果（thought/tool/answer），
            # messages 拿 token 级增量（delta），前端逐字上屏，体感快很多。
            async for mode, payload in graph.astream(
                {"messages": [HumanMessage(content=message)]},
                config=thread_config(conversation_id),
                stream_mode=["updates", "messages"],
            ):
                if mode == "messages":
                    msg, meta = payload
                    node_name = (meta or {}).get("langgraph_node") or (meta or {}).get("node")
                    if node_name != "agent":
                        continue
                    txt = msg.content
                    if isinstance(txt, list):  # 兼容分片内容格式
                        txt = "".join(p.get("text", "") for p in txt if isinstance(p, dict))
                    txt = txt or ""
                    # 只把纯文本 token 往下发；带工具调用意图的片段交给 updates 通道处理
                    if txt and not getattr(msg, "tool_call_chunks", None):
                        yield sse({"type": "delta", "content": str(txt)})
                    continue

                for node, inner in (payload or {}).items():
                    inner = inner or {}
                    if node == "agent":
                        for msg in inner.get("messages") or []:
                            calls = getattr(msg, "tool_calls", None) or []
                            text = str(msg.content or "").strip()
                            if calls:
                                # 这一轮模型决定调工具：清掉刚才流出去的半截文字，再吐轨迹。
                                yield sse({"type": "delta_clear"})
                                yield sse(
                                    {
                                        "type": "thought",
                                        "content": text,
                                        "tool_calls": [
                                            {"name": c.get("name"), "args": c.get("args")} for c in calls
                                        ],
                                    }
                                )
                            elif text:
                                # 没有工具调用的消息就是最终回答。
                                answer = text
                                # 护栏：回答里贴了图片链接但本轮没调过画图工具 → 是编的，直接替换成真图
                                fake = IMG_MD.search(answer)
                                if fake and gen_tool is not None and not gen_ran:
                                    prompt = (fake.group(1) or message).strip() or message
                                    yield sse(
                                        {
                                            "type": "tool",
                                            "name": "generate_image",
                                            "content": "检测到模型自己编图片链接，护栏已拦截，改为真实生图…",
                                            "ok": True,
                                        }
                                    )
                                    answer = await _real_image(gen_tool, prompt)
                                yield sse({"type": "answer", "content": answer})
                    elif node == "tools":
                        for msg in inner.get("messages") or []:
                            result = str(msg.content or "")
                            if getattr(msg, "name", "") == "generate_image":
                                gen_ran = True
                            yield sse(
                                {
                                    "type": "tool",
                                    "name": getattr(msg, "name", "tool"),
                                    "content": result[:1200],
                                    "ok": not result.startswith("[失败]"),
                                }
                            )
        except Exception as exc:  # noqa: BLE001
            yield sse({"type": "error", "message": _friendly(exc)})
        finally:
            await _remember(store, conversation_id, message, answer)
            yield sse({"type": "done"})

    return await stream_response(context, events())


async def _remember(store, conversation_id: str, question: str, answer: str) -> None:
    """把这一轮写进平台会话存储，刷新页面后 /history 才拿得回来。失败不影响对话。"""
    if store is None or not answer:
        return
    try:
        await store.append_message(conversation_id=conversation_id, role="user", content=question)
        await store.append_message(conversation_id=conversation_id, role="assistant", content=answer)
    except Exception:  # noqa: BLE001
        pass
