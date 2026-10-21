#!/usr/bin/env python3
"""在一次性官方装配树内安装固定版本的原生 SUSFS + SukiSU。

boot.img 实际由 //msm-kernel:sun16k_perf_dist 产出（run 39532454585 证据：
msm-kernel vmlinux 链接 susfs_* 失败），因此 common 与 msm-kernel 两棵完整
内核树都必须打同一份原生补丁，否则运行时内核缺少隐藏逻辑。
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
    if p.returncode or rejects or re.search(r"offset|fuzz|FAILED|malformed", output, re.I):
        raise RuntimeError("原生补丁未零拒绝/零偏移应用：" + name)
    print("原生补丁应用成功：", name, flush=True)

# 3. 获取固定 SukiSU 源码并应用 SukiSU 侧适配。
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
item = lock["adaptation"]["patches"]["ksu"]
patch = HERE / item["path"]
if helpers.sha256(patch) != item["sha256"]:
    raise RuntimeError("SukiSU 补丁哈希不符")
with patch.open("rb") as stream:
    p = subprocess.run(["patch", "--batch", "--forward", "--fuzz=0", "-p1"], cwd=ksu,
                       stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
output = p.stdout.decode("utf-8", "replace")
(report / "ksu-apply.log").write_text(output, encoding="utf-8")
if p.returncode or list(ksu.rglob("*.rej")) or re.search(r"offset|fuzz|FAILED|malformed", output, re.I):
    raise RuntimeError("SukiSU 侧补丁未零拒绝/零偏移应用")

# 4. 两棵树分别跑源码门禁：保留既有 hook，且新增接线完整。
results = {}
for name, tree in TREES.items():
    results[name] = check(tree, ksu, lock, common_key=name)
    print("源码门禁通过：", name, len(results[name]), "项", flush=True)
(report / "source-checks.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

# 5. 两棵树都安装 KSU；原生文件系统部分已由补丁进入各自内核树。
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
    p = tree / "arch/arm64/configs/gki_defconfig"
    lines = p.read_text().splitlines()
    lines = [l for l in lines if l not in ["CONFIG_MODULE_SIG_PROTECT=y", "# CONFIG_MODULE_SIG_PROTECT is not set"]]
    if any("CONFIG_KSU" in l for l in lines):
        raise RuntimeError("原始 defconfig 意外含 KSU 项：" + name)
    p.write_text("\n".join(lines) + "\n")
    shutil.copy2(p, report / (name + "-gki_defconfig"))
    print("KSU 安装完成：", name, flush=True)
shutil.copytree(ksu, report / "patched-ksu")
print("装配完成：两棵树均已打原生补丁并安装 KSU", flush=True)
