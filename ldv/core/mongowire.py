# -*- coding: utf-8 -*-
"""mongowire.py —— MongoDB 线协议最小实现（LDV 自己开端口，不用驱动）

只做"说话"这一层：
  编：dict → BSON → OP_MSG（opcode 2013）
  解：OP_MSG → BSON → dict（含 kind 1 文档序列——find 的 firstBatch 在这）
不做的：连接池、认证、游标续取（getMore）。够 LDV 读写账本就够。

协议要点（教在《MongoDB 端口协议》conc/396 里）：
  报文头 16 字节 = 长度 int32 / 请求号 int32 / 响应号 int32 / 操作码 int32
  OP_MSG = flagBits int32 + 若干 section：
           kind 0 = 一个 BSON 文档（命令本体）
           kind 1 = 文档序列（大结果集用，identifier 指明塞给哪个字段）
  每个请求**自带 "$db"**——协议里没有"先 use 库"这一步。
"""
from __future__ import annotations

import socket
import struct

OP_MSG = 2013

# BSON 类型标记（只实现 LDV 用得上的那些）
T_DOUBLE = 0x01
T_STRING = 0x02
T_DOC = 0x03
T_ARRAY = 0x04
T_BINARY = 0x05
T_OID = 0x07
T_BOOL = 0x08
T_DATE = 0x09
T_NULL = 0x0A
T_INT32 = 0x10
T_TIMESTAMP = 0x11
T_INT64 = 0x12
T_DEC128 = 0x13


# ── BSON 编码 ─────────────────────────────────────────────────────────
def _i32(v: int) -> bytes:
    return struct.pack("<i", v)


def _cstr(s: str) -> bytes:
    return str(s).encode("utf-8") + b"\x00"


def _elem(key: str, v) -> bytes:
    k = _cstr(key)
    if isinstance(v, bool):                      # bool 必须先判（它是 int 子类）
        return b"\x08" + k + (b"\x01" if v else b"\x00")
    if isinstance(v, int):
        if -(2 ** 31) <= v < 2 ** 31:
            return b"\x10" + k + _i32(v)
        return b"\x12" + k + struct.pack("<q", v)
    if isinstance(v, float):
        return b"\x01" + k + struct.pack("<d", v)
    if isinstance(v, str):
        raw = v.encode("utf-8") + b"\x00"
        return b"\x02" + k + _i32(len(raw)) + raw
    if isinstance(v, dict):
        return b"\x03" + k + encode(v)
    if isinstance(v, (list, tuple)):
        return b"\x04" + k + encode({str(i): x for i, x in enumerate(v)})
    if v is None:
        return b"\x0a" + k
    raise TypeError(f"BSON 编不了这个类型：{type(v).__name__}（键 {key}）")


def encode(doc: dict) -> bytes:
    body = b"".join(_elem(k, v) for k, v in doc.items())
    return _i32(len(body) + 5) + body + b"\x00"


# ── BSON 解码 ─────────────────────────────────────────────────────────
def _val(t: int, buf: bytes, p: int):
    if t == T_DOUBLE:
        return struct.unpack_from("<d", buf, p)[0], p + 8
    if t == T_STRING:
        (n,) = struct.unpack_from("<i", buf, p)
        s = buf[p + 4:p + 4 + n - 1].decode("utf-8", "replace")
        return s, p + 4 + n
    if t == T_DOC:
        return decode(buf, p)
    if t == T_ARRAY:
        d, q = decode(buf, p)
        return [d[k] for k in sorted(d, key=lambda x: int(x))], q
    if t == T_BINARY:
        (n,) = struct.unpack_from("<i", buf, p)
        return {"$binary": buf[p + 5:p + 5 + n].hex()}, p + 5 + n
    if t == T_OID:
        return {"$oid": buf[p:p + 12].hex()}, p + 12
    if t == T_BOOL:
        return buf[p] != 0, p + 1
    if t == T_DATE:
        return {"$date": struct.unpack_from("<q", buf, p)[0]}, p + 8
    if t == T_NULL:
        return None, p
    if t == T_INT32:
        return struct.unpack_from("<i", buf, p)[0], p + 4
    if t == T_TIMESTAMP:
        return {"$ts": struct.unpack_from("<Q", buf, p)[0]}, p + 8
    if t == T_INT64:
        return struct.unpack_from("<q", buf, p)[0], p + 8
    if t == T_DEC128:
        return {"$dec128": buf[p:p + 16].hex()}, p + 16
    raise ValueError(f"不认识的 BSON 类型标记 0x{t:02x}（位置 {p}）")


def decode(buf: bytes, pos: int = 0):
    (size,) = struct.unpack_from("<i", buf, pos)
    end = pos + size
    p = pos + 4
    out = {}
    while p < end - 1:
        t = buf[p]
        p += 1
        z = buf.index(b"\x00", p)
        k = buf[p:z].decode("utf-8", "replace")
        p = z + 1
        v, p = _val(t, buf, p)
        out[k] = v
    return out, end


# ── OP_MSG 收发 ───────────────────────────────────────────────────────
def _recv_exact(sock, n: int) -> bytes:
    got = b""
    while len(got) < n:
        piece = sock.recv(n - len(got))
        if not piece:
            raise ConnectionError("对方在报文没发完时关了连接")
        got += piece
    return got


def _parse_reply(buf: bytes) -> dict:
    length, _req, _resp, opcode = struct.unpack_from("<iiii", buf, 0)
    if opcode != OP_MSG:
        raise ValueError(f"期望 OP_MSG(2013)，收到 {opcode}")
    flags = struct.unpack_from("<I", buf, 16)[0]
    p = 20
    doc = None
    seqs = []
    while p < length:
        kind = buf[p]
        p += 1
        if kind == 0:
            doc, p = decode(buf, p)
        elif kind == 1:
            sec_start = p
            (sec_size,) = struct.unpack_from("<i", buf, sec_start)
            sec_end = sec_start + sec_size
            q = sec_start + 4
            z = buf.index(b"\x00", q)
            ident = buf[q:z].decode("utf-8", "replace")
            q = z + 1
            items = []
            while q < sec_end:
                d, q = decode(buf, q)
                items.append(d)
            seqs.append({"字段": ident, "文档": items})
            p = sec_end
        else:
            break
    out = {"文档": doc or {}, "序列": seqs, "flagBits": flags}
    cur = (doc or {}).get("cursor") if isinstance(doc, dict) else None
    if isinstance(cur, dict):
        nid = cur.get("id")
        if nid not in (None, 0, "0"):
            # 服务端还留着游标 = 还有没取完的批次——如实标出来，别让调用方以为拿全了
            out["未取完"] = True
            out["游标"] = str(nid)
            out["已取"] = len(cur.get("firstBatch") or [])
    # kind 1 的文档序列是对命令某个字段（如 cursor.firstBatch）的分块续写——
    # 原样留在"序列"里，由调用方按 identifier 取，不在这里猜着合并。
    return out


def command(doc: dict, host: str = "127.0.0.1", port: int = 27017,
            timeout: float = 4.0, req_id: int = 1) -> dict:
    """发一条命令，返回 {"文档": 回执, "序列": [...]}。"""
    body = struct.pack("<I", 0) + b"\x00" + encode(doc)   # flagBits + kind0 + 命令
    # 变量名不能用 total —— B12（ldv/checks/source.py）按名字禁
    # score/objective/global/importance/total 这类"全局评分"词，这里只是
    # 报文字节数，换个名字语义更准，也不必去放宽判据。
    msg_len = 16 + len(body)
    msg = struct.pack("<iiii", msg_len, req_id, 0, OP_MSG) + body
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(msg)
        head = _recv_exact(s, 4)
        (length,) = struct.unpack("<i", head)
        rest = _recv_exact(s, length - 4)
    return _parse_reply(head + rest)


# ── 命令糖（LDV 只会用这几个）─────────────────────────────────────────
def hello(host="127.0.0.1", port=27017, timeout=4.0) -> dict:
    return command({"hello": 1, "$db": "admin"}, host, port, timeout)


def ping(host="127.0.0.1", port=27017, timeout=4.0) -> dict:
    return command({"ping": 1, "$db": "admin"}, host, port, timeout)


def find(coll: str, flt: dict | None = None, limit: int = 10,
         db: str = "lamp_oil", host="127.0.0.1", port=27017,
         timeout=4.0, projection: dict | None = None,
         sort: dict | None = None) -> dict:
    # batchSize 必须跟着 limit：默认 batchSize 是 101，singleBatch=true 时
    # 服务端不建游标，**超出 101 的部分直接丢**（实测 200 条只回 101 条，不报错）。
    cmd = {"find": coll, "filter": flt or {}, "limit": int(limit),
           "batchSize": int(limit), "singleBatch": True, "$db": db}
    if projection:
        cmd["projection"] = projection
    if sort:
        cmd["sort"] = sort
    return command(cmd, host, port, timeout)


def aggregate(coll: str, pipeline: list, db: str = "lamp_oil",
              host="127.0.0.1", port=27017, timeout=6.0) -> dict:
    # aggregate 必须带 cursor 字段——3.6 起是硬要求，不带对方直接拒
    # 同理：cursor 里给大 batchSize，否则聚合结果也会被 101 截断
    return command({"aggregate": coll, "pipeline": list(pipeline),
                    "cursor": {"batchSize": 1000}, "$db": db},
                   host, port, timeout)


def count(coll: str, flt: dict | None = None, db: str = "lamp_oil",
          host="127.0.0.1", port=27017, timeout=4.0) -> dict:
    return command({"count": coll, "query": flt or {}, "$db": db},
                   host, port, timeout)


def update(coll: str, flt: dict, sets: dict, db: str = "lamp_oil",
           host="127.0.0.1", port=27017, timeout=4.0, many: bool = False) -> dict:
    """改状态类字段用；业务字段禁改由路由层把关（conc/364 同款）。"""
    return command({"update": coll,
                    "updates": [{"q": flt or {}, "u": {"$set": sets},
                                 "multi": bool(many)}],
                    "$db": db}, host, port, timeout)


def ensure_index(coll: str, keys: dict, db: str = "lamp_oil", host="127.0.0.1",
                 port=27017, timeout=4.0, unique: bool = False,
                 name: str | None = None) -> dict:
    """建索引（幂等）。**计数器的原子性靠它兜底**：

    没有唯一索引时，两个并发的 upsert 都可能"查不到→插入"，于是同一个人
    建出两条计数账，各自记各的数（实测：4 条借出、计数账 [3,1]）。
    唯一键把"只能有一条"这件事交给库来保证，不靠我先查一遍。
    """
    return command({"createIndexes": coll,
                    "indexes": [{"key": dict(keys),
                                 "name": name or ("uk_" + "_".join(keys)),
                                 "unique": bool(unique)}],
                    "$db": db}, host, port, timeout)


def find_and_modify(coll: str, flt: dict, update: dict, db: str = "lamp_oil",
                    host="127.0.0.1", port=27017, timeout=4.0,
                    new: bool = True, upsert: bool = False) -> dict:
    """带条件的原子改（CAS）——《并发控制规程》conc/410 的可执行形态。

    条件（query）和改动（update）**一次交给服务端**：命中就改，不命中就一条都不动。
    回执里 `value` 有文档 = 改到了（抢到）；为 None = 没抢到。
    **这一条报文就是原子性的来源**——不是本地加锁，是服务端一次判一次做。
    """
    return command({"findAndModify": coll, "query": flt or {},
                    "update": update or {}, "new": bool(new),
                    "upsert": bool(upsert), "$db": db}, host, port, timeout)


def insert(coll: str, docs: list, db: str = "lamp_oil", host="127.0.0.1",
           port=27017, timeout=4.0) -> dict:
    return command({"insert": coll, "documents": docs, "ordered": True, "$db": db},
                   host, port, timeout)
