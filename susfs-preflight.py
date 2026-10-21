#!/usr/bin/env python3
"""在一次性云端工作区核验固定版本的 SUSFS 补丁，不编译、不刷机。"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile

HERE = Path(__file__).resolve().parent
LOCK = json.loads((HERE / "susfs-inputs.lock.json").read_text(encoding="utf-8"))
WORK = Path(os.environ.get("SUSFS_PREFLIGHT_WORK", "/mnt/work/susfs-preflight"))
OUT = Path(os.environ.get("SUSFS_PREFLIGHT_OUT", "/mnt/work/susfs-report"))

def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def extract_selected(archive, destination, wanted):
    """只展开清单内的普通源码文件，拒绝链接和目录穿越。"""
    remaining = set(wanted)
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            parts = PurePosixPath(member.name).parts
            if len(parts) < 2:
                continue
            relative = PurePosixPath(*parts[1:]).as_posix()
            if relative not in remaining:
                continue
            if not member.isfile() or ".." in parts or PurePosixPath(relative).is_absolute():
                raise RuntimeError("源码条目不安全：" + member.name)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(member) as src, target.open("xb") as dst:
                shutil.copyfileobj(src, dst)
            remaining.remove(relative)
    if remaining:
        raise RuntimeError("归档缺少源码：" + ", ".join(sorted(remaining)))

def main():
    WORK.mkdir(parents=True, exist_ok=False)
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HERE / "susfs-inputs.lock.json", OUT / "inputs.lock.json")
    sources = {}
    download_record = []
    for name, spec in LOCK["sources"].items():
        archive = WORK / (name + ".tar.gz")
        original = f"https://github.com/{spec['repo']}/archive/{spec['commit']}.tar.gz"
        url = "https://gh-proxy.org/" + original
        print("下载固定提交：", name, spec["commit"], flush=True)
        subprocess.run(["curl", "--fail", "--location", "--retry", "3", "--connect-timeout", "20",
                        "--max-time", "300", "--output", str(archive), url], check=True)
        tree = WORK / (name + "-base")
        tree.mkdir()
        extract_selected(archive, tree, spec["files"])
        for relative, expected in spec["files"].items():
            actual = sha256(tree / relative)
            if actual != expected:
                raise RuntimeError(f"固定源码内容不符：{name}/{relative}，{actual} != {expected}")
        sources[name] = tree
        download_record.append({"name": name, "url": url, "bytes": archive.stat().st_size,
                                "sha256_archive": sha256(archive), "verified_source_files": len(spec["files"])})
        print("源码内容校验通过：", name, len(spec["files"]), "个文件", flush=True)
    (OUT / "downloads.json").write_text(json.dumps(download_record, ensure_ascii=False, indent=2), encoding="utf-8")
    results = []
    upstream = sources["susfs"]
    for name, patch_name in [
        ("common", "kernel_patches/50_add_susfs_in_gki-android15-6.6.patch"),
        ("ksu", "kernel_patches/KernelSU/10_enable_susfs_for_ksu.patch"),
    ]:
        tree = WORK / (name + "-patched")
        shutil.copytree(sources[name], tree)
        patch = upstream / patch_name
        if name == "common":
            for relative in ["fs/susfs.c", "include/linux/susfs.h", "include/linux/susfs_def.h"]:
                dst = tree / relative
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(upstream / "kernel_patches" / relative, dst)
        # 在一次性副本上实际应用，避免含同一路径多段修改的补丁被 dry-run 误报。
        with patch.open("rb") as stream:
            p = subprocess.run(["patch", "--batch", "--forward", "--fuzz=0", "-p1"],
                               cwd=tree, stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        output = p.stdout.decode("utf-8", "replace")
        (OUT / (name + "-apply.log")).write_text(output, encoding="utf-8")
        rejects = [f for f in tree.rglob("*.rej")]
        failed = [line for line in output.splitlines() if re.search(r"FAILED|malformed|can't find|not found", line, re.I)]
        result = {"target": name, "exit_code": p.returncode,
                  "reject_files": [f.relative_to(tree).as_posix() for f in rejects], "failed_hunks": failed}
        results.append(result)
        for f in rejects:
            dst = OUT / "rejects" / name / f.relative_to(tree)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
        # 保留精简的原始与应用后源码，供逐个冲突人工复核。
        shutil.copytree(sources[name], OUT / "base" / name)
        shutil.copytree(tree, OUT / "patched" / name)
        print(name, "退出码", p.returncode, "冲突文件", len(rejects), flush=True)
        for line in failed[:30]:
            print(line, flush=True)
    (OUT / "result.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    title = "# 原生 SUSFS 文本兼容性诊断\n\n"
    title += "固定已验证 SukiSU 提交；未编译内核，未访问手机。补丁可应用不等于编译或运行兼容。\n\n"
    for result in results:
        title += f"- {result['target']}：退出码 {result['exit_code']}，冲突文件 {len(result['reject_files'])} 个。\n"
    title += "\nKSU 适配补丁会替换 hook 机制，还需审查对 SukiSU 扩展的影响，不能直接作为已兼容结论。\n"
    (OUT / "SUMMARY.md").write_text(title, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(title)
    return 1 if any(r["exit_code"] != 0 or r["reject_files"] for r in results) else 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "fatal-error.txt").write_text(str(error), encoding="utf-8")
        raise
