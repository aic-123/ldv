#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一模型通道（E1/E2 双模型用）：deepseek-flash 与 glm-5 同一接口。

    from llm import chat
    r = chat(messages, max_tokens=..., temperature=0, tries=6)   # 用 set_model 预设
    chat.set_model("glm-5")

返回统一形状 {text, usage{in,out,cached}, ms, finish}。
⚠️ 两个家的 tokenizer 不同（中文下 deepseek ≈3 字/token、glm ≈1.8 字/token）；
   预算的 150k 以 **deepseek 口径估算单位**统一记账（见 PREREG_E1 V3 增补），
   跨模型比较的是「同一预算规则下的表现」，不是绝对 token 数。
key 只从环境变量读（DEEPSEEK_API_KEY / GLM_API_KEY），不落盘。
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

class BalanceError(RuntimeError):
    """账户余额/资源包耗尽 —— 调用方应停批，而不是重试。"""


MODEL = "deepseek-flash"

DEEPSEEK_BASE = "https://api.deepseek.com/chat/completions"
GLM_BASE = "https://open.bigmodel.cn/api/paas/v4/chat/completions"


def set_model(name: str) -> None:
    global MODEL
    MODEL = name


def _post(base: str, key: str, body: dict, timeout: int) -> dict:
    req = urllib.request.Request(base, data=json.dumps(body, ensure_ascii=False).encode(),
                                 method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def chat(messages: list[dict], *, model: str | None = None, max_tokens: int = 2048,
         temperature: float = 0.0, tries: int = 4, timeout: int = 600) -> dict:
    m = model or MODEL
    provider = "glm" if m.startswith("glm") else "deepseek"
    key_env = "GLM_API_KEY" if provider == "glm" else "DEEPSEEK_API_KEY"
    key = os.environ.get(key_env)
    if not key:
        raise RuntimeError(f"环境变量 {key_env} 未设置")
    body: dict = {"model": m, "messages": messages, "max_tokens": max_tokens,
                  "temperature": temperature}
    if provider == "deepseek":
        body["thinking"] = {"type": "disabled"}       # 与历史实验同口径
    if m.startswith("glm-4"):
        body["thinking"] = {"type": "disabled"}       # glm-4.6 是思考型：不关 ⇒ 输出预算全被推理吃掉
                                                      # （2026-10-31 实测 32 tok 全耗推理、正文空）
    last = ""
    for i in range(tries):
        req_t0 = time.perf_counter()
        try:
            raw = _post(DEEPSEEK_BASE if provider == "deepseek" else GLM_BASE,
                        key, body, timeout)
            ms = (time.perf_counter() - req_t0) * 1000
            ch = (raw.get("choices") or [{}])[0]
            u = raw.get("usage") or {}
            if provider == "deepseek":
                usage = {"in": u.get("prompt_cache_miss_tokens", 0)
                         + u.get("prompt_cache_hit_tokens", 0),
                         "out": u.get("completion_tokens", 0),
                         "cached": u.get("prompt_cache_hit_tokens", 0)}
            else:                                      # glm：暂无缓存口径
                usage = {"in": u.get("prompt_tokens", 0),
                         "out": u.get("completion_tokens", 0),
                         "cached": 0}
            return {"text": (ch.get("message") or {}).get("content") or "",
                    "usage": usage, "ms": ms, "raw": raw,
                    "finish": ch.get("finish_reason")}
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:                          # noqa: BLE001
                pass
            last = f"HTTP {e.code}: {detail}"
            if e.code in (400, 401, 402, 403):
                raise
            if "余额不足" in detail or "1113" in detail:
                raise BalanceError(f"{m}: {detail}")   # 干净停批，不硬锤
        except Exception as exc:                       # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(min(30, 1.5 * (i + 1) ** 2))        # 退避加长（429 是小时级窗口）
    raise RuntimeError(f"{m} 调用失败（{tries} 次）：{last}")
