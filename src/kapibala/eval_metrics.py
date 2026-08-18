"""评估指标：intent 准确率 + dissatisfied 宏 F1。

dissatisfied 是不均衡二分类（不满样本少），用宏 F1 而非准确率（plan2 第 2.3 节）。
"""

from __future__ import annotations


def intent_accuracy(golds: list[str], preds: list[str]) -> float:
    assert len(golds) == len(preds) and golds
    correct = sum(g == p for g, p in zip(golds, preds, strict=True))
    return correct / len(golds)


def _f1(golds: list[bool], preds: list[bool], positive: bool) -> float:
    tp = sum(g == positive and p == positive for g, p in zip(golds, preds, strict=True))
    fp = sum(g != positive and p == positive for g, p in zip(golds, preds, strict=True))
    fn = sum(g == positive and p != positive for g, p in zip(golds, preds, strict=True))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def dissatisfied_macro_f1(golds: list[bool], preds: list[bool]) -> float:
    """不满类与满意类的 F1 取宏平均。"""
    assert len(golds) == len(preds) and golds
    return (_f1(golds, preds, True) + _f1(golds, preds, False)) / 2


def per_intent_accuracy(golds: list[str], preds: list[str]) -> dict[str, tuple[int, int]]:
    """每个 intent 的 (正确数, 总数)，用于 bad case 归因。"""
    stats: dict[str, list[int]] = {}
    for g, p in zip(golds, preds, strict=True):
        correct, total = stats.get(g, [0, 0])
        stats[g] = [correct + (g == p), total + 1]
    return {k: (v[0], v[1]) for k, v in sorted(stats.items())}
