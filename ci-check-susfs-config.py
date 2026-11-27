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
# Nova builtin 的 Kconfig 没有 KSU_MANUAL_SU（Ultra 独有），不列入。
required = ["KSU", "KSU_SUSFS", "KSU_SUSFS_SUS_PATH", "KSU_SUSFS_SUS_MOUNT",
    "KSU_SUSFS_SUS_KSTAT", "KSU_SUSFS_SPOOF_UNAME", "KSU_SUSFS_ENABLE_LOG",
    "KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS", "KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG",
    "KSU_SUSFS_OPEN_REDIRECT", "KSU_SUSFS_SUS_MAP", "KPROBES", "THREAD_INFO_IN_TASK", "64BIT",
    "KPM", "KALLSYMS", "KALLSYMS_ALL", "SECURITY_SELINUX", "AUDIT"]
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
    # 扩展功能熔断：这些关系一旦被破坏，编译能过但功能不可用，
    # 或运行期直接失效；放在编译前 5 秒判死，避免白跑一小时。
    #   KPM 在 nova-new/kernel/Kconfig 里 select KALLSYMS/KALLSYMS_ALL，
    #   运行期用 find_kernel_symbol_exact("__arm64_sys_truncate") 找入口，
    #   而 CFI 会把该符号改名为 .cfi_jt，故 CFI 必须为 y。
    must = ["CONFIG_KALLSYMS=y", "CONFIG_KALLSYMS_ALL=y", "CONFIG_CFI_CLANG=y",
            "CONFIG_KPM=y", "CONFIG_KSU_SUSFS=y"]
    lost = [k for k in must if k not in lines]
    if lost:
        raise RuntimeError(name + " 扩展依赖被破坏：" + ", ".join(lost))
    # 诊断：KPM 在 KConfig 里 depends on KSU && 64BIT，任一为 n 时该行会被丢弃。
    diag = {k: ("CONFIG_" + k + "=y" in lines) for k in
            ("KSU", "64BIT", "KPM", "KALLSYMS", "KALLSYMS_ALL", "KPROBES", "THREAD_INFO_IN_TASK")}
    print(name, "关键符号:", diag, flush=True)
    shutil.copy2(path, "/mnt/work/config-" + name + ".txt")
    print(name, "配置门禁通过：", len(required), "项为 y，MODULE_SIG_PROTECT 未开启", flush=True)
