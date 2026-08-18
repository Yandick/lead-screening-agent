# 获客初筛 Agent：开工方案（v2，可直接开发）

> 基于 DEVELOPMENT_PLAN.md 修订。修订原则：保留原有分层架构与约束设计，补齐策略映射表、LLM 理解质量评估、确定性输出防护三个缺口，将所有语义歧义落成默认决策。文末「假设与待确认」一节列出的条目，动工前请对照笔试原题核实。

---

## 1. 架构总览（不变）

```text
客户消息
  -> LLM 结构化状态估计（仅估计，不决策、不执行）
  -> 确定性状态机（连续异常计数、会话状态）
  -> 策略层（查真值表，选唯一合法动作）
  -> 执行层（allowlist 校验 -> 状态门禁 -> 滑动窗口限流 -> 发送 -> 审计）
```

LLM 输出永远只是数据结构输入；所有约束（格式、状态转移、动作枚举、限流、静默）都由代码强制。

## 2. LLM 理解模块

### 2.1 输出 Schema（在原 plan 基础上加一个字段）

```json
{
  "intent": "interested | needs_info | rejected | off_topic | other",
  "dissatisfied": true,
  "followup_requested": false,
  "confidence": 0.0,
  "reason": "brief internal rationale"
}
```

- `dissatisfied`：正交信号，不得从 intent 推导；
- `followup_requested`：客户是否明确表示"稍后再联系/现在忙/下周再聊"（新增，用于触发跟进，见策略表）；
- `confidence`：取值 0~1，参与策略（见 3.3），不再只是观测字段；
- `reason`：仅内部审计，不展示。

### 2.2 格式约束

使用 Gemini API 的结构化输出能力（`response_mime_type = application/json` + `response_schema`），意图值在 API 层限定为枚举。**不允许**只在 prompt 里写"请返回 JSON"。

解析失败、字段非法、超时、API 报错：fail-closed，不发送任何客户可见消息，进入待处理并记录错误。

### 2.3 理解质量评估（新增，核心考点）

- **评估集**：`data/eval_set.jsonl`，60~100 条客户消息，人工标注 `intent` + `dissatisfied` 双标签。必须覆盖：
  - 五类意图的正常样本；
  - 正交样例："有兴趣但很不满"、"语气激烈但只是咨询"、"平静地拒绝"；
  - 讽刺、客气抱怨、表情包式不满等边界情绪样本；
  - 3~5 条注入攻击样本（攻击文本不影响标注，只用于验证分类器不被带偏）。
- **评估脚本**：`scripts/eval.py`，调用真实 API 跑评估集，输出 intent 准确率 + dissatisfied 宏 F1（不满类样本少，不用准确率）。
- **迭代流程**：baseline（零样本 prompt）→ 按 bad case 补 few-shot 示例 → 每轮记录指标到 `eval_history.md`。答辩材料里保留至少三轮"改动 → 指标变化"的记录。

## 3. 策略层：动作映射真值表（新增，原 plan 缺失）

状态为 `active` 时，按以下优先级逐条匹配，命中即执行：

| # | 条件 | 计数器操作 | 动作 |
|---|------|-----------|------|
| 1 | `intent == rejected` | 清零 | `mark_not_interested` → `closed` |
| 2 | `confidence < 0.5`（连续第 2 次） | — | `escalate_to_human`（系统无法理解客户，转人工） |
| 3 | `confidence < 0.5`（首次） | 冻结（不增不清） | `reply`（澄清反问："想确认一下，您是想……吗？"） |
| 4 | `dissatisfied == true` | +1；≥2 则升级 | `escalate_to_human` 或 `reply`（安抚 + 回应其意图） |
| 5 | `intent == off_topic` | +1；≥2 则升级 | `escalate_to_human` 或 `reply`（礼貌拉回话题） |
| 6 | `intent == interested` 且 `followup_requested` | 清零 | `reply`（确认）+ `schedule_followup` |
| 7 | `intent == interested` | 清零 | `reply`（推进转化） |
| 8 | `intent == needs_info` | 清零 | `reply`（解答问题） |
| 9 | `intent == other` | 清零 | `reply`（澄清式回应） |

补充规则：

- `rejected` 优先级最高：客户明确拒绝时，即使 `dissatisfied == true` 也直接关闭会话，不再安抚或升级（对一个愤怒的拒绝者继续发消息是最差动作）；
- 规则 4/5 中"≥2 升级"沿用原 plan 的共享连续计数器（见「假设与待确认」第 2 条）；
- 状态 `escalated`：执行层无条件静默，任何消息不触发动作；仅 `reactivate` 命令恢复 `active`；
- 状态 `closed`：不再自动处理。

## 4. 回复生成与输出防护（修订为确定性机制）

- 分类与生成是两次独立调用，生成 prompt 不包含任何内部机密；
- **Canary 机制**：系统提示词中埋入哨兵字符串（如 `CANARY_TOKEN = "ZQX9182-INTERNAL"`），最终发送前对回复做确定性检查：
  1. 是否包含 canary 字符串（命中 = 提示词泄露）；
  2. 是否匹配密钥/凭证正则模式；
  3. 长度与格式是否合规；
- 任一命中：丢弃原文，替换为泛化安全回复（如"这个问题我帮您转给专人处理"），写审计；
- 所有客户可见消息只能经由统一发送函数发出；
- README 中保留原 plan 的诚实声明：自然语言泄露防御无法 100% 保证，可强制保证的是动作越权、升级静默与限流。

## 5. 限流（语义决策，默认从严）

- 每客户滑动窗口 60 秒，最多 **1 条客户可见消息**（`reply` 与跟进触发的发送都计入）；
- 被拒绝时：静默不发送 + 写审计日志（答辩时说明这是可解释的安全降级）；
- 实现为配置项 `RATE_LIMIT_APPLIES_TO_REPLY`（默认 `true`），若原题语义不同可一行切换；
- 只有统一发送函数写时间戳；LLM 重试、策略重算不占配额；
- `schedule_followup` / `escalate_to_human` / `mark_not_interested` 不产生客户可见消息，不占配额；
- 使用可注入时钟，测试覆盖 59s / 60s / 61s 边界。

## 6. 跟进机制（补上原 plan 的演示漏洞）

- `schedule_followup` 将 `{customer_id, due_at, context}` 写入内存跟进队列；
- CLI 提供 `run_followups` 命令：扫描到期跟进，逐条走"生成 -> 输出防护 -> 状态门禁 -> 限流 -> 统一发送"完整链路；
- 这样答辩时被问"跟进怎么发、占不占限流"，可以现场演示，而不是说"只是个标记"。

## 7. CLI 命令

```text
msg <customer_id> <text>     处理一条客户消息，显示判定摘要、状态变化、动作
reactivate <customer_id>     人工恢复 active
show_state <customer_id>     查看状态机与计数器
run_followups                触发到期跟进
eval                         跑评估集并输出指标（便捷入口）
reset <customer_id>          清空该客户状态（演示用）
```

## 8. 开发里程碑（确定性核心先行，LLM 最后接）

**M0 骨架（约半天）**
- Git 仓库、目录结构、`.env.example`、`pyproject.toml`；
- 定义适配器接口 `LLMAdapter.estimate(messages) -> Estimation`，业务代码只依赖此接口；
- 把第 3 节真值表落成代码注释/文档字符串，作为策略层开发依据。
- 完成标准：项目可安装、pytest 空跑通过。

**M1 确定性核心 TDD（约 1 天，全程不碰 LLM）**
- 状态机（含共享计数器、reactivate、closed）；
- 策略层（真值表全覆盖单测）；
- 滑动窗口限流器（可注入时钟）；
- 执行层（allowlist、状态门禁、审计日志）。
- 完成标准：原 plan 测试清单 3、4、5、6、7、8、9、11 全部通过。

**M2 管道串通 + 输出防护（约半天）**
- Fake adapter（返回脚本化 Estimation）+ CLI 端到端；
- Canary + 正则 + 长度的确定性输出检查；
- 跟进队列与 `run_followups`。
- 完成标准：用 fake adapter 在终端演示完整流程；测试清单 13 通过。

**M3 真实 LLM 接入（约半天）**
- Gemini 适配器，structured output 强约束；
- 超时/重试/非法输出 fail-closed。
- 完成标准：真实 API smoke test 通过；测试清单 10、12 通过；密钥不落地。

**M4 理解质量评估与迭代（约 1 天）**
- 标注评估集（这是本阶段最大的工作量，预留足够时间）；
- `scripts/eval.py`；baseline 指标；few-shot 迭代 ≥2 轮；`eval_history.md`。
- 完成标准：intent 准确率、dissatisfied F1 有明确数字；bad case 有归因记录。

**M5 攻击测试与交付（约半天）**
- 三组答辩攻击对话（伪造系统指令 / 转人工后诱导恢复 / 改写套取提示词），记录输入、模型判断、状态变化、最终动作；
- README：架构图、约束保证清单、评估指标、已知局限。
- 完成标准：攻击可影响分类或措辞，但无法突破动作枚举、状态机、静默门禁、限流。

## 9. 目录结构

```text
├── README.md
├── pyproject.toml
├── .env.example
├── data/
│   └── eval_set.jsonl
├── eval_history.md
├── scripts/
│   └── eval.py
├── src/
│   ├── adapters/        # LLM 适配器接口 + Gemini 实现 + Fake 实现
│   ├── state_machine.py
│   ├── policy.py        # 第 3 节真值表
│   ├── rate_limiter.py
│   ├── executor.py      # allowlist + 门禁 + 统一发送函数
│   ├── output_guard.py  # canary / 正则 / 长度检查
│   ├── followup.py
│   ├── audit.py
│   └── cli.py
└── tests/
```

## 10. 假设与待确认（动工前对照笔试原题核实）

1. **限流是否覆盖 `reply`**：默认从严（覆盖），配置开关可切换；若原题"主动消息"仅指系统发起，则改为只限跟进触达；
2. **共享计数器**：默认沿用原 plan（`off_topic` 与 `dissatisfied` 共享连续计数器，混计达 2 次升级）；若原题要求分开计数，改两个计数器即可，状态机结构不变；
3. **rejected 是否立即关闭**：默认是（含 dissatisfied + rejected）；若原题要求拒绝前挽留一次，在真值表规则 1 前插一条挽留规则；
4. **低置信处理**：默认两次连续低置信转人工（本方案新增，原 plan 未定义）；阈值 0.5 先拍定，M4 用评估集校准；
5. **跟进触发**：默认手动 `run_followups` 演示；不做后台定时任务（超出 demo 范围）。

## 11. 相对原 plan 的变更摘要

| 变更 | 原因 |
|------|------|
| 新增第 3 节策略真值表 | 原 plan 策略层无定义，无法编码 |
| Schema 增加 `followup_requested` | 跟进动作需要触发依据，否则策略层无法选择 schedule_followup |
| `confidence` 参与决策（低置信澄清/升级） | 原 plan 该字段为死字段 |
| 输出防护改为 canary + 正则的确定性检查 | 避免"用 prompt 防 prompt 泄露"的自相矛盾，使测试 13 真正可测 |
| 新增评估集、评估脚本、迭代记录 | 原 plan 只验证管道，不验证理解质量；这是题目核心考点 |
| 新增 `run_followups` 演示链路 | 堵住"跟进只是标记"的答辩漏洞 |
| 限流/计数器/rejected 语义落成默认决策 + 待确认清单 | 消除歧义，同时保留对照原题修正的余地 |
| 补充 M0–M5 开发顺序 | 确定性核心先行 TDD，LLM 最后接入，降低联调风险 |
