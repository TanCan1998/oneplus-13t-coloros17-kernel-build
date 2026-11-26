#!/usr/bin/env python3
"""在一次性官方装配树内安装固定版本的原生 SUSFS + SukiSU Nova（builtin 分支）。

与 Ultra 版的差异：
  * 上游源码换成 SukiSU-Nova/SukiSU-Nova 的 builtin 分支，目录含 kernel/ 与 uapi/；
  * Nova 自带 SUSFS supercall 桥接，不再需要 KSU 侧源码补丁，改为整体复制；
  * 额外启用三项扩展：KPM（CONFIG_KPM=y）、版本信息自定义（内置）、
    AVC 日志伪装（feature/avc_spoof.c，已随源码带入）；
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

# defconfig 追加项：KPM 默认 n，必须显式开启；AVC 依赖的 KPROBES 等已默认 y。
EXTRA_CONFIGS = ["CONFIG_KPM=y"]

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
    if p.returncode or rejects or re.search(r"offset|fuzz|FAILED|malformed", output, re.I):
        raise RuntimeError("原生补丁未零拒绝/零偏移应用：" + name)
    print("原生补丁应用成功：", name, flush=True)

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
print("SukiSU Nova 源码校验通过：", len(source["files"]), "个文件", flush=True)

# 两项扩展的源码证据必须存在，否则说明归档或锁定文件被改坏。
for rel, needle in [
    ("kernel/feature/avc_spoof.c", "slow_avc_audit"),
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

# 5. 两棵树都安装 KSU；Nova 的 kernel/ 与 uapi/ 整体复制。
for name, tree in TREES.items():
    dest = tree / "drivers/kernelsu"
    if dest.exists() or dest.is_symlink():
        raise RuntimeError("装配树意外已有 kernelsu：" + name)
    shutil.copytree(ksu / "kernel", dest)
    shutil.copytree(ksu / "uapi", dest / "include/uapi")
    assert not (dest / "include/uapi").is_symlink()
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
    # 默认 y 的 KSU/SUSFS 不写入 defconfig；仅移除会破坏 WiFi 的导出保护。
    # CONFIG_KPM 默认 n，必须显式写入才会编译 KPM 子系统。
    p = tree / "arch/arm64/configs/gki_defconfig"
    lines = p.read_text().splitlines()
    lines = [l for l in lines if l not in ["CONFIG_MODULE_SIG_PROTECT=y", "# CONFIG_MODULE_SIG_PROTECT is not set"]]
    if any("CONFIG_KSU" in l for l in lines):
        raise RuntimeError("原始 defconfig 意外含 KSU 项：" + name)
    for entry in EXTRA_CONFIGS:
        if entry not in lines:
            lines.append(entry)
    p.write_text("\n".join(lines) + "\n")
    shutil.copy2(p, report / (name + "-gki_defconfig"))
    print("KSU 安装完成：", name, flush=True)
shutil.copytree(ksu, report / "patched-ksu")
print("装配完成：两棵树均已打原生补丁并安装 SukiSU Nova（KPM/版本伪装/AVC 伪装已启用）", flush=True)
