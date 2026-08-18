"""跑评估集并输出指标。

用法：
    python scripts/eval.py                 # 全量评估（baseline）
    python scripts/eval.py --few-shot      # 带 few-shot 示例（迭代轮次）
    python scripts/eval.py --limit 10      # 调试用小样本

输出：intent 准确率、dissatisfied 宏 F1、分类别准确率、bad case 清单。
结果请记录到 eval_history.md（改动 -> 指标变化）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kapibala.eval_metrics import (  # noqa: E402
    dissatisfied_macro_f1,
    intent_accuracy,
    per_intent_accuracy,
)

EVAL_SET = Path("data/eval_set.jsonl")


def load_eval_set(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（调试用）")
    parser.add_argument("--few-shot", action="store_true", help="使用 few-shot 示例（迭代轮）")
    args = parser.parse_args()

    load_dotenv(".env")
    from kapibala.adapters.gemini import GeminiAdapter

    records = load_eval_set(EVAL_SET)
    if args.limit:
        records = records[: args.limit]

    adapter = GeminiAdapter(few_shot=args.few_shot)
    golds_intent, preds_intent = [], []
    golds_diss, preds_diss = [], []
    bad_cases = []

    for i, rec in enumerate(records, 1):
        est = adapter.estimate(rec["text"])
        golds_intent.append(rec["intent"])
        preds_intent.append(est.intent.value)
        golds_diss.append(rec["dissatisfied"])
        preds_diss.append(est.dissatisfied)
        if rec["intent"] != est.intent.value or rec["dissatisfied"] != est.dissatisfied:
            bad_cases.append(
                {
                    "text": rec["text"],
                    "gold_intent": rec["intent"],
                    "pred_intent": est.intent.value,
                    "gold_dissatisfied": rec["dissatisfied"],
                    "pred_dissatisfied": est.dissatisfied,
                    "confidence": est.confidence,
                }
            )
        print(f"\r[{i}/{len(records)}]", end="", flush=True)
    print()

    print(f"\nintent 准确率: {intent_accuracy(golds_intent, preds_intent):.3f} ({len(records)} 条)")
    print(f"dissatisfied 宏 F1: {dissatisfied_macro_f1(golds_diss, preds_diss):.3f}")
    print("\n分类别 intent 准确率:")
    for intent, (correct, total) in per_intent_accuracy(golds_intent, preds_intent).items():
        print(f"  {intent}: {correct}/{total}")
    print(f"\nbad case ({len(bad_cases)} 条):")
    for case in bad_cases:
        print(
            f"  {case['text'][:40]!r} gold=({case['gold_intent']}, {case['gold_dissatisfied']}) "
            f"pred=({case['pred_intent']}, {case['pred_dissatisfied']}) conf={case['confidence']:.2f}"
        )


if __name__ == "__main__":
    main()
