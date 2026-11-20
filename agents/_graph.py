"""LangGraph 状态图：单节点 ReAct 循环（对应 runoob LangGraph 快速上手的标准写法）。

    START → agent ─(有 tool_calls)→ tools ─┐
                ↑──────────────────────────┘
                └─(没有 tool_calls)→ END

用到的 LangGraph 核心概念：
- StateGraph + Annotated[list, add_messages]：消息状态，add_messages 自动追加而非覆盖。
- ToolNode + tools_condition：预置工具节点和「要不要调工具」的路由判断。
- MemorySaver checkpointer：按 thread_id 存每个会话的消息，多轮对话记得住。

全能助手不需要投资人那种「调研→判定→点评」的多段流水线，
一个标准 ReAct 循环 + 强提示词就够了：模型自己决定搜不搜、算不算，
查完工具回到 agent 再想下一步，直到能回答为止。
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from ._prompt import SYSTEM_PROMPT

# prefill 瘦身参数：上下文条数上限 + 旧工具结果截断长度。
# 搜索结果 / 网页正文动辄几千字，全量留在历史里会显著拖慢每次请求的首 token。
MAX_HISTORY = 16
MAX_OLD_TOOL_CHARS = 400


def _slim_history(messages: list) -> list:
    """只带最近几条消息进模型；除最后一条外，旧工具结果截断到少量字符。"""
    recent = list(messages)[-MAX_HISTORY:]
    last_tool_idx = -1
    for i, m in enumerate(recent):
        mtype = getattr(m, "type", "")
        if mtype == "tool":
            last_tool_idx = i
    slimmed = []
    for i, m in enumerate(recent):
        if getattr(m, "type", "") == "tool" and i < last_tool_idx:
            content = str(getattr(m, "content", "") or "")
            if len(content) > MAX_OLD_TOOL_CHARS:
                from langchain_core.messages import ToolMessage

                m = ToolMessage(
                    content=content[:MAX_OLD_TOOL_CHARS] + "…（旧工具结果已截断）",
                    tool_call_id=m.tool_call_id,
                    name=getattr(m, "name", "") or "",
                )
        slimmed.append(m)
    return slimmed


class AssistantState(TypedDict):
    messages: Annotated[list, add_messages]


def build_graph(llm, tools, checkpointer=None):
    llm_with_tools = llm.bind_tools(tools)

    async def agent(state: AssistantState) -> dict:
        reply = await llm_with_tools.ainvoke(
            [{"role": "system", "content": SYSTEM_PROMPT}, *_slim_history(state["messages"])]
        )
        return {"messages": [reply]}

    builder = StateGraph(AssistantState)
    builder.add_node("agent", agent)
    builder.add_node("tools", ToolNode(tools))

    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")

    return builder.compile(checkpointer=checkpointer or MemorySaver())


def thread_config(conversation_id: str, max_rounds: int = 8) -> dict:
    return {
        "configurable": {"thread_id": conversation_id},
        "recursion_limit": max_rounds * 2 + 4,
    }
