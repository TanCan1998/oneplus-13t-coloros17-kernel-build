#!/usr/bin/env python3
"""适配源码门禁：只证明接线与基线保留，不替代编译和实机验收。

Nova 版相对 Ultra 版的差异：
  * KSU 侧基线按 Nova builtin 的文件清单校验；
  * 不再断言 Ultra 独有的 susfs_native.c / Kbuild 版本钉；
  * 改为断言 Nova 自带的三条 SUSFS 接线（dispatch/selinux/lsm_hook/ksu_main）；
  * 额外断言本次启用的三项扩展（KPM、版本伪装、AVC 伪装）源码与接线齐全。
"""
import hashlib
from pathlib import Path
import re

# Nova builtin 里必须保持原样的关键文件（去掉本次补丁涉及的三个）。
PRESERVE_KSU = [
    "kernel/feature/sucompat.c", "kernel/feature/sucompat.h",
    "kernel/feature/mount_hide.c", "kernel/feature/kernel_umount.c",
    "kernel/feature/selinux_hide.c", "kernel/feature/adb_root.c",
    "kernel/hook/lsm_hook.c",
    "kernel/supercall/dispatch.c", "kernel/supercall/supercall.c",
    "kernel/policy/app_profile.h", "kernel/preinit/preinit.c",
    "kernel/extension/extension.c", "kernel/extension/kpm.c",
    "kernel/extension/cpu_spoof.c", "kernel/selinux/selinux.c",
    "kernel/extension/susfs_extension.c",
    # kernel/runtime/ksud.c 已由 patches/nova/ksu-avc-kpm.patch 修改（AVC late_init），
    # 不再作为不可改基线；其 AVC 接线由下方专门断言覆盖。
]
PRESERVE_COMMON = [
    "drivers/input/input.c", "fs/exec.c", "fs/open.c", "fs/read_write.c",
    "security/selinux/hooks.c", "security/selinux/selinuxfs.c",
    "security/selinux/ss/services.c",
]

def check(common, ksu, lock, common_key="common"):
    """common_tree 可以是 common 或 msm-kernel：两者均为完整内核树。"""
    common, ksu = Path(common), Path(ksu)
    checks = []
    def require(ok, message):
        if not ok:
            raise RuntimeError("源码门禁失败：" + message)
        checks.append(message)
    def read(tree, rel):
        return (tree / rel).read_text(encoding="utf-8")
    for name, tree, paths in [("ksu", ksu, PRESERVE_KSU), (common_key, common, PRESERVE_COMMON)]:
        for rel in paths:
            expected = lock["sources"][name]["files"][rel]
            actual = hashlib.sha256((tree / rel).read_bytes()).hexdigest()
            require(actual == expected, "保留固定基线 " + name + "/" + rel)

    # ---- Nova 上游钉：不查询网络、包名固定 ----
    makefile = read(ksu, "kernel/Makefile")
    require("KSU_PACKAGE_NAME ?= com.sukisu.nova" in makefile,
            "上游 Makefile 固定 Nova 正式包名")
    require("REPO_BRANCH := main" in makefile and "GITHUB_COMMITS" in makefile,
            "确认上游版本号仍由仓库提交数推导，而非本地伪造")

    # ---- Nova 自带的三条 SUSFS 接线 ----
    dispatch = read(ksu, "kernel/supercall/dispatch.c")
    require("int ksu_handle_sys_reboot(int magic1, int magic2, unsigned int cmd, void __user **arg)" in dispatch,
            "Nova 在 supercall/dispatch.c 提供 SUSFS reboot 入口")
    require("magic2 == SUSFS_MAGIC && current_uid().val == 0" in dispatch,
            "SUSFS 控制接口同时验证两组 magic 与全局 root UID")
    require("CMD_SUSFS_SHOW_VERSION" in dispatch,
            "SUSFS 版本查询命令仍在默认分支内")
    selinux = read(ksu, "kernel/selinux/selinux.c")
    for fn in ["susfs_is_current_ksu_domain", "susfs_is_current_init_domain",
               "susfs_is_current_zygote_domain", "susfs_is_current_zygote_next_domain",
               "susfs_set_batch_sid"]:
        require(re.search(r"\b" + fn + r"\([^;]*?\)\s*\{", selinux, re.S) is not None,
                "提供 SUSFS 域判定函数 " + fn)
    lsm = read(ksu, "kernel/hook/lsm_hook.c")
    require("susfs_set_current_proc_umounted_for_zygote_next();" in lsm,
            "zygote next 域切换仍走 SUSFS 卸载标记")
    main = read(ksu, "kernel/ksu_main.c")
    require(main.count("susfs_init();") == 1, "唯一 susfs_init 调用点")
    require(main.count('"feature/avc_spoof.c"') == 1, "AVC 伪装源码进入 unity build")
    require(main.count("ksu_avc_spoof_init();") == 1, "AVC 伪装初始化接入内核启动")

    # ---- 本次启用的三项扩展 ----
    kconfig = read(ksu, "kernel/Kconfig")
    require(re.search(r"^config KPM$", kconfig, re.M) is not None, "KPM 配置项存在")
    require(re.search(r"^config KPM\n(?:.*\n)*?\s+default y", kconfig, re.M) is not None,
            "KPM 默认 y（不改 defconfig，避免 Klean savedefconfig 校验失败）")
    require(re.search(r"^config KSU_SUSFS$", kconfig, re.M) is not None, "SUSFS 配置项存在")
    compat = read(ksu, "kernel/kernel_compat.h")
    require("define KSU_CAN_USE_KPM" in compat, "6.6 内核满足 KPM 编译条件")
    kpm = read(ksu, "kernel/extension/kpm/kpm.c")
    require("kpm_install" in kpm or "do_kpm" in kpm, "KPM 装载实现存在")
    version = read(ksu, "kernel/extension/version_spoof.c")
    require("KSU_VERSION_SPOOF_IO_SET" in version, "版本伪装支持运行时写入")
    require("KSU_VERSION_SPOOF_FULL_VERSION" in version, "版本伪装覆盖完整版本串")
    ext = read(ksu, "kernel/extension/extension.c")
    require('"version_spoof"' in ext and "KSU_EXTENSION_VERSION_SPOOF" in ext,
            "版本伪装扩展已注册")
    avc = read(ksu, "kernel/feature/avc_spoof.c")
    require('register_kprobe' in avc and '"slow_avc_audit"' in avc,
            "AVC 伪装通过 slow_avc_audit kprobe 生效")
    require("CONFIG_KPROBES" in avc, "AVC 伪装对无 KPROBES 内核有编译期保护")
    feature_uapi = read(ksu, "kernel/include/uapi/feature.h")
    require("KSU_FEATURE_AVC_SPOOF = 10003" in feature_uapi,
            "AVC 功能 ID 与 Nova 主线一致")
    feature_c = read(ksu, "kernel/policy/feature.c")
    require('[KSU_FEATURE_AVC_SPOOF] = "avc_spoof"' in feature_c,
            "AVC 功能名进入内核注册表")
    ksud = read(ksu, "kernel/runtime/ksud.c")
    require("ksu_avc_spoof_late_init();" in ksud,
            "AVC 伪装在 boot_completed 后安装 kprobe")

    # ---- 内核侧原生接线 ----
    require("depends on KSU && 64BIT" in kconfig,
            "Kconfig 依赖语法与 Nova 一致（KPM 需要 64 位）")
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
        ("kernel/reboot.c", "ksu_handle_sys_reboot(magic1, magic2, cmd, &arg)"),
        ("fs/namei.c", "susfs_open_redirect"),
        ("fs/namespace.c", "susfs_get_non_sus_mnt_id_from_mnt"),
        ("fs/proc_namespace.c", "susfs_is_hide_sus_mnts_for_non_su_procs_enabled"),
        ("security/selinux/avc.c", "susfs_is_avc_log_spoofing_enabled"),
    ]:
        require(token in read(common, rel), "保留原生文件系统接线 " + rel)
    for rel in ["include/linux/susfs.h", "include/linux/susfs_def.h"]:
        digest = hashlib.sha256((common / rel).read_bytes()).hexdigest()
        expected = lock["sources"]["susfs"]["files"]["kernel_patches/" + rel]
        require(digest == expected, "原生 SUSFS 核心与固定上游一致 " + rel)
    raw = read(common, "fs/susfs.c")
    m = re.search(r"\n/\* \(sb, i_ino\) 索引：.*?\n\treturn ret;\n\}", raw, re.S)
    require(m is not None, "fs/susfs.c 含已批准的 (sb,i_ino) 索引增量块")
    stripped = raw[:m.start()] + raw[m.end():]
    require(stripped.count("#include <linux/hashtable.h>\n") == 1, "fs/susfs.c 仅新增 hashtable 头一次")
    stripped = stripped.replace("#include <linux/hashtable.h>\n", "", 1)
    require(stripped.count("\t\tsusfs_add_sus_ino(inode);\n") == 4, "四处 sus_path 登记点齐全")
    stripped = stripped.replace("\t\tsusfs_add_sus_ino(inode);\n", "")
    expected_susfs = lock["sources"]["susfs"]["files"]["kernel_patches/fs/susfs.c"]
    require(hashlib.sha256(stripped.encode()).hexdigest() == expected_susfs,
            "剥离已批准增量后，原生 SUSFS 核心仍与固定上游一致 fs/susfs.c")
    rd = read(common, "fs/readdir.c")
    require(rd.count("\tif (susfs_is_sus_ino(buf->sb, ino)) {") == 5,
            "readdir 五处 getdents 回调均先查 (sb,i_ino) 索引")
    require("extern bool susfs_is_sus_ino(struct super_block *sb, unsigned long ino);" in rd,
            "readdir 声明 (sb,i_ino) 索引接口")
    return checks
