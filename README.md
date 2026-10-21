# Agent Orchestra

**一个持久化、可审计的编码 agent 编排内核，配一个原生的 iPhone 控制台。**

一句话需求进去，五个阶段出来：**规划 → 实现 → 独立审查 → 测试 → 汇总**。
任务落在 SQLite 里，进程被杀也能接着跑；每一步留下可核对的证据；审批绑定到具体操作摘要。
实现方与审查方必须是不同端点——这不是提示词里的建议，是内核里的校验。

`Python 3.12+` · `零运行时依赖` · `SwiftUI 客户端` · `Apache-2.0` · `仅回环地址`

---

## 不是又一个 agent 脚本

大多数 agent 工具是一个循环加一个终端。这里有三件事不一样：

| | 常见做法 | 这里 |
|---|---|---|
| 中断 | 进程结束，状态归零 | 任务、阶段、产物、事件全部落库，重启只会重放未完成的阶段 |
| 自审 | 同一个模型检查自己 | 实现端与审查端必须是不同端点，审查钉在一个提交 SHA 上 |
| 审批 | 「允许这个 agent」 | 「允许这一次操作」——绑定操作摘要与版本，版本变了审批自动失效 |

还有一条容易被忽略的：**成本未知就写未知**。按量计费之外的费用拿不到准数时显示 `unknown`，
不显示 `$0`——把未知伪装成零，是审计里最坏的一种谎。

## 为什么需要它

单个 agent 跑完整条流水线，有三个绕不过去的问题：

**1. 自己审自己不可靠。** 让同一个模型检查自己的输出，它倾向于自我确认。
这里把实现和审查强制拆给**不同厂商**，审查针对一个固定的提交 SHA，不是一份会被改动的草稿。

**2. 会话一旦中断，一切归零。** 多数工具 Ctrl-C 就丢状态。这里任务和事件都落在 SQLite，
进程重启后按显式恢复继续，而不是静默重放。

**3. 成本不透明。** 按量计费之外的费用往往不可知。这里宁可显示「未知」，也不写 `$0`。

## 核心特性

| 特性 | 说明 | 状态 |
|---|---|---|
| 持久状态机 | SQLite 存任务与事件，租约超时、断点恢复、陈旧写入防护 | ✅ |
| 固定五阶段 | `plan → implement → review → test → summarize`，每阶段留证据 | ✅ |
| 跨端点独立审查 | 实现端与审查端必须是不同端点，指向同一个会直接拒绝启动 | ✅ |
| 隔离工作树 | 每个任务独立工作树，审查固定提交 SHA，集成前不碰你的分支 | ✅ |
| 审批门 | 审批绑定操作摘要（digest）+ revision；revision 变了审批自动失效 | ✅ |
| 版本化设置 | 配置有 revision 与审计记录，新任务保存不可变快照 | ✅ |
| 成本诚实 | 未知成本记为 `null`，不伪装成 0 | ✅ |
| 幂等提交 | 同一 `request_id` 重复提交不会创建新任务 | ✅ |
| Mock 模式 | 9 种场景（审批/限流/额度/超时/失败…），零成本、零网络 | ✅ |
| 隔离执行测试 | 固定夹具在 `-I -S` 解释器里执行，不导入工作区、不读站点包 | ✅ |
| 可插拔适配器 | 适配器注册表；本仓库只带 Mock 实现 | 🚧 真实适配器待接入 |
| iPhone 控制台 | 原生 SwiftUI：提交、历史、审批、折叠详情 | ✅ iOS 17+ |
| 代码库搜索 | ripgrep + 符号索引，按 token 预算裁剪结果 | 🚧 计划中 |
| Linux 沙箱 | `bwrap` / `landlock` 后端 | 🚧 计划中 |
| 库结构迁移 | SQLite `user_version` 前向迁移 | 🚧 计划中 |

> **当前阶段**：内核已可运行，但只带 Mock 适配器——端到端流程能跑通，不会调用任何厂商。
> 标 🚧 的能力尚未可用。完整设计见 [docs/DESIGN.md](docs/DESIGN.md)。

## 架构

```
       ┌──────────────┐        ┌──────────────┐
       │  原生客户端    │        │  curl / CLI  │
       └──────┬───────┘        └──────┬───────┘
              └─────────┬─────────────┘
                 HTTP · 127.0.0.1:8765
                        │
              ┌─────────▼──────────┐
              │    Controller      │  状态机 · 审批 · 租约 · 恢复
              │  （唯一写入方）      │  模型无权授权任何动作
              └────┬──────────┬────┘
                   │          │
        ┌──────────▼──┐   ┌───▼─────────────┐
        │  Adapters   │   │    Workspace    │
        │ 各厂商模型    │   │ 隔离工作树        │
        └─────────────┘   │ 固定 SHA · 沙箱  │
                          └────────┬────────┘
                                   │
                          ┌────────▼────────┐
                          │ SQLite + 事件日志 │
                          └─────────────────┘
```

四条硬规则：

1. **控制面不信任模型。** 模型只返回结构化数据；落盘、提交、跑测试由控制器做。
2. **失败即停。** 额度耗尽、未登录、被限流各自保留状态，不自动换账号、换厂商或切计费。
3. **每步可追。** 固定提交 SHA、事件序号、模型返回的用量，全部入库。
4. **审批绑定具体操作。** 不是「允许这个 agent」，而是「允许这一次操作摘要」。

## iPhone 控制台

`ios/` 是一个原生 SwiftUI 客户端（iOS 17+，无第三方依赖），把服务端能力完整搬到手机上：

- 聊天式提交、历史列表、阶段详情可折叠
- 审批卡显示操作原文、完整摘要与版本，按钮提交的就是卡片上那一段
- 断线每三秒自动重连；切到后台只停止轮询，服务端任务照常推进
- 提交前把请求写进 `UserDefaults`，丢响应后可以重试同一个请求，不会重复建任务

```sh
xcodebuild -project ios/Orchestragent.xcodeproj -scheme Orchestragent \
  -sdk iphonesimulator -derivedDataPath .build/ios CODE_SIGNING_ALLOWED=NO build
```

详见 [ios/README.md](ios/README.md)。

## 快速开始

前置：Python 3.12+、[uv](https://docs.astral.sh/uv/)、Git。

```sh
cd agent-orchestra
uv sync --locked --group dev
PYTHONPATH=src uv run python -m orchestragent.server
```

启动后监听 `127.0.0.1:8765`，默认 **Mock 模式**：不联网、不调用任何厂商、费用为零。
数据写在 `.data/`（已被 git 忽略）。

```sh
curl -s http://127.0.0.1:8765/health
# {"mode": "mock", "version": 2}
```

## 接入真实端点

不配端点时全程 Mock。配两个端点后，规划与实现走一个、审查走另一个——**两个都必须给，
少一个直接拒绝启动**，所以不存在「同一个端点审自己的代码」这种配置。

```sh
export IMPL_KEY=...      # 只从环境变量读取，不进数据库、事件或日志
export REVIEW_KEY=...

PYTHONPATH=src uv run python -m orchestragent.server \
  --implement-endpoint https://impl.example/v1 --implement-model model-a --implement-key-env IMPL_KEY \
  --review-endpoint    https://rev.example/v1  --review-model  model-b --review-key-env REVIEW_KEY
```

端点需要提供 `POST /chat/completions`，返回体的 `choices[0].message.content` 是一段 JSON。
这段 JSON 必须满足该阶段的结构（`schema.py` 逐字段校验），不合格就判失败，
不会带着脏数据继续下一步。检查 `GET /providers` 可以看到注册了哪些端点。

## 使用

### 提交任务

```sh
curl -s -X POST http://127.0.0.1:8765/tasks \
  -H 'Content-Type: application/json' \
  -d '{"request_id": "demo-001",
       "prompt": "写一个 Python 函数 clamp(x, lo, hi)，附 unittest 与 README。"}'
```

`request_id` 是幂等键：同一条只创建一次任务，重复提交返回已有任务（HTTP 200 而非 201）。
`prompt` 上限 8000 字符。

### 查看进度与事件

```sh
curl -s http://127.0.0.1:8765/tasks                      # 全部任务
curl -s http://127.0.0.1:8765/tasks/<task_id>            # 单个任务，含阶段与产物
curl -s 'http://127.0.0.1:8765/tasks/<task_id>/events?after=0'
```

### 跑测试

```sh
uv run pytest -q
uv run ruff check .
uv run mypy
```

事件是追加写、带递增序号，可以用 `after` 做增量拉取：

```json
{"events": [{"seq": 1, "kind": "submitted", ...}], "cursor": 1}
```

### 批准一次操作

任务进入 `waiting_approval` 时，`approval` 里会带上 `operation`（人类可读的操作描述）和
`operation_digest`（该操作的摘要）。批准必须回传**同一个** digest：

```sh
curl -s -X POST http://127.0.0.1:8765/tasks/<task_id>/approvals/<approval_id> \
  -H 'Content-Type: application/json' \
  -d '{"decision": "approve", "operation_digest": "<operation_digest>"}'
```

`decision` 取 `approve` 或 `deny`。digest 不匹配返回 409；`revision` 期间变化也会 409 ——
你不能批准一个和当初展示时不一样的改动。

### 暂停 / 恢复 / 取消

```sh
curl -s -X POST http://127.0.0.1:8765/tasks/<task_id>/actions \
  -H 'Content-Type: application/json' \
  -d '{"action": "pause"}'    # pause | resume | cancel
```

可恢复状态：`paused`、`rate_limited`、`quota_exhausted`、`auth_required`、`timed_out`。
终态（`completed` / `failed` / `cancelled`）不可再操作。

### 设置

```sh
curl -s http://127.0.0.1:8765/settings                          # 当前配置 + revision
curl -s -X POST http://127.0.0.1:8765/settings \
  -H 'Content-Type: application/json' \
  -d '{"base_revision": 3, "policy": { ... }}'
```

写入必须带 `base_revision`，版本不匹配会拒绝——避免并发覆盖。
「用一句话改设置」走提案接口，先看后应用：

```sh
curl -s -X POST http://127.0.0.1:8765/settings/proposals \
  -H 'Content-Type: application/json' \
  -d '{"instruction": "实现用 A 厂商，审查用 B 厂商"}'
curl -s -X POST http://127.0.0.1:8765/settings/proposals/<proposal_id>/apply
```

### 接口一览

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/health` | 模式与版本 |
| GET | `/providers` | 各厂商就绪状态 |
| GET | `/settings` | 读取配置 |
| POST | `/settings` | 更新配置（需 `base_revision`） |
| POST | `/settings/proposals` | 自然语言生成配置草案 |
| POST | `/settings/proposals/{id}/apply` | 应用草案 |
| GET | `/tasks` | 任务列表 |
| POST | `/tasks` | 提交任务 |
| GET | `/tasks/{id}` | 任务详情 |
| GET | `/tasks/{id}/events?after=N` | 增量事件 |
| POST | `/tasks/{id}/actions` | 暂停 / 恢复 / 取消 |
| POST | `/tasks/{id}/approvals/{approval_id}` | 批准或拒绝 |

请求体限 1–16384 字节，必须是 `application/json`。

## 配置

| 参数 | 默认 | 说明 |
|---|---|---|
| `--port` | `8765` | 监听端口 |
| `--data-dir` | `.data` | 数据库与工作树位置 |

服务只允许从本机回环访问：校验 `Host` 必须为 `127.0.0.1` 或 `localhost`，
并拒绝带 `Origin` 头的请求（防浏览器跨站调用）。**不要暴露到公网。**

## 它做什么

把一句话需求拆成五个阶段，让**不同厂商的模型互相校验**：规划由一个端点出，
实现交给另一个，独立审查交给第三个，最后由本地跑测试并汇总。

独立审查是这个项目的重点。同一个模型审查自己的输出，存在系统性自我确认偏差；
换一个厂商的模型复核，才能带来真正独立的判断。代价是每多一个端点就多一份开销——
所以只有当「复核」变得足够便宜时，这件事才做得起来。如果你已经配置了多个端点，
多跑一遍几乎不增加成本；如果只有一个，这套编排会退化成自审，也就失去了意义。

这也是内核强制要求实现端与审查端必须不同、而不允许配成同一个的原因。

## 怎么接

适配器是可插拔的，同一套契约支持两种后端：

- **本机 CLI** —— 你机器上已安装、已登录的命令行工具，配一个调用模板。
  我们按阶段调用它、读它的结构化输出，**不读取、不复制、不保存它的登录态**。
- **HTTP 端点** —— 任何兼容接口，配 `base_url` + 密钥 + 模型名。

**本仓库不包含任何厂商绑定实现**，也不内置任何登录流程。要接什么端点，
由你配置的那一条命令或那一个 `base_url` 决定。

## 边界

- 凭据始终留在原工具或你的配置里，本项目的数据库、事件日志和错误信息中不会出现。
- 仓库内的代码不含厂商实现，因此不针对任何厂商做条款判断；你自行接入某家服务时，
  其条款需要你自己确认。
- 不做跨用户共享、账号池化或转售——每个用户只用自己配置的端点。

## 明确不做

边界写在这里，是为了避免误解，也为了避免功能蔓延：

- **不做公网部署。** 单用户 + 回环是安全模型的基础。
- **不做动态 DAG。** 固定五阶段是可审计性的来源。
- **不做失败兜底。** 不因额度、登录或超时自动切换账号或计费方式。

## 路线图

已完成：

| 波次 | 内容 |
|---|---|
| W0 | 仓库骨架、许可、文档 |
| W1 | 内核去厂商化：适配器注册表，核心中不出现任何厂商名 |

待做：

| 波次 | 内容 | 目标 |
|---|---|---|
| W2 | `Adapter` 协议改为数据导向（`data` + JSON Schema） | 新厂商接入无需适配私有形状 |
| W3 | CLI 与 HTTP 两种后端实现 | 端到端串起多个端点 |
| W4 | 库结构迁移、服务端 token | 可安全升级 |
| W5 | Linux 沙箱后端 | 跨平台 |
| W6 | 代码库搜索 | 规划阶段按 token 预算检索 |

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。提交前请确保 `pytest`、`ruff`、`mypy` 全绿，
并运行 `sh scripts/check_leaks.sh`。
安全问题请走 [SECURITY.md](SECURITY.md) 里的私密渠道，不要开公开 issue。

## 许可

[Apache-2.0](LICENSE)。
