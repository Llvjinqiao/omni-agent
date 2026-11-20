"""不依赖网络和密钥的冒烟测试。

    python tests/test_agent.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents._graph import build_graph, thread_config  # noqa: E402
from agents._model import model_config  # noqa: E402
from agents._sse import sse  # noqa: E402
from agents._tools import _safe_eval, make_tools  # noqa: E402

passed = 0
failed: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed.append(name)
        print(f"  FAIL  {name} {detail}")


print("[1] 模型配置")
cfg = model_config({"AI_GATEWAY_API_KEY": "sk-x", "AI_GATEWAY_MODEL": "qwen-plus"})
check("读取 env 里的 key", cfg["api_key"] == "sk-x", str(cfg))
check("缺省 base_url 落到 Makers 网关", "ai-gateway.edgeone.link" in model_config({})["base_url"])
check("缺省模型是 makers 内置", model_config({})["model"].startswith("@makers/"))

print("[2] 计算器（AST 白名单）")
check("四则运算", _safe_eval("(1+2)*3-4/2") == 7)
check("幂运算", abs(_safe_eval("(1+0.08)**12") - 2.5181701168197786) < 1e-9)
check("内置函数与常量", abs(_safe_eval("sqrt(2)*pi") - 4.442882938158366) < 1e-9)
check("整除与取余", _safe_eval("17//5+17%5") == 5)
try:
    _safe_eval("__import__('os').system('dir')")
    check("拒绝任意代码", False, "竟然执行了")
except Exception:
    check("拒绝任意代码", True)

print("[3] 工具集")
tools = make_tools({"AI_GATEWAY_API_KEY": "sk-x"})
names = {t.name for t in tools}
check("五个工具齐了", names == {"web_search", "fetch_webpage", "calculator", "current_time", "generate_image"}, str(names))

async def _tool_results():
    calc = next(t for t in tools if t.name == "calculator")
    time_t = next(t for t in tools if t.name == "current_time")
    search = next(t for t in tools if t.name == "web_search")
    bad_search = next(t for t in tools if t.name == "web_search")
    return (
        await calc.ainvoke({"expression": "2**10"}),
        await time_t.ainvoke({}),
        await search.ainvoke({"query": "langgraph"}),
        await bad_search.ainvoke({"query": ""}),
    )

r_calc, r_time, r_search, r_bad = asyncio.run(_tool_results())
check("calculator 返回 1024", "1024" in r_calc and r_calc.startswith("[成功]"), r_calc)
check("current_time 带日期", r_time.startswith("[成功]") and "-" in r_time, r_time)
check("搜索失败也是字符串交回模型", r_bad.startswith("["), r_bad)
print(f"      （联网搜索实测返回前 60 字：{r_search[:60]}）")

print("[4] LangGraph 图")
from langchain_openai import ChatOpenAI  # noqa: E402

llm = ChatOpenAI(model="test", api_key="sk-x", base_url="https://example.invalid/v1")
graph = build_graph(llm, tools)
check("图编译成功且带 checkpointer", graph.checkpointer is not None)
conf = thread_config("conv-test")
check("thread_id 进了 config", conf["configurable"]["thread_id"] == "conv-test")

print("[5] SSE 格式")
check("sse 以 data: 开头双换行结尾", sse({"a": 1}).startswith("data: ") and sse({"a": 1}).endswith("\n\n"))

print()
if failed:
    print(f"共 {passed + len(failed)} 项，失败 {len(failed)} 项：{failed}")
    sys.exit(1)
print(f"全部通过：{passed} 项 ✅")
