#!/usr/bin/env python3
"""只在云端一次性官方装配树内安装固定版本的原生 SUSFS + SukiSU。"""
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
common = platform / "common"
report = Path("/mnt/work/susfs-install-report")
report.mkdir(exist_ok=False)
shutil.copy2(HERE / "susfs-native.lock.json", report / "inputs.lock.json")
for rel, expected in lock["sources"]["common"]["files"].items():
    if helpers.sha256(common / rel) != expected:
        raise RuntimeError("官方 common 固定源码不符：" + rel)

ksu = platform / "KernelSU"
ksu.mkdir(exist_ok=False)
archive = Path("/mnt/work/sukisu-pinned.tar.gz")
source = lock["sources"]["ksu"]
url = "https://gh-proxy.org/https://github.com/" + source["repo"] + "/archive/" + source["commit"] + ".tar.gz"
subprocess.run(["curl", "--fail", "--location", "--retry", "3", "--connect-timeout", "20",
    "--max-time", "300", "--output", str(archive), url], check=True)
helpers.extract_selected(archive, ksu, source["files"])
for rel, expected in source["files"].items():
    if helpers.sha256(ksu / rel) != expected:
        raise RuntimeError("固定 SukiSU 源码不符：" + rel)
for name, tree in [("common", common), ("ksu", ksu)]:
    item = lock["adaptation"]["patches"][name]
    patch = HERE / item["path"]
    if helpers.sha256(patch) != item["sha256"]:
        raise RuntimeError("补丁哈希不符：" + name)
    with patch.open("rb") as stream:
        p = subprocess.run(["patch", "--batch", "--forward", "--fuzz=0", "-p1"], cwd=tree,
            stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = p.stdout.decode("utf-8", "replace")
    (report / (name + "-apply.log")).write_text(output, encoding="utf-8")
    if p.returncode or re.search(r"offset|fuzz|FAILED|malformed", output, re.I) or list(tree.rglob("*.rej")):
        raise RuntimeError("补丁未零拒绝/零偏移应用：" + name)
checks = check(common, ksu, lock)
(report / "source-checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")

# common 编译基础 Image；msm-kernel 参与配置和模块目标，两侧均需要 KSU 定义。
# 原生文件系统修改针对 common；msm-kernel 只同步其 KSU 代码和所需头文件。
for target in ["common", "msm-kernel"]:
    tree = platform / target
    dest = tree / "drivers/kernelsu"
    if dest.exists() or dest.is_symlink():
        raise RuntimeError("装配树意外已有 kernelsu，拒绝隐式覆盖：" + target)
    shutil.copytree(ksu / "kernel", dest)
    shutil.copytree(ksu / "uapi", dest / "include/uapi")
    assert not (dest / "include/uapi").is_symlink()
    for rel in ["susfs.h", "susfs_def.h"]:
        header = tree / "include/linux" / rel
        src = common / "include/linux" / rel
        if target == "msm-kernel":
            if header.exists():
                raise RuntimeError("msm-kernel 意外已有 SUSFS 头文件")
            shutil.copy2(src, header)
    p = tree / "drivers/Makefile"
    text = p.read_text()
    if "obj-$(CONFIG_KSU) += kernelsu/" not in text:
        p.write_text(text + "\nobj-$(CONFIG_KSU) += kernelsu/\n")
    p = tree / "drivers/Kconfig"
    text = p.read_text()
    if 'source "drivers/kernelsu/Kconfig"' not in text:
        index = text.rfind("\nendmenu")
        if index < 0:
            raise RuntimeError("drivers/Kconfig 缺少菜单尾部")
        p.write_text(text[:index] + '\nsource "drivers/kernelsu/Kconfig"\n' + text[index:])
    # 默认 y 的 KSU/SUSFS 不写入 defconfig，避免严格 savedefconfig 比较失败。
    p = tree / "arch/arm64/configs/gki_defconfig"
    lines = p.read_text().splitlines()
    lines = [line for line in lines if line not in [
        "CONFIG_MODULE_SIG_PROTECT=y", "# CONFIG_MODULE_SIG_PROTECT is not set"]]
    if any("CONFIG_KSU_SUSFS" in line for line in lines):
        raise RuntimeError("原始 defconfig 意外已有 SUSFS 项")
    p.write_text("\n".join(lines) + "\n")
    shutil.copy2(p, report / (target + "-gki_defconfig"))
    print("安装完成：", target, "实体 UAPI 与 SUSFS 头文件齐全", flush=True)
shutil.copytree(ksu, report / "patched-ksu")
print("完整装配树源码门禁通过：", len(checks), "项", flush=True)
