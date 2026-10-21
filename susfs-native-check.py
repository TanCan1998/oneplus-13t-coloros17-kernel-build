#!/usr/bin/env python3
"""适配源码门禁：只证明接线与基线保留，不替代编译和实机验收。"""
import hashlib
from pathlib import Path
import re

PRESERVE_KSU = [
    "kernel/feature/sucompat.c", "kernel/feature/sucompat.h",
    "kernel/feature/mount_hide.c", "kernel/feature/kernel_umount.c",
    "kernel/feature/selinux_hide.c", "kernel/feature/adb_root.c",
    "kernel/hook/syscall_event_bridge.c", "kernel/hook/syscall_hook_manager.c",
    "kernel/hook/tp_marker.c", "kernel/hook/lsm_hook.c",
    "kernel/hook/arm64/patch_memory.c", "kernel/hook/arm64/syscall_hook.c",
    "kernel/runtime/ksud_integration.c", "kernel/supercall/dispatch.c",
    "kernel/supercall/supercall.c", "kernel/policy/app_profile.h",
    "kernel/preinit/preinit.c", "kernel/extension/extension.c",
    "kernel/extension/kpm.c", "kernel/extension/cpu_spoof.c",
]
PRESERVE_COMMON = [
    "drivers/input/input.c", "fs/exec.c", "fs/open.c", "fs/read_write.c",
    "security/selinux/hooks.c", "security/selinux/selinuxfs.c",
    "security/selinux/ss/services.c",
]

def check(common, ksu, lock):
    common, ksu = Path(common), Path(ksu)
    checks = []
    def require(ok, message):
        if not ok:
            raise RuntimeError("源码门禁失败：" + message)
        checks.append(message)
    def read(tree, rel):
        return (tree / rel).read_text(encoding="utf-8")
    for name, tree, paths in [("ksu", ksu, PRESERVE_KSU), ("common", common, PRESERVE_COMMON)]:
        for rel in paths:
            expected = lock["sources"][name]["files"][rel]
            actual = hashlib.sha256((tree / rel).read_bytes()).hexdigest()
            require(actual == expected, "保留固定基线 " + name + "/" + rel)
    kbuild = read(ksu, "kernel/Kbuild")
    require("KSU_VERSION := 41028" in kbuild and "KSU_VERSION_FULL := native-susfs-7ea0dd2b" in kbuild,
            "版本号固定为已验证基线 41028，完整版本标记包含固定源码提交")
    require("KSU_PACKAGE_NAME := com.sukisu.ultra" in kbuild and "KSU_IS_PR_BUILD := 0" in kbuild,
            "在 Kbuild 内固定正式管理器包名，而非依赖跨步骤环境变量")
    require(not any(t in kbuild for t in ["GITHUB_API_BASE", "CURL_BIN", "GITHUB_COMMITS", "fetch --unshallow"]),
            "构建过程不查询移动分支、最新 Release 或隐式拉取历史")
    native = read(ksu, "kernel/feature/susfs_native.c")
    require("if (magic1 != KSU_INSTALL_MAGIC1)" in native and
            "magic2 == SUSFS_MAGIC && current_uid().val == 0" in native,
            "SUSFS 控制接口同时验证两组 magic 与全局 root UID")
    require("ksu_supercall_reboot_handler(arg)" not in native,
            "SUSFS 不接管现有 KSU fd 安装路径")
    require("sid2" in native and "if (!cred || !sid2)" in native,
            "尚未解析的零 SID 不产生域误匹配")
    for fn in ["susfs_is_current_ksu_domain", "susfs_is_current_init_domain",
               "susfs_is_current_zygote_domain", "susfs_is_current_zygote_next_domain",
               "susfs_set_batch_sid", "ksu_susfs_mark_setuid", "ksu_susfs_after_umount"]:
        require(re.search(r"\b" + fn + r"\([^;]*?\)\s*\{", native, re.S) is not None, "提供 native 桥接函数 " + fn)
    for rel, token in [
        ("kernel/core/init.c", "susfs_init();"),
        ("kernel/selinux/selinux.c", "susfs_set_batch_sid();"),
        ("kernel/runtime/boot_event.c", "susfs_start_sdcard_monitor_fn();"),
        ("kernel/hook/setuid_hook.c", "ksu_susfs_mark_setuid(old_uid, new_uid);"),
        ("kernel/hook/setuid_hook.c", "ksu_susfs_after_umount();"),
        ("kernel/Kbuild", "kernelsu-objs += feature/susfs_native.o"),
    ]:
        require(read(ksu, rel).count(token) == 1, "唯一接线 " + token)
    setuid = read(ksu, "kernel/hook/setuid_hook.c")
    require(setuid.index("ksu_handle_umount(old_uid, new_uid);") <
            setuid.index("ksu_susfs_after_umount();"), "额外 SUSFS 工作在既有卸载路径之后调度")
    require("int ksu_handle_setresuid(uid_t old_uid, uid_t new_uid)" in setuid,
            "保留现有二参数 setresuid 回调 ABI")
    require(read(ksu, "kernel/policy/app_profile.c").count("ksu_susfs_clear_current_flags();") == 2,
            "普通提权和 init 特例均清除继承的 SUSFS 标志")
    require("depends on KSU=y && 64BIT" in read(ksu, "kernel/Kconfig"),
            "仅支持本次内置 64 位构建模式")
    stat = read(common, "fs/stat.c")
    require(not any(t in stat for t in ["ksu_handle_stat(", "ksu_handle_vfs_fstat(", "__ksu_is_allow_uid_for_current"]),
            "保留 SUS_KSTAT、剔除不兼容的 root stat hook")
    require("ksu_handle_setresuid(" not in read(common, "kernel/sys.c"),
            "UID 切换只由已有 SukiSU hook 分发，避免重复卸载")
    rollup = read(common, "fs/proc/task_mmu.c")
    require(rollup.count("/* Case 4 above */") == 1 and
            "last_vma_end = vma->vm_end;\n\t\t\t\t}\n#else" in rollup,
            "smaps 使用当前 LTS 的唯一新版 Case 4 分支")
    require("\nelse\n" not in rollup and "#ifdef __fold_filemap_fixup_entry" not in rollup,
            "映射重定向沿用当前 OnePlus fold 修复调用，消除上游裸 else 和 SRCU 早退")
    require("#include <linux/susfs_def.h>" in read(common, "fs/proc/base.c"),
            "补齐 proc/base.c 所需 SUSFS 头文件")
    for rel, token in [
        ("fs/Makefile", "obj-$(CONFIG_KSU_SUSFS)"),
        ("kernel/reboot.c", "ksu_susfs_handle_reboot(magic1, magic2, cmd, &arg)"),
        ("fs/namei.c", "susfs_open_redirect"),
        ("fs/namespace.c", "susfs_get_non_sus_mnt_id_from_mnt"),
        ("fs/proc_namespace.c", "susfs_is_hide_sus_mnts_for_non_su_procs_enabled"),
        ("security/selinux/avc.c", "susfs_is_avc_log_spoofing_enabled"),
    ]:
        require(token in read(common, rel), "保留原生文件系统接线 " + rel)
    for rel in ["fs/susfs.c", "include/linux/susfs.h", "include/linux/susfs_def.h"]:
        digest = hashlib.sha256((common / rel).read_bytes()).hexdigest()
        expected = lock["sources"]["susfs"]["files"]["kernel_patches/" + rel]
        require(digest == expected, "原生 SUSFS 核心与固定上游一致 " + rel)
    return checks
