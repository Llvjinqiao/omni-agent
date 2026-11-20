# OMNI · 全能智能体 Agent

> OMNI（拉丁语 omnis，"全部、一切"）—— 一个入口，分派所有能力。
> 视觉概念取自**棱镜**：一束白光穿过棱镜分成光谱，表示"一个问题进来，分成搜索/计算/对比/时间/图像"。

基于 **LangGraph** 的多能力通用 Agent，按 EdgeOne Makers 平台规范编写，
本地和线上同一份代码。框架选型参考：[LangGraph 快速上手](https://www.runoob.com/ai-agent/langgraph-quick-start.html)。

## 它能干什么

| 能力 | 工具 | 说明 |
|---|---|---|
| 联网搜索 | `web_search` | 腾讯云 Web Search API（WSA_API_KEY），没 key 自动退回 360 搜索兜底 |
| 读网页 | `fetch_webpage` | 抓正文、去脚本样式，可细读搜索结果 |
| 精确计算 | `calculator` | AST 白名单求值（拒绝任意代码执行），支持 sqrt/log/sin/pi 等 |
| 查时间 | `current_time` | 任意时区当前时间，配合计算器回答「还有几天」类问题 |
| AI 画图 | `generate_image` | 通义万相文生图（DASHSCOPE_API_KEY），出图直接贴进对话；模型编假链接会被护栏拦截改真图 |
| 多轮记忆 | LangGraph `MemorySaver` | 按 conversation_id（thread_id）存每个会话的上下文 |

## 特色功能

- **历史会话侧边栏**：多段对话可切换、可删除（悬停出现 ×），会话落盘到 `data/conversations.json`，重启服务不丢
- **记忆自动恢复**：服务重启后切回旧会话，历史自动灌回 LangGraph 线程，模型不失忆
- **双通道流式**：token 级增量逐字上屏 + 工具调用轨迹实时可见
- **速度优化**：图缓存复用 LLM 连接、历史瘦身（旧工具结果截断）、简单问题不走工具直接答

## LangGraph 架构（agents/_graph.py）

```
START → agent ─(有 tool_calls)→ tools ─┐
            ↑──────────────────────────┘
            └─(没有 tool_calls)→ END
```

- `StateGraph` + `add_messages`：消息只追加不覆盖
- `ToolNode` + `tools_condition`：预置工具节点与路由
- `MemorySaver` checkpointer：多轮对话记忆

## 目录结构

```
Agent/
├── agents/
│   ├── chat/index.py    # 平台入口：async def handler(context)，SSE 流式返回
│   ├── _graph.py        # LangGraph 状态图（ReAct 循环 + 历史瘦身）
│   ├── _model.py        # 模型工厂（只从 context.env 读配置）
│   ├── _prompt.py       # 系统提示词
│   ├── _tools.py        # 五个工具
│   └── _sse.py          # SSE 编码
├── public/index.html    # OMNI 前端（棱镜视觉 + 历史侧栏，实时显示工具轨迹）
├── local_dev.py         # 本地调试服务器（模拟平台 context，会话落盘）
├── edgeone.json         # Makers 平台配置（framework: langgraph）
├── DEPLOY.md            # 腾讯云部署操作手册（含必须你本人操作的步骤）
├── tests/test_agent.py  # 不依赖网络的冒烟测试
├── requirements.txt
└── .env.example
```

## 本地跑起来

```bash
# 1. 装依赖（线上平台不需要 fastapi/uvicorn，故拆在 dev 里）
pip install -r requirements.txt -r requirements-dev.txt

# 2. 配 .env（参考 .env.example，填 AI_GATEWAY_API_KEY）

# 3. 冒烟测试
python tests/test_agent.py

# 4. 起服务
python local_dev.py        # http://127.0.0.1:8000
```

## 部署到 EdgeOne Makers

项目已按平台规范组织（agents 目录 + edgeone.json + handler 入口），
用 `/makers-deploy` 技能或 Makers 控制台导入 Git 仓库即可上线；
线上 `AI_GATEWAY_API_KEY` / `AI_GATEWAY_BASE_URL` 由平台自动注入。
换模型不用改代码：设 `AI_GATEWAY_MODEL` 环境变量后重新部署即可。
拿到临时域名后，可按 FreeDomain + Cloudflare 流程绑定自定义域名。
