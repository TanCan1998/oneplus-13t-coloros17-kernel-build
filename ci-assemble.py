#!/usr/bin/env python3
"""在 CI 里下载官方 35 个依赖并装配成 kernel_platform 构建树。"""
import copy
import hashlib
import json
import os
import shutil
import sys
import tarfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(os.environ.get("TREE_ROOT", "/mnt/work/official-13t"))
ARCH = Path(os.environ.get("ARCH_DIR", "/mnt/work/archives"))
DEPS = Path("deps.json")

ARCH.mkdir(parents=True, exist_ok=True)
ROOT.mkdir(parents=True, exist_ok=True)
deps = json.loads(DEPS.read_text(encoding="utf-8"))
print(f"依赖 {len(deps)} 个，共 {sum(d['bytes'] for d in deps)/1024**3:.2f} GiB", flush=True)


def archive_name(d):
    return ARCH / (d["name"].replace("/", "_") + ".tar.gz")


def download(d):
    dst = archive_name(d)
    if dst.exists() and dst.stat().st_size == d["bytes"]:
        return d["name"], dst, "cached"
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(d["url"], headers={"User-Agent": "curl/8.4.0"})
            with urllib.request.urlopen(req, timeout=600) as r, dst.open("wb") as f:
                shutil.copyfileobj(r, f, 4 * 1024 * 1024)
            if dst.stat().st_size == d["bytes"]:
                return d["name"], dst, "ok"
            last = f"大小不符 {dst.stat().st_size} != {d['bytes']}"
        except Exception as e:  # noqa: BLE001
            last = str(e)[:120]
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"下载失败 {d['name']}: {last}")


t0 = time.time()
with ThreadPoolExecutor(max_workers=8) as ex:
    results = list(ex.map(download, deps))
print(f"下载完成 {len(results)} 个，用时 {time.time()-t0:.1f}s", flush=True)

# 先装根目录项目，避免后续文件冲突判断
deps_sorted = sorted(deps, key=lambda d: (d["path"] != "./", d["path"]))


def assemble(d):
    archive = archive_name(d)
    dest = ROOT / d["path"]
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    total = 0
    with tarfile.open(archive, "r|gz") as tf:
        prefix = None
        for member in tf:
            first, sep, relative = member.name.partition("/")
            if prefix is None:
                prefix = first
            if first != prefix:
                raise RuntimeError("归档存在多个顶级前缀: " + d["name"])
            if not sep or not relative:
                continue
            m = copy.copy(member)
            m.name = (Path(d["path"]) / relative).as_posix()
            if m.islnk():
                if not m.linkname.startswith(prefix + "/"):
                    raise RuntimeError("硬链接目标前缀异常: " + d["name"])
                m.linkname = (Path(d["path"]) / m.linkname[len(prefix) + 1:]).as_posix()
            target = ROOT / m.name
            if os.path.lexists(target):
                if m.isdir() and target.is_dir():
                    continue
                if m.issym() and target.is_symlink() and os.readlink(target) == m.linkname:
                    continue
                if m.isfile() and target.is_file() and not target.is_symlink():
                    incoming = hashlib.sha256(tf.extractfile(member).read()).hexdigest()
                    if incoming == hashlib.sha256(target.read_bytes()).hexdigest():
                        continue
                raise RuntimeError("项目间文件冲突: " + str(target))
            tf.extract(m, path=ROOT, filter="data")
            count += 1
            total += m.size
    print(f"装配 {d['name']} -> {dest}  ({count} 项, {total/1024**2:.0f} MiB)", flush=True)


for d in deps_sorted:
    assemble(d)

for link, target in [
    ("kernel_platform/.source_date_epoch_dir", "common"),
    ("kernel_platform/build_with_bazel.py", "msm-kernel/build_with_bazel.py"),
]:
    p = ROOT / link
    if not os.path.lexists(p):
        p.symlink_to(target)

free = shutil.disk_usage(ROOT).free / 1024**3
print(f"装配完成，剩余空间 {free:.1f} GiB", flush=True)
if free < 25:
    print("警告：剩余空间不足 25 GiB，可能导致编译失败", flush=True)
