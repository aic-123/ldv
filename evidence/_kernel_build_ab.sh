#!/usr/bin/env bash
# 建内核 A/B —— 全量 3907 项。三臂各跑 2 个**独立进程**（每进程 1 轮）。
#
# 为什么要独立进程：同进程内连跑多轮，后一轮比前一轮慢（分配器/页表累积），
# 800 项上量到过 2.01 / 2.78 / 3.17 的爬升。独立进程把这项伪影去掉。
#
# 为什么用 trap 还原：HEAD 那一臂要把 `reach.py` 换成旧版，
# 脚本被中断时仓库不能停在旧版上。
set -u
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
ROOT="C:/Users/19253/WorkBuddy AI/2026-10-05-14-16-35"
P="C:/Users/19253/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe"
REACH="$ROOT/repo/ldv/plugins/reach.py"
NEW="$ROOT/outputs/_reach_new.py"

restore() {
  cp "$NEW" "$REACH"
  echo "[driver] 已还原新版 reach.py（$(wc -l < "$REACH") 行）"
}
trap restore EXIT INT TERM

cd "$ROOT"
echo "===== HEAD（改造前）====="
( cd repo && git show HEAD:ldv/plugins/reach.py > ldv/plugins/reach.py )
echo "[driver] HEAD 版（$(wc -l < "$REACH") 行）"
for i in 1 2; do
  LDV_CORPUS=openalex-citations "$P" outputs/_measure_kernel_build.py 3907 --arm=head --rounds=1
done

restore

echo "===== 新·缓存开 ====="
for i in 1 2; do
  LDV_CORPUS=openalex-citations "$P" outputs/_measure_kernel_build.py 3907 --arm=new-on --rounds=1
done

echo "===== 新·缓存关 ====="
for i in 1 2; do
  LDV_CORPUS=openalex-citations "$P" outputs/_measure_kernel_build.py 3907 --arm=new-off --rounds=1
done

echo "===== 完成 ====="
