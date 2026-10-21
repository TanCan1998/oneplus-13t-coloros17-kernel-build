#!/usr/bin/env python3
"""分别核验 common Image 和 sun_perf 的实际生成配置。"""
import os
from pathlib import Path
import shutil

platform = Path(os.environ["TREE_ROOT"]) / "kernel_platform"
targets = {
    "common": platform / "bazel-bin/common/kernel_aarch64_config/out_dir/.config",
    "msm-kernel": platform / "bazel-bin/msm-kernel/sun_perf_config/out_dir/.config",
}
required = ["KSU", "KSU_MANUAL_SU", "KSU_SUSFS", "KSU_SUSFS_SUS_PATH", "KSU_SUSFS_SUS_MOUNT",
    "KSU_SUSFS_SUS_KSTAT", "KSU_SUSFS_SPOOF_UNAME", "KSU_SUSFS_ENABLE_LOG",
    "KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS", "KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG",
    "KSU_SUSFS_OPEN_REDIRECT", "KSU_SUSFS_SUS_MAP", "KPROBES", "THREAD_INFO_IN_TASK", "64BIT"]
for name, path in targets.items():
    if not path.is_file():
        raise RuntimeError("没有找到指定目标配置：" + str(path))
    text = path.read_text()
    lines = set(text.splitlines())
    missing = ["CONFIG_" + key + "=y" for key in required if "CONFIG_" + key + "=y" not in lines]
    if missing:
        raise RuntimeError(name + " 未满足配置：" + ", ".join(missing))
    if "CONFIG_MODULE_SIG_PROTECT=y" in lines:
        raise RuntimeError(name + " 的导出保护仍开启")
    shutil.copy2(path, "/mnt/work/config-" + name + ".txt")
    print(name, "配置门禁通过：", len(required), "项为 y，MODULE_SIG_PROTECT 未开启", flush=True)
