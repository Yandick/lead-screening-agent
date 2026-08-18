"""策略层：动作映射真值表（plan2.md 第 3 节，M1 编码依据）。

状态为 active 时，按以下优先级逐条匹配，命中即执行：

| # | 条件 | 计数器操作 | 动作 |
|---|------|-----------|------|
| 1 | intent == rejected | 清零 | mark_not_interested → closed |
| 2 | confidence < 0.5（连续第 2 次） | — | escalate_to_human |
| 3 | confidence < 0.5（首次） | 冻结（不增不清） | reply（澄清反问） |
| 4 | dissatisfied == true | +1；≥2 则升级 | escalate_to_human 或 reply（安抚 + 回应其意图） |
| 5 | intent == off_topic | +1；≥2 则升级 | escalate_to_human 或 reply（礼貌拉回话题） |
| 6 | intent == interested 且 followup_requested | 清零 | reply（确认）+ schedule_followup |
| 7 | intent == interested | 清零 | reply（推进转化） |
| 8 | intent == needs_info | 清零 | reply（解答问题） |
| 9 | intent == other | 清零 | reply（澄清式回应） |

补充规则：

- rejected 优先级最高：客户明确拒绝时，即使 dissatisfied == true 也直接
  关闭会话，不再安抚或升级；
- 规则 4/5 中"≥2 升级"沿用共享连续计数器（off_topic 与 dissatisfied
  混计达 2 次升级，出现其他情况重置）；
- 低置信计数独立于异常计数器：连续两次 confidence < 0.5 转人工；
- 状态 escalated：执行层无条件静默，仅 reactivate 命令恢复 active；
- 状态 closed：不再自动处理。
"""
