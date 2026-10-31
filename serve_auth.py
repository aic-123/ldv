# -*- coding: utf-8 -*-
"""**认证与配额**（P1，2026-10-29）—— 「谁能调、调多少」。

## 为什么单独一个文件

壳里原来**谁都能跑**（`GET /<tenant>/run` 无鉴权）。`docs/服务化设计.md` §0 约束①说
壳可以依赖任何东西、内核一行 import 都不许多 —— 认证是**壳的事**，所以它落在壳这一侧，
内核一个字没动（`ldv/` 里没有本文件的任何引用）。

## 三件东西

    TokenStore   读 `tokens.json`，把 `Authorization: Bearer <t>` 换成 `Principal`
    Principal    身份 = 「叫什么」+「能碰哪些租户」+「每分钟多少」+「到什么时候为止」
    Quota        固定窗口计数；超了给 `Retry-After`（可重试，不是写错了）

## 四条判据性的选择（都是「不这样做就会静默错」的那种）

1. **盘上只存摘要**。`tokens.json` 里是 `sha256(token)`，不是 token 本身 ——
   备份、误提交、贴进 issue 都不会泄露可用的凭据。比较用 `hmac.compare_digest`
   （**逐字节比摘要**，不是 `==`）：`==` 在第一个不同字节就返回，
   把「前几位对不对」变成可测的时间差。
   ⚠️ 这是**摘要**不是**加盐哈希**：`tokens.json` 泄露 + 弱 token ⇒ 可被离线爆破。
   所以 `mint()` 用 `secrets.token_urlsafe(32)`（约 256 bit），不是人想的词。
   ⚠️ 而 **256 bit 随机 token 用快哈希（sha256）是合理的** —— 离线爆破的代价本身就是
      天文数字，与 GitHub PAT / Stripe 存 key 哈希同一档。**但一旦允许人手工填 token**
      （低熵），就必须换成 Argon2/bcrypt。这条差别不许含糊。

2. **缺 `quota_per_min` ⇒ 60，不是「不限」**。默认值必须取**紧**的那一侧：
   写配置的人漏一个字段，结果不能是「从此不限量」。要真不限，写 `0`（显式）。

3. **配额是「固定窗口」不是「滑动窗口」**。代价写在明处：窗口边界前后各打一次，
   最坏能在 1 秒内用掉 2× 配额。选它是因为**可测**（不需要注入时钟就能验边界），
   而滑动窗口要额外存每次请求的时刻。要改口径得连判据一起改。
   （成熟替代：`pyrate-limiter` 的 GCRA/TokenBucket —— 见 `docs/壳层选型.md` §2。）

4. **`expires_at` 缺省 = 永不过期，但启动时会数出来警告**（2026-10-29 补）。
   业界要求「有轮换与过期，且支持双 key 重叠」；我们原来一条都没有。
   ⚠️ 缺省为什么不是「90 天后过期」：那会让**已经在用的** `tokens.json` 在某天
      突然全体失效 —— 一个格式升级不能把既有部署打死。所以缺省是「不设」，由人显式写，
      启动时把「有几个没过期时间」印在脸上。

## 与「租户」的关系

`Principal.tenants is None` = 管理员（能碰全部，且能建/删租户）。
否则是 frozenset —— 只在那几个租户上有权。**不是自己的一律 403**，
而且 403 在「租户存不存在」之前判：否则 404 与 403 的差别会**泄露租户是否存在**。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

#: 协议版本。**只增不改**：字段可以加，已有的含义不许变（`docs/P1落地.md` §1）。
PROTOCOL = "ldv-http/1"

#: 缺 `quota_per_min` 时的默认配额（每分钟）。理由见文件头第 2 条。
DEFAULT_QUOTA_PER_MIN = 60

#: 配额窗口（秒）。固定窗口，代价见文件头第 3 条。
WINDOW_S = 60

#: `--mint-token` 默认给多少天有效期（0 = 不过期）。业界基线是 90 天。
DEFAULT_EXPIRES_DAYS = 90


def hash_token(token: str) -> str:
    """盘上存的东西：`sha256(token)` 的十六进制。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint() -> str:
    """造一个新 token。**256 bit 随机** —— 不是人想得出来的词。"""
    return "ldv_" + secrets.token_urlsafe(32)


def parse_expires(raw) -> tuple[str | None, float | None]:
    """`expires_at` ⇒ `(原文, epoch 秒)`。`None`/空 ⇒ 不过期。

    ⚠️ 只写日期（`2027-01-31`）按**那天 23:59:59** 算 —— 写这个的人意思是
       「到那天为止」，不是「那天零点就废」。
    ⚠️ 写坏了**当场抛**（不静默当「不过期」）：一个写错的过期时间，与「没写」
       在运行时的表现**一模一样**，而前者是人以为已经设了保护。
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, None
    s = str(raw).strip()
    try:
        if len(s) == 10:
            dt = _dt.datetime.combine(_dt.date.fromisoformat(s), _dt.time(23, 59, 59))
        else:
            dt = _dt.datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(
            f"expires_at 不是 ISO 日期/时间：{raw!r}（例：2027-01-31 或 2027-01-31T09:00:00）"
        ) from exc
    return s, dt.timestamp()


@dataclass(frozen=True)
class Principal:
    """身份。`tenants is None` ⇒ 管理员（全部租户 + 建删租户）。"""

    name: str
    tenants: frozenset[str] | None = None
    quota_per_min: int | None = DEFAULT_QUOTA_PER_MIN
    #: `expires_at` 的**原文**（回显给人看）；`None` = 不过期
    expires_at: str | None = None
    #: 上面那个的 epoch 秒；`None` = 不过期
    expires_ts: float | None = field(default=None, repr=False)

    @property
    def admin(self) -> bool:
        return self.tenants is None

    def may(self, tenant: str) -> bool:
        return self.tenants is None or tenant in self.tenants

    def expired(self, now: float) -> bool:
        return self.expires_ts is not None and now >= self.expires_ts


class TokenStore:
    """`tokens.json` 的只读视图 + 认证。

    文件形状（`tokens.json`）：

        {"tokens": [{"name": "alice", "sha256": "…", "tenant": "demo",
                     "quota_per_min": 60, "expires_at": "2027-01-31"}]}

    `tenant` 写 `"*"` 或省略 ⇒ 管理员。`expires_at` 省略 ⇒ 不过期。
    """

    def __init__(self, entries: list[tuple[str, Principal]], *, open_mode: bool = False,
                 now=None):
        self._entries = entries
        self.open = open_mode
        self._now = now or time.time

    # ── 构造 ──────────────────────────────────────────────────────────
    @classmethod
    def empty(cls) -> "TokenStore":
        """**一个 token 都不认**。缺文件 / 文件读坏时用它 —— 失败方向是「谁都不许」。"""
        return cls([])

    @classmethod
    def open_for_all(cls) -> "TokenStore":
        """**显式**匿名（`--allow-anonymous`）。只在人明确要求时用。"""
        return cls([], open_mode=True)

    @classmethod
    def load(cls, path: str | Path, *, now=None) -> "TokenStore":
        p = Path(path)
        if not p.is_file():
            return cls.empty()
        raw = json.loads(p.read_text(encoding="utf-8"))
        return cls.from_obj(raw, now=now)

    @classmethod
    def from_obj(cls, raw: object, *, now=None) -> "TokenStore":
        if not isinstance(raw, dict) or not isinstance(raw.get("tokens"), list):
            raise ValueError("tokens.json 必须是 {\"tokens\": [...]} 形状")
        entries: list[tuple[str, Principal]] = []
        for i, it in enumerate(raw["tokens"]):
            if not isinstance(it, dict):
                raise ValueError(f"tokens[{i}] 不是对象")
            digest = str(it.get("sha256") or "").strip().lower()
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError(f"tokens[{i}].sha256 必须是 64 位十六进制")
            tenant = it.get("tenant")
            tenants = None if tenant in (None, "", "*") else frozenset({str(tenant)})
            q = it.get("quota_per_min", DEFAULT_QUOTA_PER_MIN)
            q = None if q is None else int(q)
            exp_raw, exp_ts = parse_expires(it.get("expires_at"))
            entries.append((digest, Principal(str(it.get("name") or f"t{i}"),
                                              tenants, q, exp_raw, exp_ts)))
        return cls(entries, now=now)

    # ── 认证 ──────────────────────────────────────────────────────────
    def authenticate_detail(self, header: str | None,
                            now: float | None = None) -> tuple[Principal | None, str]:
        """`(身份, 原因)`。原因 ∈ `ok` / `missing` / `unknown` / `expired`。

        ⚠️ **四种原因要分得开**（这是「过期」与「无效」必须长得不一样的原因）：
           401 的状态码一样，但正文要说清是「没带」「认不出」还是「过期了」——
           否则人会去重新申请一个本来还有效的 token，或者反过来以为服务挂了。
        """
        if self.open:
            return Principal("anonymous", None, None), "ok"
        token = parse_bearer(header)
        if token is None:
            return None, "missing"
        want = hash_token(token)
        hit: Principal | None = None
        # ⚠️ **不 break**：即使已经命中也要比完，否则「第几个条目命中」会从耗时里漏出来。
        for digest, principal in self._entries:
            if hmac.compare_digest(digest, want):
                hit = principal
        if hit is None:
            return None, "unknown"
        if hit.expired(self._now() if now is None else now):
            return None, "expired"
        return hit, "ok"

    def authenticate(self, header: str | None) -> Principal | None:
        """只看「认不认得出」的旧接口（等价于 `authenticate_detail(...)[0]`）。"""
        return self.authenticate_detail(header)[0]

    def entries(self) -> list[Principal]:
        return [p for _d, p in self._entries]

    def by_name(self, name: str) -> list[Principal]:
        return [p for p in self.entries() if p.name == name]

    def without_expiry(self) -> list[str]:
        """没有 `expires_at` 的 token 名字 —— 启动时数出来警告用（去重保序）。"""
        seen: list[str] = []
        for p in self.entries():
            if p.expires_ts is None and p.name not in seen:
                seen.append(p.name)
        return seen

    def __len__(self) -> int:
        return len(self._entries)


def parse_bearer(header: str | None) -> str | None:
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    tok = parts[1].strip()
    return tok or None


class Quota:
    """固定窗口配额。`take()` 返回 `None` 表示放行，否则返回「还差几秒」。"""

    def __init__(self, window_s: int = WINDOW_S, now=None):
        self.window_s = int(window_s)
        self._now = now or (lambda: time.time())
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, int], int] = {}

    def _slot(self, now: float) -> int:
        return int(now // self.window_s)

    def take(self, p: Principal, now: float | None = None) -> float | None:
        """消耗一次配额。超了**不消耗**（计数器不涨 —— 否则重试会把窗口越填越满）。"""
        limit = p.quota_per_min
        if limit is None or limit <= 0:        # `0` = 显式不限
            return None
        t = self._now() if now is None else now
        slot = self._slot(t)
        with self._lock:
            # 清掉上一窗口的桶 —— 不清就是**只增不减**的内存
            for key in [k for k in self._hits if k[1] < slot - 1]:
                del self._hits[key]
            key = (p.name, slot)
            used = self._hits.get(key, 0)
            if used >= limit:
                return max(1.0, self.window_s - (t - slot * self.window_s))
            self._hits[key] = used + 1
            return None

    def used(self, p: Principal, now: float | None = None) -> int:
        """本窗口已用几次（判据要能看见「拒绝有没有计数」）。"""
        t = self._now() if now is None else now
        with self._lock:
            return self._hits.get((p.name, self._slot(t)), 0)
