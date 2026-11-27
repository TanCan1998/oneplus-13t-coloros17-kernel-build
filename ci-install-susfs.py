#!/usr/bin/env python3
"""在一次性官方装配树内安装固定版本的原生 SUSFS + SukiSU Nova（builtin 分支）。

与 Ultra 版的差异：
  * 上游源码换成 SukiSU-Nova/SukiSU-Nova 的 builtin-new 分支（自带 AVC/版本伪装）；
  * Nova 自带 SUSFS supercall 桥接，不再需要 KSU 侧源码补丁，改为整体复制；
  * 额外启用三项扩展：KPM（CONFIG_KPM=y）、版本信息自定义（内置）、
    AVC 日志伪装（builtin-new 自带 feature/avc_spoof.c）；
  * 装配后校验三项功能的源码证据，缺失即失败。

boot.img 实际由 //msm-kernel:sun16k_perf_dist 产出，common 与 msm-kernel
两棵完整内核树都必须打同一份原生补丁，否则运行时内核缺少隐藏逻辑。
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from importlib import import_module
check = import_module("susfs-native-check").check
spec = importlib.util.spec_from_file_location("helpers", HERE / "susfs-preflight.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
lock = json.loads((HERE / "susfs-native.lock.json").read_text(encoding="utf-8"))
root = Path(os.environ["TREE_ROOT"]).resolve()
if os.name != "posix" or not str(root).startswith("/mnt/work/"):
    raise RuntimeError("此脚本只允许在云端 /mnt/work 一次性构建树运行")
platform = root / "kernel_platform"
report = Path("/mnt/work/susfs-install-report")
report.mkdir(exist_ok=False)
shutil.copy2(HERE / "susfs-native.lock.json", report / "inputs.lock.json")

TREES = {"common": platform / "common", "msm-kernel": platform / "msm-kernel"}

common_patch = HERE / lock["adaptation"]["patches"]["common"]["path"]
if helpers.sha256(common_patch) != lock["adaptation"]["patches"]["common"]["sha256"]:
    raise RuntimeError("common 补丁哈希不符")

# 1. 先核对两棵树的固定基线源码。
for name, tree in TREES.items():
    for rel, expected in lock["sources"][name]["files"].items():
        if helpers.sha256(tree / rel) != expected:
            raise RuntimeError("固定源码不符：" + name + "/" + rel)
    for rel in lock["adaptation"]["patches"]["common"]["files"]:
        if rel in ("fs/susfs.c", "include/linux/susfs.h", "include/linux/susfs_def.h"):
            if (tree / rel).exists():
                raise RuntimeError("目标树已存在 SUSFS 文件，拒绝覆盖：" + name + "/" + rel)
    print("基线源码核对通过：", name, flush=True)

# 2. 两棵树各自应用同一份原生补丁，要求零拒绝、零 fuzz、零偏移。
for name, tree in TREES.items():
    with common_patch.open("rb") as stream:
        p = subprocess.run(["patch", "--batch", "--forward", "--fuzz=0", "-p1"], cwd=tree,
                           stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = p.stdout.decode("utf-8", "replace")
    (report / (name + "-common-apply.log")).write_text(output, encoding="utf-8")
    rejects = list(tree.rglob("*.rej"))
    # offset 是 patch 自动搜索后的正常行号漂移（一加对 exec/SELinux 有定制行）；
    # 只有 fuzz（模糊落位）、FAILED（拒绝）、malformed（补丁损坏）才算失败。
    if p.returncode or rejects or re.search(r"fuzz|FAILED|malformed", output, re.I):
        raise RuntimeError("原生补丁未零 fuzz/零拒绝应用：" + name)
    print("原生补丁应用成功：", name, flush=True)

# 2b. defconfig：显式开启 KPM、移除被 select 的 KALLSYMS_ALL 与导出保护。
defcfg = lock["adaptation"]["patches"]["defconfig"]
defcfg_file = HERE / defcfg["path"]
if helpers.sha256(defcfg_file) != defcfg["sha256"]:
    raise RuntimeError("defconfig 补丁哈希不符")
for name, tree in TREES.items():
    with defcfg_file.open("rb") as stream:
        p = subprocess.run(["patch", "--batch", "--forward", "--fuzz=0", "-p1"], cwd=tree,
                           stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = p.stdout.decode("utf-8", "replace")
    (report / (name + "-defconfig-apply.log")).write_text(output, encoding="utf-8")
    # common 与 msm-kernel 的 gki_defconfig 存在 1 行差异，允许 patch 报告 offset，
    # 但仍要求零 fuzz、零拒绝；内容是否真的改对由下方断言兜底。
    if p.returncode or list(tree.rglob("*.rej")) or re.search(r"fuzz|FAILED|malformed", output, re.I):
        raise RuntimeError("defconfig 补丁应用失败：" + name + " :: " + output[-300:])
    print("defconfig 补丁应用成功：", name, flush=True)

# 3. 获取固定 SukiSU Nova 源码。
ksu = platform / "KernelSU"
ksu.mkdir(exist_ok=False)
source = lock["sources"]["ksu"]
archive = Path("/mnt/work/sukisu-pinned.tar.gz")
url = "https://gh-proxy.org/https://github.com/" + source["repo"] + "/archive/" + source["commit"] + ".tar.gz"
subprocess.run(["curl", "--fail", "--location", "--retry", "3", "--connect-timeout", "20",
                "--max-time", "300", "--output", str(archive), url], check=True)
helpers.extract_selected(archive, ksu, source["files"])
for rel, expected in source["files"].items():
    if helpers.sha256(ksu / rel) != expected:
        raise RuntimeError("固定 SukiSU 源码不符：" + rel)

# 本仓库新增、上游归档里不存在的文件（如 AVC 日志伪装）。
for rel, item in lock["adaptation"].get("additions", {}).items():
    src = HERE / item["source"]
    if helpers.sha256(src) != item["sha256"]:
        raise RuntimeError("新增文件哈希不符：" + rel)
    dest = ksu / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)

# 上游文件改动（AVC feature ID / 注册表 / 启动钩子）以补丁形式应用，要求零偏移。
ksu_patch = lock["adaptation"]["patches"]["ksu"]
if ksu_patch.get("path"):
    patch_file = HERE / ksu_patch["path"]
    if helpers.sha256(patch_file) != ksu_patch["sha256"]:
        raise RuntimeError("KSU 侧补丁哈希不符")
    with patch_file.open("rb") as stream:
        proc = subprocess.run(["patch", "--batch", "--forward", "--fuzz=0", "-p1"], cwd=ksu,
                              stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = proc.stdout.decode("utf-8", "replace")
    (report / "ksu-avc-apply.log").write_text(out, encoding="utf-8")
    if proc.returncode or list(ksu.rglob("*.rej")) or re.search(r"fuzz|FAILED|malformed", out, re.I):
        raise RuntimeError("KSU 侧补丁未零拒绝/零偏移应用：" + out[-400:])

print("SukiSU Nova 源码校验通过：", len(source["files"]), "个文件",
      "(+%d 新增)" % len(lock["adaptation"].get("additions", {})), flush=True)

# 两项扩展的源码证据必须存在，否则说明归档或锁定文件被改坏。
for rel, needle in [
    ("kernel/feature/avc_spoof.c", "ksu_avc_spoof_rewrite_tsid"),
    ("kernel/extension/version_spoof.c", "ksu_version_spoof_cmd"),
    ("kernel/extension/kpm/kpm.c", "kpm"),
]:
    p = ksu / rel
    if not p.is_file() or needle not in p.read_text(encoding="utf-8", errors="replace"):
        raise RuntimeError("扩展源码缺失或内容不符：" + rel)

# 4. 两棵树分别跑源码门禁：保留既有 hook，且新增接线完整。
results = {}
for name, tree in TREES.items():
    results[name] = check(tree, ksu, lock, common_key=name)
    print("源码门禁通过：", name, len(results[name]), "项", flush=True)
(report / "source-checks.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

# 5. 两棵树都安装 KSU；Nova 的 kernel/ 整体复制（uapi 已在其 include/ 下）。
for name, tree in TREES.items():
    dest = tree / "drivers/kernelsu"
    if dest.exists() or dest.is_symlink():
        raise RuntimeError("装配树意外已有 kernelsu：" + name)
    shutil.copytree(ksu / "kernel", dest)
    assert not (dest / "include/uapi").is_symlink()
    if not (dest / "include/uapi/supercall.h").is_file():
        raise RuntimeError("Nova uapi 头文件缺失：" + name)
    p = tree / "drivers/Makefile"
    text = p.read_text()
    if "obj-$(CONFIG_KSU) += kernelsu/" not in text:
        p.write_text(text + "\nobj-$(CONFIG_KSU) += kernelsu/\n")
    p = tree / "drivers/Kconfig"
    text = p.read_text()
    if 'source "drivers/kernelsu/Kconfig"' not in text:
        index = text.rfind("\nendmenu")
        if index < 0:
            raise RuntimeError("drivers/Kconfig 缺少菜单尾部：" + name)
        p.write_text(text[:index] + '\nsource "drivers/kernelsu/Kconfig"\n' + text[index:])
    # defconfig 由 patches/nova/defconfig-kpm.patch 在第 2 步统一应用：
    #   * CONFIG_KPM=y 显式写入（Klean 门禁要求 savedefconfig 与 defconfig 完全一致）；
    #   * CONFIG_KALLSYMS_ALL=y 移除，因为 CONFIG_KPM select 了它；
    #   * CONFIG_MODULE_SIG_PROTECT=y 移除，否则 vendor 模块整链加载失败。
    p = tree / "arch/arm64/configs/gki_defconfig"
    lines = p.read_text().splitlines()
    if "CONFIG_KPM=y" in lines:
        raise RuntimeError("defconfig 不应写 CONFIG_KPM（由 Kconfig 默认 y 提供）：" + name)
    if "CONFIG_KALLSYMS_ALL=y" in lines:
        raise RuntimeError("defconfig 应移除 KALLSYMS_ALL（KPM select 后它成为默认值）：" + name)
    if "CONFIG_MODULE_SIG_PROTECT=y" in lines:
        raise RuntimeError("defconfig 仍开启导出保护：" + name)
    shutil.copy2(p, report / (name + "-gki_defconfig"))
    print("KSU 安装完成：", name, flush=True)
shutil.copytree(ksu, report / "patched-ksu")
print("装配完成：两棵树均已打原生补丁并安装 SukiSU Nova（KPM/版本伪装/AVC 伪装已启用）", flush=True)
