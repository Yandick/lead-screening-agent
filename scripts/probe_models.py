"""探测当前 GEMINI_API_KEY 可用的模型。

用法：python scripts/probe_models.py
1. 列出 models.list() 中支持 generateContent 的模型；
2. 对候选模型各做一次最小 generate_content 调用，报告可用性。
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

CANDIDATES = [
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
]


def main() -> None:
    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("GEMINI_API_KEY 未配置（.env 或环境变量）")

    from google import genai

    client = genai.Client(api_key=api_key)

    print("== models.list() 中支持 generateContent 的模型 ==")
    try:
        for m in client.models.list():
            name = (getattr(m, "name", "") or "").removeprefix("models/")
            actions = getattr(m, "supported_actions", None) or []
            if "generateContent" in actions:
                print(" ", name)
    except Exception as exc:
        print(f"  models.list() 失败：{type(exc).__name__}: {exc}")

    print("\n== 候选模型最小调用测试 ==")
    for name in CANDIDATES:
        try:
            resp = client.models.generate_content(
                model=name, contents="只回复 ok 两个字母。"
            )
            text = (getattr(resp, "text", "") or "").strip()[:20]
            print(f"  {name}: OK -> {text!r}")
        except Exception as exc:
            print(f"  {name}: FAIL -> {type(exc).__name__}: {str(exc)[:150]}")


if __name__ == "__main__":
    main()
