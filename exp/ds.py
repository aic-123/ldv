# -*- coding: utf-8 -*-
"""DeepSeek 客户端（实验用，stdlib only）。key 只从环境变量读，**不落盘**。"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE = "https://api.deepseek.com/chat/completions"


def _key() -> str:
    k = os.environ.get("DEEPSEEK_API_KEY") or ""
    if not k:
        raise SystemExit("没有 DEEPSEEK_API_KEY（别把它写进文件，用环境变量传）")
    return k


def chat(messages: list[dict], *, model: str = "deepseek-flash", max_tokens: int = 2048,
         temperature: float = 0.0, thinking: bool = False, json_mode: bool = False,
         timeout: int = 600, tries: int = 4) -> dict:
    """一次调用。返回 `{text, usage, ms, raw}`。

    ⚠️ 默认 **关掉思考**：思考 token 也计费，而且 A 组要比的是「一趟扫完」的能力，
       不是「能想多久」。开思考会变成另一个实验（附注里单列一次即可）。
    """
    body: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if not thinking:
        # 文档：默认思考模式，可切非思考。字段名以实测为准（见 README 的核查记录）
        body["thinking"] = {"type": "disabled"}

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    last = ""
    for i in range(tries):
        req = urllib.request.Request(BASE, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {_key()}")
        req.add_header("Content-Type", "application/json")
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = json.loads(r.read().decode("utf-8"))
            ms = (time.perf_counter() - t0) * 1000
            ch = (raw.get("choices") or [{}])[0]
            return {"text": (ch.get("message") or {}).get("content") or "",
                    "usage": raw.get("usage") or {}, "ms": ms, "raw": raw,
                    "finish": ch.get("finish_reason")}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            last = f"HTTP {e.code}: {detail}"
            if e.code in (400, 401, 402, 403):     # 参数/鉴权/余额：重试没用
                raise
        except Exception as exc:                   # noqa: BLE001 - 网络抖动
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"调用失败（{tries} 次）：{last}")
