"""评估指标单元测试。"""

from kapibala.eval_metrics import (
    dissatisfied_macro_f1,
    intent_accuracy,
    per_intent_accuracy,
)


def test_intent_accuracy():
    assert intent_accuracy(["a", "b", "c"], ["a", "b", "b"]) == 2 / 3


def test_dissatisfied_macro_f1_perfect():
    golds = [True, False, True, False]
    assert dissatisfied_macro_f1(golds, golds) == 1.0


def test_dissatisfied_macro_f1_all_wrong():
    golds = [True, False, True, False]
    preds = [not g for g in golds]
    assert dissatisfied_macro_f1(golds, preds) == 0.0


def test_macro_f1_imbalanced():
    """不满样本少时宏 F1 不应被多数类带高。"""
    golds = [True] + [False] * 9
    preds = [False] * 10  # 全部判为"满意"：准确率 90%，但宏 F1 应明显低
    value = dissatisfied_macro_f1(golds, preds)
    # 不满类 F1=0，满意类 F1=2*(10/10*10/11)/(10/10+10/11)≈0.952，宏平均≈0.476
    assert 0.4 < value < 0.55


def test_per_intent_accuracy():
    stats = per_intent_accuracy(
        ["interested", "interested", "rejected"], ["interested", "other", "rejected"]
    )
    assert stats["interested"] == (1, 2)
    assert stats["rejected"] == (1, 1)
