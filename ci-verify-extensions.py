#!/usr/bin/env python3
"""校验三项扩展是否真的编进了产物内核。

只读 System.map，不做任何写入。符号名以 Nova builtin 源码为准：
  * KPM：extension/kpm/kpm.c 的唯一外部入口 do_kpm()，以及 kpm.c 的扩展胶水；
  * 版本信息自定义：extension/version_spoof.c 的 io/wipe 入口；
  * AVC 日志伪装：feature/avc_spoof.c 的 init/late_init/enable，以及被 probe 的
    slow_avc_audit（必须是可解析符号，否则 register_kprobe 会失败）。

任何一项缺失都会失败——这些功能是本次构建的目的，不能静默丢失。
"""
import os
from pathlib import Path
import sys

ROOT = Path(os.environ.get("TREE_ROOT", "/mnt/work/official-13t"))
DIST = ROOT / "kernel_platform/out/msm-kernel-sun-perf/dist"
MAP = DIST / "System.map"

# (标签, 候选符号, 是否需要全部命中)
REQUIRED = [
    ("KPM 控制入口", ["do_kpm"], True),
    ("KPM 扩展胶水", ["ksu_extension_kpm_io", "ksu_extension_kpm_get"], True),
    ("版本信息自定义", ["ksu_extension_version_spoof_io"], True),
    ("版本信息自定义（清空）", ["ksu_extension_version_spoof_wipe"], True),
    ("AVC 日志伪装", ["ksu_avc_spoof_init", "ksu_avc_spoof_late_init"], True),
    ("AVC kprobe 目标符号", ["slow_avc_audit"], True),
]

def main() -> int:
    if not MAP.is_file():
        print("::error::缺少 System.map，无法校验扩展符号", file=sys.stderr)
        return 1
    symbols = set()
    for line in MAP.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 3:
            symbols.add(parts[2])
    if not symbols:
        print("::error::System.map 为空或格式异常", file=sys.stderr)
        return 1

    print("System.map 符号数:", len(symbols), flush=True)
    failures = []
    for label, wanted, need_all in REQUIRED:
        hits = [s for s in wanted if s in symbols]
        ok = len(hits) == len(wanted) if need_all else bool(hits)
        print(("OK   " if ok else "MISS ") + label + " -> " + (", ".join(hits) or "无"), flush=True)
        if not ok:
            failures.append(label)

    if failures:
        print("::error::扩展校验失败：" + "; ".join(failures), file=sys.stderr)
        return 1
    print("三项扩展符号校验通过（KPM / 版本信息自定义 / AVC 日志伪装）", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
