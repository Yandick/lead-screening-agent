# 理解质量评估迭代记录

评估集：`data/eval_set.jsonl`（71 条，人工审校标注；LLM 生成草稿 + 逐条人工定标签）
模型：`gemini-3.5-flash`，temperature=0
指标：intent 准确率 + dissatisfied 宏 F1（不满类样本少，不用准确率）

## R1：baseline（零样本 prompt）

- **intent 准确率：0.817（58/71）**
- **dissatisfied 宏 F1：0.909**

分类别 intent 准确率：interested 14/18，needs_info 19/19，off_topic 10/11，other 1/9，rejected 14/14

bad case 归因（17 条，三类系统性错误）：

1. **`other` 被吞（6 条）**：讽刺抱怨、发错人、网络用语吐槽几乎全被误判为 `rejected`——模型把"负情绪 + 无明确咨询"一律当拒绝；
2. **`interested` → `needs_info`（4 条）**：表达兴趣但夹带询问（"怎么买""发我方案合适再聊"）被判成纯咨询；
3. **客气抱怨的不满漏检（4 条）**：措辞客气但指出我们发错资料/报错价，`dissatisfied` 被判 false。

另：`off_topic`→`rejected` 1 条（"别发了，正忙着带娃"），边界个例。

## R2：few-shot 第 1 轮（9 条新写示例，不照抄评估集，防泄露）

改动：针对 R1 三类错误加 few-shot——发错人=`other`、讽刺/吐槽无明确拒绝=`other`+不满、有推进信号=`interested`、客气指出问题=不满、明确"别再联系"=`rejected`。

- **intent 准确率：0.915（65/71，+9.8pt）**
- **dissatisfied 宏 F1：0.926（+1.7pt）**

分类别 intent 准确率：interested 16/18，needs_info 18/19，off_topic 10/11，other 7/9，rejected 14/14

剩余 bad case（10 条）归因：

1. **客气抱怨不满仍漏检（4 条）**：单条 few-shot 没打透"指出我方错误=不满"，模型把"是不是发错啦"当中性更正请求；
2. **`other` 边界残留（2 条）**：威胁投诉（"再发直接投诉"）仍判 `rejected`、"你们是骗子吗"判 `needs_info`；
3. **interested/needs_info 固有模糊（2 条）**："听起来不错+问收费"、"想试用再定买不买"——边界样本，标注本身可说理；
4. 个例："别发了，正忙着带娃"判 `rejected`（金标 `off_topic`，此条标注本身存争议，答辩可主动说明）。

## R3：prompt 定义修订 + 补 3 条示例（共 12 条）

改动：intent 五类补充判定细则（interested 推进信号、rejected 必须明确拒绝接触、other 含身份质疑/发错人/纯吐槽）；dissatisfied 明确"客气指出我方错误也算不满"；新增示例覆盖涨价吐槽、威胁投诉、发错资料。

- **intent 准确率：0.915（65/71，持平）**
- **dissatisfied 宏 F1：0.983（+5.7pt）**——客气抱怨 4 条全部纠回

分类别 intent 准确率：interested 17/18，needs_info 16/19，off_topic 9/11，other 9/9，rejected 14/14

剩余 bad case（6 条）归因：4 条为 `interested`/`needs_info` 固有边界（"想试用再决定买不买"类，标注本身可说理）、1 条标注争议（"别发了，正忙着带娃"）、1 条迷惑消息（"你是拼车的人吗"）。已无系统性错误，继续迭代会过拟合边界标注，停止。

## 最终结论

| 轮次 | 改动 | intent 准确率 | dissatisfied 宏 F1 |
|------|------|--------------|-------------------|
| R1 | 零样本 baseline（中文 71 条） | 0.817 | 0.909 |
| R2 | +9 条 few-shot（中文 71 条） | 0.915 | 0.926 |
| R3 | prompt 定义修订 + 12 条 few-shot（中文 71 条） | 0.915 | **0.983** |
| R4 | 同 R3 配置，双语 95 条（+24 英文） | **0.916** | **0.974** |

已知边界：`interested` 与 `needs_info` 在"带询问的兴趣表达"上存在固有模糊，策略层对这两类都走 `reply`（仅话术不同），该模糊不影响动作安全性。

## R4：双语评估（补 24 条英文场景后全量复跑）

英文 24 条（LLM 草稿 + 人工定标，1 处改标）覆盖五类意图、正交不满、客气抱怨、讽刺、跟进请求与注入攻击。英文 bad case 仅 3 条，均为边界标注争议（"I'm interested but wanted to know..."、质疑身份的不满判定、注入样本 other/off_topic 之别），英文注入攻击全部未被带偏。
