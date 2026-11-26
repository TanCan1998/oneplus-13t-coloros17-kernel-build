#!/usr/bin/env python3
"""核验固定源码、零偏移应用原生 SUSFS 适配补丁并保留完整证据。

Nova 版差异：
  * 只有内核侧（common）需要补丁；KSU 侧整体复制，item["path"] 为 None 时跳过；
  * KSU 基线改为 Nova builtin 的完整文件清单。
"""
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
    # 本仓库新增、上游归档里不存在的文件（如 AVC 日志伪装）。
    additions = LOCK["adaptation"].get("additions", {})
    for rel, item in additions.items():
        src = HERE / item["source"]
        if helpers.sha256(src) != item["sha256"]:
            raise RuntimeError("新增文件哈希不符：" + rel)
        dest = sources["ksu"] / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    if additions:
        print("已并入本仓库新增文件：", len(additions), "个", flush=True)
    (args.out / "downloads.json").write_text(json.dumps(downloads, ensure_ascii=False, indent=2), encoding="utf-8")
    results = []
    trees = {}
    for name in ["common", "ksu"]:
        item = LOCK["adaptation"]["patches"][name]
        tree = args.work / (name + "-patched")
        if not item.get("path"):
            # Nova builtin 自带 SUSFS 桥接，KSU 侧不需要任何源码改动。
            shutil.copytree(sources[name], tree)
            results.append({"target": name, "patch": None, "exit_code": 0,
                            "rejects": [], "offsets_or_errors": []})
            shutil.copytree(tree, args.out / "patched" / name)
            shutil.copytree(sources[name], args.out / "base" / name)
            trees[name] = tree
            print(name, "无需补丁（整体复制）", flush=True)
            continue
        patch = HERE / item["path"]
        if helpers.sha256(patch) != item["sha256"]:
            raise RuntimeError("适配补丁 SHA256 不符：" + name)
        shutil.copytree(sources[name], tree)
        with patch.open("rb") as stream:
            p = subprocess.run([args.patch, "--batch", "--forward", "--fuzz=0", "-p1"],
                cwd=tree, stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        output = p.stdout.decode("utf-8", "replace")
        (args.out / (name + "-apply.log")).write_text(output, encoding="utf-8")
        rejects = [p.relative_to(tree).as_posix() for p in tree.rglob("*.rej")]
        offsets = [line for line in output.splitlines() if re.search(r"offset|fuzz|FAILED|malformed", line, re.I)]
        results.append({"target": name, "patch": item["path"], "exit_code": p.returncode,
                        "rejects": rejects, "offsets_or_errors": offsets})
        shutil.copytree(tree, args.out / "patched" / name)
        shutil.copytree(sources[name], args.out / "base" / name)
        trees[name] = tree
        print(name, "补丁退出码", p.returncode, "拒绝", len(rejects), "偏移/错误", len(offsets), flush=True)
    # defconfig 补丁：预检只做内容校验。这里没有完整内核树，
    # 真正的零偏移应用由 ci-install-susfs.py 在两棵真实内核树上完成并留证。
    defcfg = LOCK["adaptation"]["patches"].get("defconfig")
    if defcfg:
        dfile = HERE / defcfg["path"]
        if helpers.sha256(dfile) != defcfg["sha256"]:
            raise RuntimeError("defconfig 补丁 SHA256 不符")
        body = dfile.read_text(encoding="utf-8")
        for needle in ("+CONFIG_KPM=y", "-CONFIG_KALLSYMS_ALL=y", "-CONFIG_MODULE_SIG_PROTECT=y"):
            if needle not in body:
                raise RuntimeError("defconfig 补丁缺少预期改动：" + needle)
        if "+CONFIG_KALLSYMS_ALL=y" in body or "+CONFIG_MODULE_SIG_PROTECT=y" in body:
            raise RuntimeError("defconfig 补丁出现了不应有的新增项")
        results.append({"target": "defconfig", "patch": defcfg["path"], "exit_code": 0,
                        "rejects": [], "offsets_or_errors": []})
        print("defconfig 补丁内容校验通过（KPM=y，移除 KALLSYMS_ALL 与 SIG_PROTECT）", flush=True)

    (args.out / "result.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if any(r["exit_code"] or r["rejects"] or r["offsets_or_errors"] for r in results):
        return 1
    checks = check(trees["common"], trees["ksu"], LOCK)
    (args.out / "source-checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
    title = "# 原生 SUSFS + SukiSU Nova 适配源码门禁\n\n"
    title += "内核补丁零拒绝、零 fuzz、零偏移；" + str(len(checks)) + " 项源码接线/基线保留检查通过。\n\n"
    title += "SukiSU 源为 Nova builtin（自带 SUSFS 桥接）；启用 KPM、版本信息自定义、AVC 日志伪装。\n\n"
    title += "**尚未证明编译或运行兼容；本作业不编译内核、不访问手机。**\n"
    (args.out / "SUMMARY.md").write_text(title, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(title)
    print("源码门禁通过：", len(checks), "项；不等于编译/运行验收。", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
