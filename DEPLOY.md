# 部署到 EdgeOne Makers（腾讯云）操作手册

> 本文件只讲「这个项目怎么上线」，通用背景见教程原文。
> 标注说明：🤖 = 已由 AI 处理完毕，不用管；👤 = 必须你本人操作（要账号/要授权）；⚠️ = 注意事项。

---

## 一、项目是否已符合 Makers 平台规范（已实测）

| 规范要求 | 本项目状态 |
|---|---|
| 入口必须是 `agents/<路由>/index.py` 里的 `async def handler(context)` | 🤖 `agents/chat/index.py` ✅ 对应 `POST /chat` |
| 配置 `edgeone.json`（framework / dir / outputDirectory） | 🤖 已配置 `framework: langgraph`、`dir: agents`、`outputDirectory: public` |
| `agents/` 目录里不许用 `os.environ` 读配置 | 🤖 已检查，全部通过 `context.env` 读取 ✅ |
| 流式返回走 `context.utils.stream_sse` | 🤖 `agents/_sse.py` 已做优先使用平台能力、本地兜底 ✅ |
| 依赖必须写进 `requirements.txt` | 🤖 已核对 `agents/` 全部 import，只用到 `langgraph / langchain-core / langchain-openai / pydantic / httpx` ✅ |
| 线上密钥由平台自动注入，不用本地 `.env` | 🤖 代码在拿不到 key 时给友好提示，不会崩 ✅ |
| 数据落盘（`data/`）只在本地生效 | ⚠️ 线上是无状态运行，历史会话列表功能会降级（详见第七节） |

结论：**可以直接部署，不需要改代码。**

---

## 二、部署前准备（2 件事）

### 1. 👤 安装 Makers 技能包

你的环境 Node v22.22.2、Git 2.55 都已就绪，直接在项目目录开终端跑：

```bash
npx skills add TencentEdgeOne/edgeone-makers-tools
```

按提示选 **全局安装**，勾选 `makers-agents` 和 `makers-deploy` 两个技能。

### 2. 👤 拿两个密钥

| 变量 | 去哪拿 | 是否必需 |
|---|---|---|
| `AI_GATEWAY_API_KEY` | EdgeOne Makers 控制台 → 项目/账户设置里获取（形如 `sk-xxx`） | ✅ 必需 |
| `AI_GATEWAY_BASE_URL` | 固定值 `https://ai-gateway.edgeone.link/v1` | ✅ 必需 |
| `WSA_API_KEY` | 腾讯云控制台开通「Web Search」服务（选最便宜套餐） | 可选，不配则搜索走 360 兜底（效果差一些） |
| `DASHSCOPE_API_KEY` | 阿里云百炼控制台（你本地 `.env` 里已有） | 可选，不配则画图功能不可用 |

⚠️ 本地 `.env` 里的 DashScope Key 不要写进 Git 仓库（`.gitignore` 已排除 `.env`），部署时在控制台手动加环境变量。

---

## 三、部署（两种方式，任选其一）

### 方式 A：控制台导入 Git 仓库（推荐，后面改代码能自动重新部署）

👤 你需要做：

1. 把这个文件夹推到 GitHub/Gitee（新建空仓库 → `git init` → `git add .` → `git commit` → `git push`）
   - ⚠️ 推送前确认 `.env` 没被提交（`.gitignore` 已排除，可用 `git status` 核对）
2. 打开 EdgeOne Makers 控制台 → 创建项目 → **导入 Git 仓库** → 选刚推的仓库
3. 构建命令留空（`edgeone.json` 已写），输出目录 `public`，选加速区域
4. 在「环境变量」里加上表里的几个 key
5. 点创建部署，等 1~3 分钟拿到临时域名

### 方式 B：用 `/makers-deploy` 技能一句话部署（教程推荐）

👤 在 AI 编程工具里（装了技能包之后）发：

```
帮我部署上线这个项目：
AI_GATEWAY_BASE_URL 是 https://ai-gateway.edgeone.link/v1
AI_GATEWAY_API_KEY 是 sk-xxxxx（换成你自己的）
WSA_API_KEY 是 sk-xxxxx（有就填，没有可留空）
```

首次会要求登录授权，跟着提示走即可。

---

## 四、部署后验证（3 步）

1. 浏览器打开临时域名，页面能正常显示棱镜首屏
2. 输入「今天几号？离 2027 年元旦还有多少天？」→ 应看到工具轨迹里出现「时间查询」步骤，回答带天数
3. 输入「画一张赛博朋克城市夜景」→ 若配了 `DASHSCOPE_API_KEY` 会贴出真实图片；没配会明确说画图服务不可用（不会编假链接）

---

## 五、换模型（不改代码）

在 Makers 控制台把环境变量 `AI_GATEWAY_MODEL` 改成想要的模型 ID（如 `deepseek/deepseek-v4-pro`），重新部署即可。
代码里 `agents/_model.py` 优先读这个变量，缺省才用 `@makers/deepseek-v4-flash`。

---

## 六、绑定免费自定义域名（👤 纯手工，约 20 分钟 + 等 DNS 传播）

1. **注册 Cloudflare**：https://dash.cloudflare.com/sign-up
2. **注册 DigitalPlat FreeDomain**：https://dash.domain.digitalplat.org/auth/register（邮箱验证码验证）
3. **申请子域名**：登录 DigitalPlat → 选后缀（`.dpdns.org` / `.us.kg` / `.qzz.io` / `.xx.kg` / `.qd.je`）→ 填前缀（如 `omni`）→ 查重 → 提交
4. **把 NS 转进 Cloudflare**：Cloudflare 控制台 → 添加站点 → 输入 `omni.dpdns.org` → 选 Free 套餐 → 记下分配的两组 NS（形如 `xxx.ns.cloudflare.com`）
5. 回 DigitalPlat 后台 → 把原 NS 替换成这两组 → 保存
6. 等 Cloudflare 显示 **Active**（几分钟，最长 24 小时）
7. **加解析记录**：Cloudflare → DNS → 新增 **CNAME**，名称 `omni`，内容填 Makers 给你的临时域名，代理状态开橙色云朵
8. **在 Makers 绑域名**：控制台 → 你的项目 → 域名管理 → 添加自定义域名 → 填 `omni.dpdns.org` → 平台自动校验并签发 SSL
9. 验证：浏览器访问 `https://omni.dpdns.org` 能对话即成功

⚠️ Makers 默认临时域名只有 3 小时预览期，长期对外访问必须绑自定义域名。

---

## 七、线上与本地功能差异（重要，答辩时别说错）

| 功能 | 本地 `local_dev.py` | 线上 Makers |
|---|---|---|
| 单会话多轮记忆 | ✅ | ✅ 平台托管记忆 |
| 刷新页面恢复当前会话 | ✅ `/history` 接口 | ⚠️ 取决于平台是否提供会话读取接口，未验证 |
| 左侧历史会话列表 / 删除 | ✅ 落盘 `data/conversations.json` | ⚠️ 无本地存储，侧栏会提示「当前环境不支持历史会话列表」，不影响对话 |
| 联网搜索 | ✅ WSA / 360 兜底 | ✅ 配 `WSA_API_KEY` 更好 |
| 画图 | ✅ | ✅ 需 `DASHSCOPE_API_KEY` |
| 工具轨迹 / 流式输出 | ✅ | ✅ |

如果线上也需要历史会话列表，需要把会话存储换成平台提供的持久化能力（如控制台开通数据库），属于二次开发，不在本次范围。

---

## 八、常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 部署失败、找不到入口 | 没先装/调用 `makers-agents` 技能，或 `edgeone.json` 被改动 |
| 部署成功但对话报错 | 没配 `AI_GATEWAY_API_KEY` / `AI_GATEWAY_BASE_URL` |
| 搜索总是返回兜底结果 | 没配 `WSA_API_KEY` |
| 画图提示未配置密钥 | 没配 `DASHSCOPE_API_KEY` |
| 想改答案风格 | 改 `agents/_prompt.py` 后重新部署 |
| 免费域名不生效 | DigitalPlat 后台 NS 是否已改成 Cloudflare 的两组、是否 Active |
| 线上侧栏空白 | 预期行为，见第七节 |

---

## 九、我自己能替你做的 / 你必须自己做的

🤖 已做：项目规范核查、依赖核对、`.gitignore`、部署文档、线上降级处理、本地全链路测试通过。

👤 只能你做：
1. 跑 `npx skills add` 装技能包（交互式命令，需你选安装范围）
2. 登录 Makers 控制台拿 `AI_GATEWAY_API_KEY`、点部署、完成授权
3. 注册 Cloudflare / DigitalPlat、申请域名、改 NS、加解析
4. 决定 `.env` 里的真实 Key 是否随作业提交

⚠️ 我无法验证的部分：Makers 线上 `context.store` 是否支持列出会话（影响历史侧栏）、线上自定义域名是否需备案。
