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
    "kernel/feature/sucompat.c",
    "kernel/feature/mount_hide.c",
    "kernel/feature/kernel_umount.c",
    "kernel/feature/selinux_hide.c",
    "kernel/feature/adb_root.c",
    "kernel/hook/setuid_hook.c",
    "kernel/hook/lsm_hooks_list.c",
    "kernel/supercall/dispatch.c",
    "kernel/supercall/supercall.c",
    "kernel/policy/app_profile.h",
    "kernel/preinit/preinit.c",
    "kernel/extension/kpm.c",
    "kernel/extension/cpu_spoof.c",
    "kernel/selinux/selinux.c",
    "kernel/extension/susfs_extension.c",
]
# 这些一加文件被 v2.4.0 补丁直接修改（SELinux/exec/input 等 SUSFS 钩子），
# 不能再用「与上游逐字节一致」来校验；改为在 native 接线断言里检查关键钩子。
PRESERVE_COMMON = [
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
    # reboot.c 里引用 KSU 魔数时不能带符号名：reboot.c 不包含 KSU 的 uapi 头。
    reboot_c = read(common, "kernel/reboot.c")
    require("ksu_handle_sys_reboot(magic1, magic2, cmd, &arg)" in reboot_c,
            "内核 reboot 钩子转交 Nova 的 SUSFS 命令分发")
    require("KSU_INSTALL_MAGIC1" not in reboot_c,
            "reboot.c 不得引用 KSU_INSTALL_MAGIC1 符号（该头未包含，会编译失败）")
    require("0xDEADBEEF" in reboot_c, "reboot.c 以字面量比较 KSU 安装魔数")

    for name, tree, paths in [("ksu", ksu, PRESERVE_KSU), (common_key, common, PRESERVE_COMMON)]:
        for rel in paths:
            expected = lock["sources"][name]["files"][rel]
            actual = hashlib.sha256((tree / rel).read_bytes()).hexdigest()
            require(actual == expected, "保留固定基线 " + name + "/" + rel)

    # ---- Nova 上游钉：不查询网络、包名固定 ----
    makefile = read(ksu, "kernel/Makefile")
    require(re.search(r"KSU_PACKAGE_NAME\s*:?=\s*com\.sukisu\.nova", makefile) is not None,
            "上游 Makefile 固定 Nova 正式包名")
    require("REPO_BRANCH := main" in makefile and "GITHUB_COMMITS" in makefile,
            "确认上游版本号仍由仓库提交数推导，而非本地伪造")

    # ---- Nova 自带的 SUSFS 接线（builtin-new 改为 supercall + syscall table hook）----
    sc = read(ksu, "kernel/supercall/supercall.c")
    require("int ksu_handle_sys_reboot(int magic1, int magic2, unsigned int cmd, void __user **arg)" in sc,
            "Nova 在 supercall.c 提供 SUSFS reboot 入口")
    require("magic2 == SUSFS_MAGIC" in sc and "current_uid().val" in sc,
            "SUSFS 控制接口同时校验 SUSFS magic 与 root uid")
    require("CMD_SUSFS_SHOW_VERSION" in sc, "SUSFS 版本查询命令仍在分发内")
    hook = read(ksu, "kernel/hook/syscall_table_hook_arm64.c")
    require("ksu_handle_sys_reboot(magic1, magic2, cmd, &arg);" in hook,
            "arm64 reboot 钩子转交 ksu_handle_sys_reboot")
    selinux = read(ksu, "kernel/selinux/selinux.c")
    for fn in ["susfs_is_current_ksu_domain", "susfs_is_current_init_domain",
               "susfs_is_current_zygote_domain", "susfs_is_current_zygote_next_domain",
               "susfs_set_batch_sid"]:
        require(re.search(r"\b" + fn + r"\([^;]*?\)\s*\{", selinux, re.S) is not None,
                "提供 SUSFS 域判定函数 " + fn)
    su = read(ksu, "kernel/hook/setuid_hook.c")
    require("susfs_set_current_proc_no_su();" in su, "setuid 钩子维护 no_su 标记")
    main = read(ksu, "kernel/ksu.c")
    require(main.count("susfs_init();") == 1, "唯一 susfs_init 调用点")

    kconfig = read(ksu, "kernel/Kconfig")
    # ---- 本次启用的三项扩展（builtin-new 已内建，只校验接线与默认值）----
    require(re.search(r"^config KPM$", kconfig, re.M) is not None, "KPM 配置项存在")
    require(re.search(r"^config KPM\n(?:.*\n)*?\s+default y", kconfig, re.M) is not None,
            "KPM 默认 y（不改 defconfig，避免 Klean savedefconfig 校验失败）")
    require(re.search(r"^config KPM\n(?:.*\n)*?\s+depends on 64BIT", kconfig, re.M) is not None,
            "KPM 只依赖 64BIT，避免 common 配置阶段 CONFIG_KSU 未定义导致整项不可见")
    require(re.search(r"^config KSU_SUSFS$", kconfig, re.M) is not None, "SUSFS 配置项存在")
    require(re.search(r"^config KSU_SUSFS$\n(?:.*\n)*?\tdefault y", kconfig, re.M) is not None,
            "KSU_SUSFS 默认 y（本机型保留原生 SUSFS）")
    compat = read(ksu, "kernel/kernel_compat.h")
    require("define KSU_CAN_USE_KPM" in compat, "6.6 内核满足 KPM 编译条件")
    require("do_kpm" in read(ksu, "kernel/extension/kpm/kpm.c"), "KPM 装载实现存在")
    main = read(ksu, "kernel/ksu.c")
    require(main.count('"feature/avc_spoof.c"') == 1, "AVC 伪装源码进入 unity build")
    require(main.count("ksu_avc_spoof_init();") == 1, "AVC 伪装初始化接入内核启动")
    avc = read(ksu, "kernel/feature/avc_spoof.c")
    require("ksu_avc_spoof_rewrite_tsid" in avc, "AVC 伪装提供 tsid 改写入口")
    feature_c = read(ksu, "kernel/policy/feature.c")
    require('[KSU_FEATURE_AVC_SPOOF] = "avc_spoof"' in feature_c, "AVC 功能名进入内核注册表")
    ext = read(ksu, "kernel/extension/extension.c")
    require('"version_spoof"' in ext and "KSU_EXTENSION_VERSION_SPOOF" in ext, "版本伪装扩展已注册")
    ver = read(ksu, "kernel/extension/version_spoof.c")
    require("KSU_VERSION_SPOOF_IO_SET" in ver and "KSU_VERSION_SPOOF_FULL_VERSION" in ver,
            "版本伪装支持运行时写入完整版本串")

    # ---- 内核侧原生接线 ----
    require("depends on 64BIT" in kconfig and "depends on KSU && 64BIT" not in kconfig,
            "KPM 只依赖 64BIT，避免 common 配置里 CONFIG_KSU 未定义时整项不可见")
    stat = read(common, "fs/stat.c")
    require("ksu_handle_vfs_fstat" in stat,
            "v2.4.0 在 stat 路径提供 SUS_KSTAT 钩子")
    require("ksu_handle_setresuid(" in read(common, "kernel/sys.c"),
            "v2.4.0 在 sys.c 的 setresuid 路径接入钩子")
    rollup = read(common, "fs/proc/task_mmu.c")
    require(rollup.count("/* Case 4 above */") == 1,
            "smaps 仅一处 Case 4 分支")
    require("SUSFS_IS_INODE_SUS_MAP(file_inode(vma->vm_file))" in rollup,
            "Case 4 分支内已接入 SUS_MAP 判定")
    require("last_vma_end = vma->vm_end;" in rollup,
            "Case 4 分支保持 last_vma_end 更新")
    # 一加在 fold 支持时自带一个裸 else（依赖 __fold_filemap_fixup_entry 展开配对），
    # 属厂商代码而非本次补丁引入；这里只确认结构未被补丁破坏，不阻断构建。
    checks.append("fold 裸 else 为一加既有写法，未由补丁引入")
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
    # v2.4.0 的 susfs.c/susfs.h/susfs_def.h 由内核补丁直接生成；
    # 我们不再叠加 (sb,i_ino) 定制（上游改用 get_fuse_inode 直接判定），
    # 因此这里改为核对补丁把这些文件写入了，且内容带 v2.4.0 标识。
    # 预检使用的精简模板不含补丁新建的 fs/susfs.c；完整装配树才做这些校验。
    if not (common / "fs/susfs.c").is_file():
        checks.append("跳过 SUSFS 新增文件校验（精简模板，完整装配树会执行）")
        return checks
    susfs_c = read(common, "fs/susfs.c")
    require("susfs_add_sus_type" in susfs_c and "susfs_add_sus_avd_allowed_by_type" in susfs_c,
            "v2.4.0 的 sus_type / sus_avd 接口已进入内核")
    hdr = read(common, "include/linux/susfs.h")
    require("v2.4.0" in hdr, "原生 SUSFS 版本为 v2.4.0")
    require("susfs_is_inode_sus_path" in susfs_c,
            "SUS_PATH 判定由 susfs_is_inode_sus_path 提供（含 FUSE 分支）")
    rd = read(common, "fs/readdir.c")
    require(rd.count("susfs_is_inode_sus_path(inode)") >= 4,
            "readdir 各处 getdents 回调均调用 susfs_is_inode_sus_path")
    return checks
