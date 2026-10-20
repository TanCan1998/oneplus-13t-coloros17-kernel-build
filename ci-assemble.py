#!/usr/bin/env python3
"""在 CI 里下载官方 35 个依赖并装配成 kernel_platform 构建树。

注意：GitHub 的 /archive/<sha>.tar.gz 不是确定性输出——同一 commit 每次打包
字节数会变（gzip 元数据差异）。因此这里不比对精确字节数，改为"能完整解压"
作为完整性判据（gzip CRC 会兜底传输损坏），并记录实际大小与预期差异。
"""
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
print(f"依赖 {len(deps)} 个，预期共 {sum(d['bytes'] for d in deps)/1024**3:.2f} GiB", flush=True)


def archive_name(d):
    return ARCH / (d["name"].replace("/", "_") + ".tar.gz")


def archive_ok(path):
    """能被 tar 完整读取且至少有一个成员，即认为归档完好。"""
    try:
        with tarfile.open(path, "r|gz") as tf:
            first = tf.next()
            if first is None:
                return False
            for _ in tf:
                pass
        return True
    except Exception:
        return False


def sha256_of(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(d, dst):
    """镜像源（Release）确定性下载，严格校验字节数 + SHA256。

    其他源（GitHub /archive/<sha>.tar.gz）非确定性打包，字节数会漂移，
    只能以"能完整解压"作为完整性判据（gzip CRC 会兜底传输损坏）。
    """
    if d.get("source") == "release-mirror":
        if dst.stat().st_size != d["bytes"]:
            return f"大小不符 {dst.stat().st_size} != {d['bytes']}"
        actual = sha256_of(dst)
        if actual != d["sha256"]:
            return f"SHA256 不符 {actual[:16]} != {d['sha256'][:16]}"
        return None
    if not archive_ok(dst):
        return "解压校验失败"
    return None


def download(d):
    dst = archive_name(d)
    if dst.exists():
        if verify(d, dst) is None:
            return d["name"], "cached", dst.stat().st_size
        dst.unlink()
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(d["url"], headers={"User-Agent": "curl/8.4.0"})
            with urllib.request.urlopen(req, timeout=1800) as r, dst.open("wb") as f:
                shutil.copyfileobj(r, f, 4 * 1024 * 1024)
            problem = verify(d, dst)
            if problem is None:
                return d["name"], "ok", dst.stat().st_size
            last = problem
        except Exception as e:  # noqa: BLE001
            last = str(e)[:120]
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"下载失败 {d['name']}: {last}")


t0 = time.time()
with ThreadPoolExecutor(max_workers=8) as ex:
    results = list(ex.map(download, deps))
print(f"下载完成 {len(results)} 个，用时 {time.time()-t0:.1f}s", flush=True)

drift = [(d, size) for d, (_, _, size) in zip(deps, results)
         if size != d["bytes"] and d.get("source") != "release-mirror"]
if drift:
    print(f"注意：{len(drift)} 个 GitHub 归档字节数与本地记录不同（打包非确定性，属正常）：", flush=True)
    for d, size in drift[:10]:
        print(f"  {d['name']}: {size} vs {d['bytes']} ({(size-d['bytes'])/d['bytes']*100:+.2f}%)", flush=True)

mirrored = [d for d in deps if d.get("source") == "release-mirror"]
print(f"其中 {len(mirrored)} 个走 Release 镜像，已逐个校验字节数与 SHA256", flush=True)

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

# prepare_vendor.sh 会在真正构建时创建这些链接，但配置门禁步骤直接调 bazel，
# 跳过了它，所以必须自己建，否则 bazel 报
# "cannot load '//build:msm_kernel_extensions.bzl': no such file"
links = [
    ("kernel_platform/.source_date_epoch_dir", "common"),
    ("kernel_platform/build_with_bazel.py", "msm-kernel/build_with_bazel.py"),
    ("kernel_platform/build/msm_kernel_extensions.bzl",
     "../msm-kernel/msm_kernel_extensions.bzl"),
    ("kernel_platform/build/abl_extensions.bzl",
     "../bootable/bootloader/edk2/abl_extensions.bzl"),
]
for link, target in links:
    p = ROOT / link
    if os.path.lexists(p):
        continue
    src = (p.parent / target)
    if not src.exists():
        print(f"跳过软链接（源不存在）: {link}", flush=True)
        continue
    p.parent.mkdir(parents=True, exist_ok=True)
    p.symlink_to(target)
    print(f"建立软链接: {link} -> {target}", flush=True)

free = shutil.disk_usage(ROOT).free / 1024**3
print(f"装配完成，剩余空间 {free:.1f} GiB", flush=True)
if free < 25:
    print("警告：剩余空间不足 25 GiB，可能导致编译失败", flush=True)
