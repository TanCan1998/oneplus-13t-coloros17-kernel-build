#!/usr/bin/env python3
"""只读巡检一加官方内核源码：13T 分支是否有新提交、是否出现 17.x 分支。

不下载源码、不编译、不碰设备。发现变化时在本仓库开 issue（同标题只评论一次）。
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCK = ROOT / "susfs-native.lock.json"
BRANCH_13T = "oneplus/sm8750_b_16.0.0_oneplus_13t"
SERIES_RE = re.compile(r"^oneplus/sm8750_([a-z])_(\d+)\.(\d+)\.(\d+)_(.+)$")
ISSUE_TITLE = "[source-watch] 一加 13T 官方内核源码有更新"
LABEL = "source-watch"
COMMON_REPO = "OnePlusOSS/android_kernel_common_oneplus_sm8750"


def c(text):
    """反引号包裹，用于 Markdown 代码片段。"""
    return "\x60" + str(text) + "\x60"


def api(path, method="GET", payload=None, token=None):
    url = "https://api.github.com" + path
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "oneplus-source-watch")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
    return json.loads(body) if body else {}


def pinned_commits():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    out = {}
    for name, meta in lock["sources"].items():
        repo = meta.get("repo", "")
        if repo.startswith("OnePlusOSS/"):
            out[repo] = {"name": name, "commit": meta["commit"]}
    if not out:
        raise RuntimeError("锁文件里没有 OnePlusOSS 源码记录")
    return out


def list_branches(repo, token):
    names = []
    for page in range(1, 4):
        chunk = api("/repos/" + repo + "/branches?per_page=100&page=" + str(page), token=token)
        names += [row["name"] for row in chunk]
        if len(chunk) < 100:
            break
    return names


def head_commit(repo, branch, token):
    quoted = urllib.parse.quote(branch, safe="")
    row = api("/repos/" + repo + "/commits/" + quoted, token=token)
    return {
        "sha": row["sha"],
        "date": row["commit"]["committer"]["date"],
        "subject": row["commit"]["message"].splitlines()[0],
    }


def new_series(branches):
    """列出 17.x 及以上的分支；同时给出 13T 专属的新系列。"""
    found = []
    for name in branches:
        hit = SERIES_RE.match(name)
        if not hit:
            continue
        major = int(hit.group(2))
        if major >= 17:
            found.append({"branch": name, "major": major, "is_13t": "13t" in hit.group(5)})
    found.sort(key=lambda row: (row["major"], row["branch"]))
    return found


def render(report):
    lines = [
        "巡检时间：" + report["checked_at"],
        "官方 13T 分支：" + c(report["branch_13t"]),
        "",
        "| 仓库 | 官方 HEAD | 锁定提交 | 状态 |",
        "|---|---|---|---|",
    ]
    for row in report["repos"]:
        state = "**有更新**" if row["drift"] else "与锁定一致"
        lines.append(
            "| " + c(row["repo"]) + " | " + c(row["head"]["sha"][:10]) + " " + row["head"]["date"]
            + " | " + c(row["pinned"][:10]) + " | " + state + " |"
        )
    if report["series"]:
        lines += ["", "### 出现 17.x 及以上分支", ""]
        for row in report["series"]:
            mark = "（13T）" if row["is_13t"] else ""
            lines.append("- " + c(row["branch"]) + mark)
    else:
        lines += ["", "尚未出现 17.x 及以上分支。"]
    if report.get("errors"):
        lines += ["", "### 查询失败", ""] + ["- " + err for err in report["errors"]]
    lines += ["", "下一步：确认新提交内容后更新 deps.json 与 susfs-native.lock.json 的提交和 SHA256，"
                  "先跑 skip_build=true 门禁，再编译与单槽刷机。"]
    return "\n".join(lines)


def build_report(token):
    pinned = pinned_commits()
    repos, series, errors = [], [], []
    for repo in sorted(pinned):
        try:
            branches = list_branches(repo, token)
            head = head_commit(repo, BRANCH_13T, token)
        except Exception as exc:
            errors.append(repo + ": " + type(exc).__name__ + " " + str(exc))
            continue
        if repo == COMMON_REPO:
            series = new_series(branches)
        repos.append({
            "repo": repo,
            "pinned": pinned[repo]["commit"],
            "head": head,
            "drift": head["sha"] != pinned[repo]["commit"],
            "branch_count": len(branches),
        })
    return {
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "branch_13t": BRANCH_13T,
        "repos": repos,
        "series": series,
        "errors": errors,
        "changed": any(row["drift"] for row in repos) or bool(series),
    }


def ensure_label(repo, token):
    try:
        api("/repos/" + repo + "/labels/" + LABEL, token=token)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        api("/repos/" + repo + "/labels", method="POST",
            payload={"name": LABEL, "color": "0e8a16",
                     "description": "一加官方内核源码更新巡检"}, token=token)


def upsert_issue(repo, token, body):
    ensure_label(repo, token)
    existing = api("/repos/" + repo + "/issues?state=open&labels=" + LABEL + "&per_page=20", token=token)
    for issue in existing:
        if issue["title"] == ISSUE_TITLE:
            api("/repos/" + repo + "/issues/" + str(issue["number"]) + "/comments",
                method="POST", payload={"body": body}, token=token)
            return "commented", issue["number"]
    created = api("/repos/" + repo + "/issues", method="POST",
                  payload={"title": ISSUE_TITLE, "body": body, "labels": [LABEL]}, token=token)
    return "created", created["number"]


def self_test():
    assert new_series([
        "oneplus/sm8750_b_16.0.0_oneplus_13t",
        "oneplus/sm8750_b_17.0.0_oneplus_13t",
        "oneplus/sm8750_v_15.0.2_oneplus_13t",
        "junk",
    ]) == [{"branch": "oneplus/sm8750_b_17.0.0_oneplus_13t", "major": 17, "is_13t": True}]
    assert new_series(["oneplus/sm8750_b_16.0.0_oneplus_13"]) == []
    fake = {
        "checked_at": "2026-01-01 00:00 UTC",
        "branch_13t": BRANCH_13T,
        "repos": [{"repo": "OnePlusOSS/x", "pinned": "a" * 40,
                   "head": {"sha": "b" * 40, "date": "2026-01-01T00:00:00Z", "subject": "s"},
                   "drift": True, "branch_count": 3}],
        "series": [{"branch": "oneplus/sm8750_b_17.0.0_oneplus_13t", "major": 17, "is_13t": True}],
        "errors": [], "changed": True,
    }
    text = render(fake)
    assert "有更新" in text and "17.0.0_oneplus_13t" in text and "查询失败" not in text
    assert c("x") == chr(96) + "x" + chr(96)
    print("self-test ok")
    return 0


def main():
    if "--self-test" in sys.argv:
        return self_test()
    token = os.environ.get("GITHUB_TOKEN")
    dry_run = "--dry-run" in sys.argv
    report = build_report(token)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    body = render(report)
    out = Path(os.environ.get("WATCH_OUT", "/tmp/source-watch"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(body + "\n", encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        Path(summary).write_text("## 一加官方源码巡检\n\n" + body + "\n", encoding="utf-8")
    if report["errors"] and not report["repos"]:
        print("全部仓库查询失败：" + "; ".join(report["errors"]), file=sys.stderr)
        return 1
    if report["changed"]:
        repo = os.environ.get("GITHUB_REPOSITORY")
        if dry_run or not repo or not token:
            print("检测到变化（未开 issue：dry-run 或缺少 GITHUB_TOKEN/GITHUB_REPOSITORY）")
        else:
            action, number = upsert_issue(repo, token, body)
            print("issue " + action + ": #" + str(number))
    else:
        print("无变化：官方 13T 分支 HEAD 与锁定提交一致，且无 17.x 分支")
    return 0


if __name__ == "__main__":
    sys.exit(main())
