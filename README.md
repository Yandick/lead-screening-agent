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
# CLI 入口（M2 提供）：kapibala 或 python -m kapibala.cli
```

## 约束保证机制（随开发进度更新）

| 约束 | 强制层 | 状态 |
|------|--------|------|
| 任意 60 秒滑动窗口最多 1 条主动消息 | `rate_limiter.py` 滑动窗口 + `executor.py` 统一发送函数（唯一写时间戳处） | ✅ M1 已实现并测试（含 59s/60s 边界） |
| 连续 2 次 off_topic / dissatisfied 必转人工 | `state_machine.py` 共享计数器，代码写死、不依赖 LLM 输出 | ✅ M1 已实现并测试 |
| 对话内容无法越权执行动作 | `executor.py` 动作 allowlist（非 `Action` 枚举一律拒绝）+ escalated/closed 状态门禁 | ✅ M1 已实现并测试 |
| 防套系统提示词/内部规则 | 分类与生成分离 + canary 哨兵 + 输出审查（已知局限：无法 100% 拦截自然语言泄露） | M2 待实现 |

## 已知局限

- 内存存储，进程重启后状态清空；
- 自然语言泄露防御（约束 4）无法 100% 保证，可强制保证的是动作越权、升级静默与限流；
- 首版无真实 IM / 数据库 / 前端。

## 开发耗时记录

| 里程碑 | 内容 | 实际耗时 |
|--------|------|----------|
| M0 | 仓库与项目骨架、LLMAdapter 接口、真值表文档、冒烟测试 | 约 20 分钟（含 conda 环境创建与镜像源排障） |
| M1 | 状态机、策略真值表、滑动窗口限流器、执行层（allowlist/门禁/审计），44 个测试全绿 | 约 20 分钟 |
