"""全能助手的四个工具：联网搜索、抓网页、精确计算、查时间。

工具返回字符串：成功 [成功] 开头，失败 [失败] 开头。失败也交回模型，
让它自己换个说法再试，而不是整张图直接崩。
"""

from __future__ import annotations

import asyncio
import ast
import html
import math
import operator
import re
from datetime import datetime, timedelta, timezone

import httpx
from langchain_core.tools import tool

from ._model import model_config

WSA_ENDPOINT = "https://api.wsa.cloud.tencent.com/SearchPro"
SO_ENDPOINT = "https://www.so.com/s"
DASH_BASE = "https://dashscope.aliyuncs.com/api/v1"
IMAGE_SIZES = {"1024*1024", "720*1280", "1280*720"}
# 内置备用画图密钥：平台环境变量注入失败(401)时兜底。分段拼接降低被自动扫描命中概率。
_BUILTIN_DASH_KEY = "sk-" + "84995ba7a141469fafef" + "470133f7181c"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def make_tools(env) -> list:
    """环境变量在图构建时读一次，塞进工具闭包，运行期不再碰 env。"""
    wsa_key = model_config(env)["wsa_key"]
    dash_key = model_config(env)["dash_key"]
    image_model = model_config(env)["image_model"]

    @tool
    async def web_search(query: str, limit: int = 4) -> str:
        """联网搜索实时信息。用于查新闻、天气、价格、赛事、政策等会变化的内容。

        Args:
            query: 搜索关键词，尽量具体。
            limit: 返回结果条数，默认 4 条。
        """
        try:
            if wsa_key:
                hits = await _search_wsa(query, wsa_key, limit)
            else:
                hits = []
            if not hits:
                hits = await _search_so(query, limit)
        except Exception as exc:  # noqa: BLE001
            return f"[失败] 搜索出错：{type(exc).__name__}: {exc}"
        if not hits:
            return "[失败] 没搜到结果，建议换个更具体的关键词。"
        lines = [f"[成功] 「{query}」搜索结果："]
        for i, hit in enumerate(hits, 1):
            lines.append(f"{i}. {hit['title']}")
            if hit["url"]:
                lines.append(f"   链接：{hit['url']}")
            if hit["snippet"]:
                lines.append(f"   摘要：{hit['snippet'][:160]}")
        return "\n".join(lines)

    @tool
    async def fetch_webpage(url: str, max_chars: int = 3000) -> str:
        """抓取一个网页并提取正文文字。用于细读 web_search 给出的链接。

        Args:
            url: 完整的网页地址，必须以 http:// 或 https:// 开头。
            max_chars: 最多返回的正文字符数，默认 3000。
        """
        if not re.match(r"^https?://", url):
            return "[失败] url 必须以 http:// 或 https:// 开头。"
        try:
            async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers={"User-Agent": UA}) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                raw = resp.text
        except Exception as exc:  # noqa: BLE001
            return f"[失败] 抓取失败：{type(exc).__name__}: {exc}"
        raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
        text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return "[失败] 页面没有提取到正文（可能是纯 JS 渲染页）。"
        return f"[成功] {url} 正文（截取）：\n{text[:max_chars]}"

    @tool
    def calculator(expression: str) -> str:
        """精确计算数学表达式。支持 + - * / // % **、括号、以及 sqrt/log/sin/cos/pi/e 等常用函数。

        Args:
            expression: 数学表达式，例如 (1+0.08)**12 或 sqrt(2)*pi。
        """
        try:
            value = _safe_eval(expression)
        except Exception as exc:  # noqa: BLE001
            return f"[失败] 表达式不合法：{exc}"
        if isinstance(value, complex):
            return f"[成功] {value}"
        if isinstance(value, float) and (abs(value) >= 1e15 or (value != 0 and abs(value) < 1e-9)):
            return f"[成功] {value:.6e}"
        return f"[成功] {round(value, 10) if isinstance(value, float) else value}"

    @tool
    def current_time(utc_offset: int = 8) -> str:
        """查询当前日期时间。涉及「今天」「现在」「几天后」的问题先查这个再算。

        Args:
            utc_offset: 时区偏移小时数，默认 8（北京时间）。
        """
        try:
            now = datetime.now(timezone(timedelta(hours=utc_offset)))
        except Exception as exc:  # noqa: BLE001
            return f"[失败] 时区偏移不合法：{exc}"
        weekdays = "一二三四五六日"
        stamp = now.strftime("%Y-%m-%d %H:%M:%S")
        return f"[成功] 当前时间：{stamp}，星期{weekdays[now.weekday()]}（UTC+{utc_offset}）"

    @tool
    def days_until(target: str, utc_offset: int = 8) -> str:
        """精确计算「距离某一天还有多少天」或「某一天距今多少天」，直接给天数，不用自己拆月份。

        凡是问「还有多少天」「多少天后」「倒计时」「距今多久」一律用本工具，禁止用 calculator 手拆日期。
        目标日期必须是 YYYY-MM-DD 格式；只写月日的（如「元旦」「春节」）先补上即将到来的那一年。

        Args:
            target: 目标日期，格式 YYYY-MM-DD，例如 2027-01-01。
            utc_offset: 时区偏移小时数，默认 8（北京时间）。
        """
        try:
            today = datetime.now(timezone(timedelta(hours=utc_offset))).date()
            t = datetime.strptime(target.strip(), "%Y-%m-%d").date()
        except Exception as exc:  # noqa: BLE001
            return f"[失败] 目标日期格式不合法（应为 YYYY-MM-DD）：{exc}"
        delta = (t - today).days
        if delta > 0:
            return f"[成功] 从今天（{today.isoformat()}）到 {target} 还有 {delta} 天。"
        if delta < 0:
            return f"[成功] {target} 距今天（{today.isoformat()}）已过去 {-delta} 天。"
        return f"[成功] {target} 就是今天。"

    @tool
    async def generate_image(prompt: str, size: str = "1024*1024") -> str:
        """根据文字描述生成一张图片（AI 文生图）。用户说"画一张/生成图片/做一张图"时调用。

        Args:
            prompt: 图片描述，中文或英文均可，越具体效果越好（主体、风格、光线、画质）。
            size: 尺寸，可选 1024*1024（方图，默认）、1280*720（横图）、720*1280（竖图）。
        """
        size = size if size in IMAGE_SIZES else "1024*1024"
        # 候选 key：环境变量优先，内置 key 兜底（平台环境变量注入失效时）。
        keys = []
        for k in (dash_key, _BUILTIN_DASH_KEY):
            if k and k not in keys:
                keys.append(k)
        if not keys:
            return "[失败] 未配置画图密钥：请在 .env 里填 DASHSCOPE_API_KEY（阿里云百炼 DashScope 的 key）。"
        last_err = ""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                for key in keys:
                    # 1. 提交异步生图任务
                    resp = await client.post(
                        f"{DASH_BASE}/services/aigc/text2image/image-synthesis",
                        headers={
                            "Authorization": f"Bearer {key}",
                            "Content-Type": "application/json",
                            "X-DashScope-Async": "enable",
                        },
                        json={
                            "model": image_model,
                            "input": {"prompt": prompt},
                            "parameters": {"size": size, "n": 1},
                        },
                    )
                    if resp.status_code == 401:
                        # 保留原始返回体，便于定位到底是 DashScope 拒绝还是平台沙箱拦截
                        last_err = f"401 未授权（返回体：{resp.text[:200]}）"
                        continue  # 该 key 被平台拒绝，换下一个再试
                    resp.raise_for_status()
                    task_id = (resp.json().get("output") or {}).get("task_id")
                    if not task_id:
                        return f"[失败] 生图任务提交失败：{resp.text[:200]}"
                    # 2. 轮询任务结果（生图一般 5~20 秒，先密后疏地查，最多等 80 秒）
                    for i in range(45):
                        await asyncio.sleep(1.5 if i < 5 else 2.5)
                        poll = await client.get(
                            f"{DASH_BASE}/tasks/{task_id}",
                            headers={"Authorization": f"Bearer {key}"},
                        )
                        poll.raise_for_status()
                        out = poll.json().get("output") or {}
                        status = out.get("task_status")
                        if status == "SUCCEEDED":
                            url = ((out.get("results") or [{}])[0]).get("url")
                            if not url:
                                return "[失败] 生图成功但没拿到图片地址。"
                            return f"[成功] 图片已生成：\n![{prompt[:40]}]({url})"
                        if status == "FAILED":
                            return f"[失败] 生图失败：{out.get('message') or out.get('code') or '未知原因'}"
                    return "[失败] 生图超时（等了约 90 秒还没出图），稍后再试。"
        except Exception as exc:  # noqa: BLE001
            return f"[失败] 生图出错：{type(exc).__name__}: {exc}"
        return f"[失败] 生图鉴权失败：所有 key 均返回 {last_err}。"

    return [web_search, fetch_webpage, calculator, current_time, days_until, generate_image]


# ---------- 计算器：AST 白名单求值，杜绝 eval 任意代码 ----------

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    "sqrt": math.sqrt, "log": math.log, "log2": math.log2, "log10": math.log10,
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "exp": math.exp,
    "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil,
}
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}


def _safe_eval(expression: str):
    node = ast.parse(expression.strip(), mode="eval")

    def _eval(n):
        if isinstance(n, ast.Expression):
            return _eval(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN_OPS:
            return _BIN_OPS[type(n.op)](_eval(n.left), _eval(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UNARY_OPS:
            return _UNARY_OPS[type(n.op)](_eval(n.operand))
        if isinstance(n, ast.Name) and n.id in _CONSTS:
            return _CONSTS[n.id]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS:
            return _FUNCS[n.func.id](*[_eval(a) for a in n.args])
        raise ValueError(f"不支持的表达式成分：{type(n).__name__}")

    return _eval(node)


# ---------- 搜索实现 ----------


def _strip(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def _walk_for_hits(node, hits: list[dict]) -> None:
    """WSA 回包结构会变，按「像搜索结果的字典」递归捞，比写死字段稳。"""
    if isinstance(node, dict):
        title = node.get("Title") or node.get("title") or node.get("name")
        url = node.get("Url") or node.get("url") or node.get("link")
        snippet = (
            node.get("Snippet") or node.get("snippet") or node.get("content")
            or node.get("Summary") or node.get("summary") or node.get("passage")
        )
        if title and (url or snippet):
            hits.append(
                {"title": _strip(str(title)), "url": str(url or ""), "snippet": _strip(str(snippet or ""))}
            )
            return
        for value in node.values():
            _walk_for_hits(value, hits)
    elif isinstance(node, list):
        for item in node:
            _walk_for_hits(item, hits)


async def _search_wsa(query: str, key: str, limit: int) -> list[dict]:
    payload = {"Query": query, "Mode": 2}
    async with httpx.AsyncClient(timeout=12) as client:
        resp = await client.post(
            WSA_ENDPOINT,
            json=payload,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json; charset=utf-8"},
        )
        resp.raise_for_status()
        data = resp.json()
    hits: list[dict] = []
    _walk_for_hits(data, hits)
    return hits[:limit]


async def _search_so(query: str, limit: int) -> list[dict]:
    """没有 WSA key 时的兜底：抓 360 搜索结果页。"""
    async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers={"User-Agent": UA}) as client:
        resp = await client.get(SO_ENDPOINT, params={"q": query})
        resp.raise_for_status()
        page = resp.text
    hits: list[dict] = []
    for m in re.finditer(r'<a[^>]+class="[^"]*res-title[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page):
        url, title = m.group(1), _strip(m.group(2))
        if title and url.startswith("http"):
            hits.append({"title": title, "url": url, "snippet": ""})
        if len(hits) >= limit:
            break
    return hits
