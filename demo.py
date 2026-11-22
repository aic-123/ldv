# -*- coding: utf-8 -*-
"""30 秒上手：克隆即可跑——演示语料随仓库走（_data/demo/nodes）。

四个例子对应 README「教学途径」里教出来的能力：
  ① 检索：带出处的定位
  ② 推算：340×3=1020，三个数三个出处
  ③ 对话：一句话进来，先认出这是哪类活（算术路由）
  ④ 装配单：目标判据逐条找规程，缺什么如实标缺
"""
import io, os, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from entry import App

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_data", "demo", "nodes")
app = App(root=root)

def show(title, spec, keys):
    r = app.query(spec)
    print(f"—— {title} ——")
    for k in keys:
        if r.get(k) is not None:
            print(f"   {k}: {str(r[k])[:200]}")
    print()

show("① 检索：带出处的定位",
     {"require": ["name=声速"]}, ["答案", "answer_titles"])

show("② 推算：三个数三个出处",
     {"derive": {"运算": "*", "左": "name=声速", "右": "name=三", "字段": "数值"}},
     ["算式", "算得", "依据"])

show("③ 对话：一句话进来，先认出是哪类活",
     {"对话": {"说": "声速乘三等于几？"}}, ["路由", "答"])

show("④ 装配单：判据逐条找规程，缺什么如实标缺",
     {"装配": {"名称": "订单退款空结果审计"}},
     ["装配单"])
