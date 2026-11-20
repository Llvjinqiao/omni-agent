"""模型工厂。

规范要求：agents/ 目录里禁止 os.environ，环境变量只能从 context.env 读。
本地调试时由 local_dev.py 用 .env 造一个同样的 env 字典，
所以这份代码本地和线上是同一条路径。
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI

DEFAULT_BASE_URL = "https://ai-gateway.edgeone.link/v1"
DEFAULT_MODEL = "@makers/deepseek-v4-flash"


def _read(env, key: str, default: str = "") -> str:
    if env is None:
        return default
    value = None
    if hasattr(env, "get"):
        try:
            value = env.get(key)
        except Exception:  # noqa: BLE001 - 平台 env 可能只支持属性访问
            value = None
    if value is None:
        value = getattr(env, key, None)
    return str(value).strip() if value else default


def model_config(env) -> dict:
    base_url = _read(env, "AI_GATEWAY_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL
    api_key = _read(env, "AI_GATEWAY_API_KEY")
    # 画图走 DashScope：优先专用 key，没配且网关本身就是 dashscope 时复用对话 key
    dash_key = _read(env, "DASHSCOPE_API_KEY") or (api_key if "dashscope" in base_url else "")
    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": _read(env, "AI_GATEWAY_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL,
        "wsa_key": _read(env, "WSA_API_KEY"),
        "dash_key": dash_key,
        "image_model": _read(env, "IMAGE_MODEL", "wanx2.1-t2i-turbo") or "wanx2.1-t2i-turbo",
    }


def build_llm(env, *, temperature: float = 0.7) -> ChatOpenAI:
    cfg = model_config(env)
    if not cfg["api_key"]:
        raise RuntimeError(
            "没有拿到模型密钥：AI_GATEWAY_API_KEY 为空。\n"
            "线上由 Makers 自动注入；本地请把 .env 里的 AI_GATEWAY_API_KEY 填上。"
        )
    return ChatOpenAI(
        model=cfg["model"],
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        temperature=temperature,
        timeout=60,
        max_retries=1,
        streaming=True,
        # 限制单次回复长度：配合提示词「结论先行、控制篇幅」，避免模型长篇大论拖时间。
        max_tokens=1500,
    )
