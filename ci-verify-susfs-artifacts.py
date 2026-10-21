#!/usr/bin/env python3
"""验证真实产物结构、配置与链接证据；不宣称运行兼容，不访问手机。"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct

root = Path(os.environ["TREE_ROOT"])
dist = root / "kernel_platform/out/msm-kernel-sun-perf/dist"
out = Path("/mnt/work/susfs-build-verification")
out.mkdir(exist_ok=False)
image = (dist / "Image").read_bytes()
boot = (dist / "boot.img").read_bytes()
symbols = (dist / "System.map").read_text()
if boot[:8] != b"ANDROID!" or len(boot) != 100663296:
    raise RuntimeError("boot 格式或 OnePlus 13T 分区尺寸不符")
kernel_size, ramdisk_size = struct.unpack_from("<II", boot, 8)
header_version = struct.unpack_from("<I", boot, 40)[0]
if header_version != 4 or ramdisk_size != 0 or kernel_size != len(image):
    raise RuntimeError("boot v4 内核/ramdisk 信息不符")
# v4 的内核从 4096 页边界开始；确认 boot 中确实嵌入当前 Image。
if boot[4096:4096 + len(image)] != image:
    raise RuntimeError("boot 内核内容与所提交 Image 不一致")
ksu = re.findall(r"^[0-9a-fA-F]+\s+\w\s+((?:ksu_|kernelsu)\S*)", symbols, re.M)
susfs = re.findall(r"^[0-9a-fA-F]+\s+\w\s+((?:susfs_|ksu_susfs_)\S*)", symbols, re.M)
if len(ksu) < 40 or len(susfs) < 10:
    raise RuntimeError("真实 KSU/SUSFS 链接符号不足：" + str((len(ksu), len(susfs))))
for name in ["susfs_add_sus_path", "ksu_susfs_handle_reboot"]:
    if not re.search(r"\s" + name + r"(?:\.[^\s]+)?$", symbols, re.M):
        raise RuntimeError("未找到关键原生控制函数：" + name)
for token in [b"hook_manager", b"com.sukisu.ultra", b"native-susfs-7ea0dd2b", b"susfs is initialized! version:"]:
    if token not in image:
        raise RuntimeError("Image 缺少运行时证据：" + token.decode())
for token in [b"exports protected symbol", b"gki_is_module_protected_export"]:
    if token in image:
        raise RuntimeError("Image 仍含导出保护拒绝逻辑")
record = {
    "workflow_commit": os.environ.get("GITHUB_SHA"),
    "baseline_run": "39450775101",
    "ksu_commit": "7ea0dd2b09bfc60e6b51a6a93fc9f49b349a9b2a",
    "susfs_commit": "3f6aae536724af0df3dfa15f932034d14a5dbc5c",
    "ksu_symbols": len(ksu), "susfs_symbols": len(susfs),
    "header_version": header_version, "ramdisk_size": ramdisk_size, "kernel_size": kernel_size,
    "files": {},
    "scope": "仅验证编译产物；手机未刷入，未完成运行与长期稳定性验收",
}
for name in ["boot.img", "Image", "System.map"]:
    p = dist / name
    record["files"][name] = {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
(dist / "SHA256SUMS.txt").write_text("".join(v["sha256"] + "  " + k + "\n" for k, v in record["files"].items()))
(out / "result.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
(out / "ksu-symbols.txt").write_text("\n".join(ksu) + "\n")
(out / "susfs-symbols.txt").write_text("\n".join(susfs) + "\n")
shutil.copy2(Path(__file__).parent / "susfs-native.lock.json", out / "inputs.lock.json")
print(json.dumps(record, ensure_ascii=False, indent=2))
