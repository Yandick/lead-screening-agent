# 获客初筛 Agent（kapibala）

用 LLM 对客户消息做**多维状态估计**（意图 + 情绪不满 + 跟进意愿），再由确定性状态机与策略层选择唯一合法动作执行。LLM 只估计、不决策、不执行；所有硬性约束由代码强制。

## 架构

```text
客户消息
  -> LLM 结构化状态估计（Estimation，仅数据结构）
  -> 确定性状态机（连续异常计数、会话状态）
  -> 策略层（真值表查表，见 src/kapibala/policy.py 文档字符串）
  -> 执行层（allowlist 校验 -> 状态门禁 -> 滑动窗口限流 -> 统一发送 -> 审计）
```

- LLM 接入通过 `LLMAdapter` 接口隔离（`src/kapibala/adapters/base.py`），业务代码不依赖具体 SDK；
- 动作枚举仅 4 个：`reply` / `schedule_followup` / `escalate_to_human` / `mark_not_interested`。

## 启动方式

```bash
conda activate kapibala
pip install -e ".[dev]"
cp .env.example .env   # 填入 GEMINI_API_KEY
pytest                 # 跑测试
python -m kapibala.cli # 启动终端 demo（当前为 fake 模式，用 script 命令预排 LLM 判定）
```

fake 模式演示示例：

```text
script intent=off_topic          # 预排下一次 LLM 判定
msg c1 今天天气真好               # 处理一条客户消息
show_state c1 / reactivate c1 / run_followups
```

## 约束保证机制（随开发进度更新）

| 约束 | 强制层 | 状态 |
|------|--------|------|
| 任意 60 秒滑动窗口最多 1 条主动消息 | `rate_limiter.py` 滑动窗口 + `executor.py` 统一发送函数（唯一写时间戳处） | ✅ M1 已实现并测试（含 59s/60s 边界） |
| 连续 2 次 off_topic / dissatisfied 必转人工 | `state_machine.py` 共享计数器，代码写死、不依赖 LLM 输出 | ✅ M1 已实现并测试 |
| 对话内容无法越权执行动作 | `executor.py` 动作 allowlist（非 `Action` 枚举一律拒绝）+ escalated/closed 状态门禁 | ✅ M1 已实现并测试 |
| 防套系统提示词/内部规则 | 分类与生成分离 + `output_guard.py`（canary 哨兵 + 凭证正则 + 长度检查，命中即替换安全回复） | ✅ M2 已实现并测试（已知局限见下） |

## 已知局限

- 内存存储，进程重启后状态清空；
- 自然语言泄露防御（约束 4）无法 100% 保证，可强制保证的是动作越权、升级静默与限流；
- 首版无真实 IM / 数据库 / 前端。

## 开发耗时记录

| 里程碑 | 内容 | 实际耗时 |
|--------|------|----------|
| M0 | 仓库与项目骨架、LLMAdapter 接口、真值表文档、冒烟测试 | 约 20 分钟（含 conda 环境创建与镜像源排障） |
| M1 | 状态机、策略真值表、滑动窗口限流器、执行层（allowlist/门禁/审计），44 个测试全绿 | 约 20 分钟 |
| M2 | 管道串通（agent.py）、FakeAdapter、CLI 端到端、canary/正则/长度输出防护、run_followups，累计 65 个测试全绿 | 约 25 分钟 |
| M3 | GeminiAdapter（structured output + 超时重试 + fail-closed）、CLI 接入、模型探测与真实 smoke test，累计 72 个测试全绿 | 约 30 分钟（另约 1 小时消耗在主办方 key 服务端 401 排障与更换上，非开发耗时） |
| M4 | 评估集 71 条（LLM 草稿 + 人工定标）、eval 脚本与指标、三轮迭代（intent 0.817→0.915，dissatisfied 宏 F1 0.909→0.983，见 eval_history.md），累计 77 个测试全绿 | 约 1 小时 40 分钟（含 LLM 生成草稿与三轮真实评估运行时间） |
