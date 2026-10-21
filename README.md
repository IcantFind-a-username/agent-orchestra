# Agent Orchestra

一个本地持久化、可审计的多模型编排内核：把你已经付费的多个订阅接成调度池，把一句话需求拆成
**规划 → 实现 → 独立审查 → 测试 → 汇总**，让不同厂商的模型互相校验，
任务重启不丢、费用不虚报。

`Python 3.12+` · `零运行时依赖` · `Apache-2.0` · `仅回环地址`

---

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
| 跨厂商独立审查 | 实现端与审查端必须是不同厂商，否则规划直接被拒 | ✅ |
| 隔离工作树 | 每个任务独立工作树，审查固定提交 SHA，集成前不碰你的分支 | ✅ |
| 审批门 | 审批绑定操作摘要（digest）+ revision；revision 变了审批自动失效 | ✅ |
| 版本化设置 | 配置有 revision 与审计记录，新任务保存不可变快照 | ✅ |
| 成本诚实 | 未知成本记为 `null`，不伪装成 0 | ✅ |
| 幂等提交 | 同一 `request_id` 重复提交不会创建新任务 | ✅ |
| Mock 模式 | 9 种场景（审批/限流/额度/超时/失败…），零成本、零网络 | ✅ |
| 沙箱测试 | 集成后在被限制的环境里跑 unittest | ✅ macOS |
| 多订阅调度 | 把各家已登录的 CLI 串成调度池，按阶段分派与汇总 | 🚧 进行中 |
| 代码库搜索 | ripgrep + 符号索引，按 token 预算裁剪结果 | 🚧 计划中 |
| Linux 沙箱 | `bwrap` / `landlock` 后端 | 🚧 计划中 |
| 库结构迁移 | SQLite `user_version` 前向迁移 | 🚧 计划中 |

> **当前阶段**：文档与骨架先行，`src/` 在迁移波次 W1 落地。标 🚧 的能力尚未可用，
> 请不要按已完成的状态依赖它们。完整设计见 [docs/DESIGN.md](docs/DESIGN.md)。

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

## 它到底做什么

把你**自己已经付费的多个订阅**接成一个调度池：同一句话需求，规划由一个订阅出，
实现交给另一个，独立审查交给第三个，最后由本地跑测试并汇总。你不需要为每个环节
再单独买一份 API 额度——订阅是已经付过的钱，这里只是把它用满。

跨厂商独立审查之所以成立，正是因为你有多个订阅：单从成本看，很少有人愿意为了
「让另一个模型复核」多付一份 API 账单；但如果你手上本来就有几家订阅，
这份复核的边际成本接近零。**聚合订阅不只是为了省，是为了让「互相校验」变得可负担。**

## 怎么接

两种后端，同一套适配器契约：

- **本机 CLI**（主要形态）—— 你机器上已经安装、已经登录好的命令行工具。我们按阶段调用它，
  读它的结构化输出，**不读取、不复制、不保存它的登录态**。
- **HTTP 端点** —— 任何兼容接口，配 `base_url` + 密钥 + 模型名。

凭据始终留在原工具自己的存储里，本项目的数据库、事件日志和错误信息里都不会出现它。

## 你需要知道的

各厂商对「订阅用于非交互式自动化」的口径不同，有的明确禁止。用官方 CLI 驱动、
不自建登录流程、不提取令牌，是这类接法里风险最低的形式，但不等于被允许。
实际后果通常落在账号层面（限流、降级、封禁），一般不会追到项目本身。

自用时这是你和厂商之间的事。**公开分发会把同样的风险复制到每个使用者身上**，
也让项目本身更容易被注意到。这个取舍值得在发布前想一次，而不是发布后。

## 明确不做

边界写在这里，是为了避免误解，也为了避免功能蔓延：

- **不做跨用户共享。** 每个用户只能用自己的订阅，池化、转售、账号共享都不做——
  这条是真正的红线，越过去性质就变了。
- **不内置厂商登录、不做令牌提取。** 仓库里不出现任何厂商的凭据处理逻辑。
- **不代管、不同步、不上传凭据。**
- **不做公网部署。** 单用户 + 回环是安全模型的基础。
- **不做动态 DAG。** 固定五阶段是可审计性的来源。
- **不做失败兜底。** 不因额度、登录或超时自动切换账号或计费方式。

## 路线图

| 波次 | 内容 | 目标 |
|---|---|---|
| W0 | 仓库骨架、许可、文档 | 可公开 |
| W1 | 内核去厂商化，`Adapter` 协议改为数据导向 | 内核不出现任何厂商名 |
| W2 | CLI 与 HTTP 双后端适配器 | 端到端串起多个订阅 |
| W3 | 库结构迁移、服务端 token | 可安全升级 |
| W4 | Linux 沙箱后端 | 跨平台 |
| W5 | 代码库搜索 | 规划阶段按 token 预算检索 |

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。提交前请确保 `pytest`、`ruff`、`mypy` 全绿，
并运行 `sh scripts/check_leaks.sh`。
安全问题请走 [SECURITY.md](SECURITY.md) 里的私密渠道，不要开公开 issue。

## 许可

[Apache-2.0](LICENSE)。
