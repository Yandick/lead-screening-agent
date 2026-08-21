"""输入侧注入检测闸门（input-level）：LLM 工作流之前的独立分类器。

设计要点：
- 使用 Llama-Prompt-Guard-2-86M（DeBERTa 级小模型，CPU 即可实时推理）；
- 惰性加载：首次判定时才加载模型，CLI/测试启动不受影响；
- 长文本分段批量扫描：超过模型窗口（512 token）时按窗口切段，
  所有段合成一个 batch 一次前向，取各段最高恶意分；
- fail-open：依赖缺失或运行异常时放行并记审计——真正的底线是
  下游的确定性层（动作白名单/状态机/output_guard），本闸门是削减层。

判 unsafe 的行为（在 agent.py 中）：不追加历史、不调 LLM、不计异常计数，
回复固定话术并记 injection_blocked 审计。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

#: 检出注入后的固定回复：不承认检测存在、不泄露防护细节，礼貌拉回业务
INJECTION_BLOCK_REPLY = (
    "抱歉，这条消息我这边无法处理。如果您有产品或方案相关的问题，欢迎随时问我。"
)

#: ModelScope 模型 ID（国内可直连，无需代理）
DEFAULT_MODEL_ID = "LLM-Research/Llama-Prompt-Guard-2-86M"

#: 模型文件默认放在仓库 models/ 目录下（已 gitignore，不入库）；
#: 可用 INJECTION_GUARD_DIR 覆盖。
_DEFAULT_MODEL_DIR = (
    Path(__file__).resolve().parents[2] / "models" / DEFAULT_MODEL_ID.split("/")[-1]
)

#: 模型输入窗口（token）
MAX_TOKENS = 512


class InjectionGuard(Protocol):
    """注入检测接口：输入一条已校验的客户消息，返回是否应拦截。"""

    def is_unsafe(self, text: str) -> bool: ...


class PromptGuardClassifier:
    """Llama-Prompt-Guard-2 分类器（CPU，惰性加载，分段批量扫描）。

    Args:
        model_id: ModelScope 模型 ID。
        threshold: 恶意概率阈值，任一段超过即判 unsafe。
    """

    def __init__(self, model_id: str = DEFAULT_MODEL_ID, threshold: float = 0.5) -> None:
        self._model_id = model_id
        self._threshold = threshold
        self._tokenizer = None
        self._model = None
        self._malicious_index: int | None = None

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch  # noqa: PLC0415 延迟导入：未启用时不产生依赖
        from modelscope import snapshot_download  # noqa: PLC0415
        from transformers import (  # noqa: PLC0415
            AutoModelForSequenceClassification,
            AutoTokenizer,
        )

        model_dir = Path(os.environ.get("INJECTION_GUARD_DIR", str(_DEFAULT_MODEL_DIR)))
        if not (model_dir / "config.json").exists():
            model_dir.parent.mkdir(parents=True, exist_ok=True)
            snapshot_download(self._model_id, local_dir=str(model_dir))
        self._tokenizer = AutoTokenizer.from_pretrained(str(model_dir))
        self._model = AutoModelForSequenceClassification.from_pretrained(str(model_dir))
        self._model.eval()
        self._torch = torch
        # 从 id2label 找恶意类索引；找不到则按惯例取 1
        id2label = getattr(self._model.config, "id2label", {}) or {}
        self._malicious_index = 1
        for idx, name in id2label.items():
            if any(k in str(name).upper() for k in ("MALICIOUS", "INJECTION", "JAILBREAK")):
                self._malicious_index = int(idx)
                break

    def malicious_score(self, text: str) -> float:
        """整条消息的最高恶意概率（超过窗口分段后逐段打分取最大）。"""
        self._load()
        tokenizer = self._tokenizer
        assert tokenizer is not None and self._model is not None
        ids = tokenizer(text, add_special_tokens=False)["input_ids"]
        segments = [
            ids[start : start + MAX_TOKENS - 2]  # 预留 [CLS]/[SEP]
            for start in range(0, max(len(ids), 1), MAX_TOKENS - 2)
        ]
        batch = tokenizer.pad(
            [{"input_ids": seg} for seg in segments],
            padding=True,
            return_tensors="pt",
        )
        with self._torch.no_grad():
            logits = self._model(**batch).logits
        probs = self._torch.softmax(logits, dim=-1)[:, self._malicious_index]
        return float(probs.max())

    def is_unsafe(self, text: str) -> bool:
        return self.malicious_score(text) >= self._threshold


def build_default_guard() -> InjectionGuard | None:
    """按环境装配默认闸门：INJECTION_GUARD=0 显式关闭；依赖缺失返回 None（fail-open）。"""
    if os.environ.get("INJECTION_GUARD", "1") == "0":
        return None
    try:
        return PromptGuardClassifier(
            model_id=os.environ.get("INJECTION_GUARD_MODEL", DEFAULT_MODEL_ID)
        )
    except Exception:
        return None
