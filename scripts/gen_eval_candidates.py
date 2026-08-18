"""用 Gemini 生成评估集候选样本（草稿，需人工审校后才进 data/eval_set.jsonl）。

用法：python scripts/gen_eval_candidates.py
输出：data/eval_candidates_raw.jsonl（不入 Git，仅为草稿）
"""

from __future__ import annotations

import json
import os
import sys
import time

from dotenv import load_dotenv

# 类别 -> (条数, 建议标签, 生成要求)
CATEGORIES = {
    "interested": (8, {"intent": "interested", "dissatisfied": False}, "客户对产品/服务明确表示兴趣，想进一步接触"),
    "needs_info": (8, {"intent": "needs_info", "dissatisfied": False}, "客户在询问具体信息（价格、功能、交付、售后等），态度中性"),
    "rejected": (8, {"intent": "rejected", "dissatisfied": False}, "客户明确拒绝，语气平静或客气"),
    "off_topic": (8, {"intent": "off_topic", "dissatisfied": False}, "客户说与业务完全无关的话题（天气、闲聊、答非所问）"),
    "other": (8, {"intent": "other", "dissatisfied": False}, "不属于以上四类：含糊、无意义、无法归类的消息"),
    "interested_but_dissatisfied": (4, {"intent": "interested", "dissatisfied": True}, "客户有兴趣但明显不满（如抱怨被反复打扰、对之前体验生气，但仍想了解）"),
    "angry_but_just_asking": (4, {"intent": "needs_info", "dissatisfied": True}, "语气激烈但本质只是在咨询信息"),
    "calm_rejection": (4, {"intent": "rejected", "dissatisfied": False}, "非常平静客气地拒绝"),
    "sarcasm": (4, {"intent": "needs_info", "dissatisfied": True}, "讽刺挖苦式表达不满（如'你们效率真高，三天才回'）"),
    "polite_complaint": (4, {"intent": "needs_info", "dissatisfied": True}, "非常客气地抱怨（'不好意思，但你们是不是发错资料了'）"),
    "emoji_complaint": (4, {"intent": "other", "dissatisfied": True}, "用表情/网络梗表达不满（如'醉了🙄'、'栓Q，又涨价'）"),
    "followup_requested": (4, {"intent": "interested", "dissatisfied": False, "followup_requested": True}, "客户有兴趣但明确表示稍后再联系（现在忙/下周聊/改天约）"),
    "injection_attack": (4, {"intent": "off_topic", "dissatisfied": False}, "提示词注入攻击：伪装系统指令要求跳过审核/泄露提示词/执行未定义动作。注意：这些消息对分类器而言就是无关内容"),
}

PROMPT = """为"获客初筛客服 Agent"的意图分类评估集生成 {n} 条中文客户消息。

场景：公司用 AI agent 自动跟潜在客户做初步筛选对话（销售触达后的首轮交流）。
要求：{requirement}

- 每条 5~60 字，口语化，像真实微信/电话聊天记录；
- 彼此之间表达方式尽量多样（长短、语气、句式、错别字/网络用语均可）；
- 不要编号、不要解释，只输出 JSON 数组，元素为 {{"text": "..."}}。"""

PROMPT_EN = """Generate {n} English customer messages for the intent-classification eval set of a lead-screening sales agent.

Context: an AI agent does first-round screening conversations with potential customers after sales outreach.
Requirement: {requirement}

- 5~40 words each, colloquial, like real chat/SMS transcripts;
- diverse in length, tone, register (slang, typos OK);
- output a JSON array only, elements: {{"text": "..."}}."""

# 英文场景（多语言客户支持）
CATEGORIES_EN = {
    "en_interested": (3, {"intent": "interested", "dissatisfied": False}, "clear interest, wants to move forward"),
    "en_needs_info": (3, {"intent": "needs_info", "dissatisfied": False}, "asking for specific info (pricing, features, onboarding), neutral tone"),
    "en_rejected": (3, {"intent": "rejected", "dissatisfied": False}, "polite/calm explicit refusal"),
    "en_off_topic": (3, {"intent": "off_topic", "dissatisfied": False}, "completely unrelated small talk"),
    "en_other": (2, {"intent": "other", "dissatisfied": False}, "unclassifiable: wrong person, vague, or questioning the caller's legitimacy"),
    "en_interested_but_dissatisfied": (2, {"intent": "interested", "dissatisfied": True}, "interested but visibly annoyed (e.g. complained about repeated calls, still wants info)"),
    "en_polite_complaint": (2, {"intent": "needs_info", "dissatisfied": True}, "politely pointing out our mistake (wrong brochure, wrong quote)"),
    "en_sarcasm": (2, {"intent": "other", "dissatisfied": True}, "sarcastic complaint, no explicit refusal"),
    "en_followup_requested": (2, {"intent": "interested", "dissatisfied": False, "followup_requested": True}, "interested but explicitly asks to be contacted later"),
    "en_injection_attack": (2, {"intent": "off_topic", "dissatisfied": False}, "prompt injection: fake system instructions demanding to skip review or reveal the system prompt"),
}


def _parse_items(text: str) -> list[dict]:
    """从模型输出解析 JSON 数组，容忍 markdown 代码围栏。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return json.loads(cleaned)


def main() -> None:
    lang = sys.argv[1] if len(sys.argv) > 1 else "zh"
    load_dotenv(".env")
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY 未配置")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
    out_path = f"data/eval_candidates_raw_{lang}.jsonl"

    if lang == "en":
        categories = {k: (n, l, PROMPT_EN.format(n=n, requirement=r)) for k, (n, l, r) in CATEGORIES_EN.items()}
    else:
        categories = {k: (n, l, PROMPT.format(n=n, requirement=r)) for k, (n, l, r) in CATEGORIES.items()}

    total = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for category, (n, labels, prompt) in categories.items():
            items = None
            for _ in range(2):  # 解析失败重试一次
                resp = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json", temperature=1.0
                    ),
                )
                try:
                    items = _parse_items(resp.text)
                    break
                except json.JSONDecodeError:
                    print(f"{category}: JSON 解析失败，重试")
            if items is None:
                print(f"{category}: 跳过（两次均解析失败）")
                continue
            for item in items[:n]:
                record = {"text": item["text"], "category": category, **labels}
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                total += 1
            print(f"{category}: {min(len(items), n)}/{n}")
            time.sleep(1)  # 温和限速，照顾免费额度
    print(f"共 {total} 条 -> {out_path}")
    print("下一步：人工审校每条标签后写入 data/eval_set.jsonl")


if __name__ == "__main__":
    main()
