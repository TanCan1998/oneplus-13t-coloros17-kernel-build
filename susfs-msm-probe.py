#!/usr/bin/env python3
"""探测 msm-kernel 树（真正的 dist/Image 来源）能否复用同一份原生 SUSFS 补丁。"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
LOCK = json.loads((HERE / "susfs-native.lock.json").read_text(encoding="utf-8"))
WORK = Path(os.environ.get("PROBE_WORK", "/mnt/work/msm-probe"))
OUT = Path(os.environ.get("PROBE_OUT", "/mnt/work/msm-probe-report"))
SPEC = {
    "repo": "OnePlusOSS/android_kernel_oneplus_sm8750",
    "commit": "745702d5ed2c0435f2b1d3bb78bc4f48422d4191",
}
PATCH = HERE / LOCK["adaptation"]["patches"]["common"]["path"]
TOUCHED = LOCK["adaptation"]["patches"]["common"]["files"]
NEW_FILES = {"fs/susfs.c", "include/linux/susfs.h", "include/linux/susfs_def.h"}
EXTRA = ["drivers/Makefile", "drivers/Kconfig", "arch/arm64/configs/gki_defconfig"]

def sha256(p):
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    WORK.mkdir(parents=True, exist_ok=False)
    OUT.mkdir(parents=True, exist_ok=True)
    if sha256(PATCH) != LOCK["adaptation"]["patches"]["common"]["sha256"]:
        raise RuntimeError("补丁哈希不符")
    archive = WORK / "msm.tar.gz"
    url = "https://gh-proxy.org/https://github.com/" + SPEC["repo"] + "/archive/" + SPEC["commit"] + ".tar.gz"
    subprocess.run(["curl", "--fail", "--location", "--retry", "3", "--connect-timeout", "20",
                    "--max-time", "600", "--output", str(archive), url], check=True)
    wanted = [p for p in TOUCHED if p not in NEW_FILES] + EXTRA
    tree = WORK / "msm"
    tree.mkdir()
    remaining = set(wanted)
    import tarfile
    from pathlib import PurePosixPath
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            parts = PurePosixPath(member.name).parts
            if len(parts) < 2:
                continue
            rel = PurePosixPath(*parts[1:]).as_posix()
            if rel not in remaining:
                continue
            if not member.isfile() or ".." in parts:
                raise RuntimeError("不安全条目：" + member.name)
            target = tree / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(member) as src, target.open("xb") as dst:
                shutil.copyfileobj(src, dst)
            remaining.remove(rel)
    if remaining:
        raise RuntimeError("缺少文件：" + ", ".join(sorted(remaining)))
    common_base = LOCK["sources"]["common"]["files"]
    record = {"commit": SPEC["commit"], "archive_bytes": archive.stat().st_size,
              "archive_sha256": sha256(archive), "files": {}, "identical_to_common": [],
              "differs_from_common": [], "new_file_conflicts": []}
    for rel in wanted:
        record["files"][rel] = sha256(tree / rel)
    for rel in TOUCHED:
        if rel in NEW_FILES:
            continue
        if record["files"][rel] == common_base.get(rel):
            record["identical_to_common"].append(rel)
        else:
            record["differs_from_common"].append(rel)
    for rel in sorted(NEW_FILES):
        if (tree / rel).exists():
            record["new_file_conflicts"].append(rel)
    # 只展开补丁涉及的普通文件即可验证补丁上下文。
    probe = WORK / "patched"
    shutil.copytree(tree, probe)
    with PATCH.open("rb") as stream:
        p = subprocess.run(["patch", "--batch", "--forward", "--fuzz=0", "-p1"], cwd=probe,
                           stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log = p.stdout.decode("utf-8", "replace")
    (OUT / "apply.log").write_text(log, encoding="utf-8")
    record["apply_exit_code"] = p.returncode
    record["rejects"] = [x.relative_to(probe).as_posix() for x in probe.rglob("*.rej")]
    record["offset_or_fuzz"] = [l for l in log.splitlines() if re.search(r"offset|fuzz|FAILED", l, re.I)]
    (OUT / "msm-probe.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in record.items() if k != "files"}, ensure_ascii=False, indent=2))
    print("涉及文件数", len(record["files"]), "相同", len(record["identical_to_common"]),
          "不同", len(record["differs_from_common"]), "新文件冲突", len(record["new_file_conflicts"]),
          "拒绝", len(record["rejects"]))
    return 0 if not record["rejects"] and not record["new_file_conflicts"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
