#!/usr/bin/env python3
"""核验固定源码、零偏移应用原生 SUSFS 适配补丁并保留完整证据。"""
import argparse
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
spec = importlib.util.spec_from_file_location("preflight_helpers", HERE / "susfs-preflight.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
LOCK = json.loads((HERE / "susfs-native.lock.json").read_text(encoding="utf-8"))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, help="已有固定源码目录；不下载、不编译")
    parser.add_argument("--patch", default="patch")
    parser.add_argument("--work", type=Path, default=Path("/mnt/work/susfs-native-preflight"))
    parser.add_argument("--out", type=Path, default=Path("/mnt/work/susfs-native-report"))
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=False)
    args.out.mkdir(parents=True, exist_ok=False)
    shutil.copy2(HERE / "susfs-native.lock.json", args.out / "inputs.lock.json")
    sources = {}
    downloads = []
    # 适配补丁本身包含新增 SUSFS 普通文件，因此不另行复制或隐式覆盖。
    for name in ["common", "ksu"]:
        spec = LOCK["sources"][name]
        base = args.work / (name + "-base")
        if args.source_root:
            shutil.copytree(args.source_root / {"common": "base-common", "ksu": "ksu"}[name], base)
        else:
            archive = args.work / (name + ".tar.gz")
            url = "https://gh-proxy.org/https://github.com/" + spec["repo"] + "/archive/" + spec["commit"] + ".tar.gz"
            subprocess.run(["curl", "--fail", "--location", "--retry", "3", "--connect-timeout", "20",
                "--max-time", "300", "--output", str(archive), url], check=True)
            base.mkdir()
            helpers.extract_selected(archive, base, spec["files"])
            downloads.append({"target": name, "sha256": helpers.sha256(archive),
                              "bytes": archive.stat().st_size, "url": url})
        for rel, expected in spec["files"].items():
            actual = helpers.sha256(base / rel)
            if actual != expected:
                raise RuntimeError("固定源码内容不符：" + name + "/" + rel)
        sources[name] = base
        print("固定源码校验通过：", name, len(spec["files"]), flush=True)
    (args.out / "downloads.json").write_text(json.dumps(downloads, ensure_ascii=False, indent=2), encoding="utf-8")
    results = []
    trees = {}
    for name in ["common", "ksu"]:
        item = LOCK["adaptation"]["patches"][name]
        patch = HERE / item["path"]
        if helpers.sha256(patch) != item["sha256"]:
            raise RuntimeError("适配补丁 SHA256 不符：" + name)
        tree = args.work / (name + "-patched")
        shutil.copytree(sources[name], tree)
        with patch.open("rb") as stream:
            p = subprocess.run([args.patch, "--batch", "--forward", "--fuzz=0", "-p1"],
                cwd=tree, stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        output = p.stdout.decode("utf-8", "replace")
        (args.out / (name + "-apply.log")).write_text(output, encoding="utf-8")
        rejects = [p.relative_to(tree).as_posix() for p in tree.rglob("*.rej")]
        offsets = [line for line in output.splitlines() if re.search(r"offset|fuzz|FAILED|malformed", line, re.I)]
        results.append({"target": name, "exit_code": p.returncode, "rejects": rejects, "offsets_or_errors": offsets})
        shutil.copytree(tree, args.out / "patched" / name)
        shutil.copytree(sources[name], args.out / "base" / name)
        trees[name] = tree
        print(name, "补丁退出码", p.returncode, "拒绝", len(rejects), "偏移/错误", len(offsets), flush=True)
    (args.out / "result.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if any(r["exit_code"] or r["rejects"] or r["offsets_or_errors"] for r in results):
        return 1
    checks = check(trees["common"], trees["ksu"], LOCK)
    (args.out / "source-checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
    title = "# 原生 SUSFS 适配源码门禁\n\n"
    title += "common 与 SukiSU 补丁均零拒绝、零 fuzz、零偏移；" + str(len(checks)) + " 项源码接线/基线保留检查通过。\n\n"
    title += "保留现有 SukiSU root hook、preinit、管理器和扩展；新增内核原生文件系统逻辑，不使用 SUSFS LKM。\n\n"
    title += "**尚未证明编译或运行兼容；本作业不编译内核、不访问手机。**\n"
    (args.out / "SUMMARY.md").write_text(title, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(title)
    print("源码门禁通过：", len(checks), "项；不等于编译/运行验收。", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
