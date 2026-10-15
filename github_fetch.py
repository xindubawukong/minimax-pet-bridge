#!/usr/bin/env python3
"""Export an organization's recent commits and their details using authenticated gh."""

from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo


PRINT_LOCK = threading.Lock()


def log(message):
    with PRINT_LOCK:
        print(message, flush=True)


def iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_date(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Dates must include a timezone")
    return result.astimezone(timezone.utc)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def gh_api(endpoint, *, paginate=False, media=None):
    command = ["gh", "api", endpoint]
    if paginate:
        command += ["--paginate", "--slurp"]
    if media:
        command += ["-H", "Accept: " + media]
    for attempt in range(4):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            if attempt == 3:
                raise RuntimeError(f"Timed out: {endpoint}")
            time.sleep(2 ** attempt)
            continue
        if result.returncode == 0:
            return result.stdout if media else json.loads(result.stdout)
        transient = any(code in result.stderr for code in ("HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504", "HTTP 429"))
        if not transient or attempt == 3:
            raise RuntimeError(result.stderr.strip() or f"gh failed: {endpoint}")
        time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def list_api(endpoint, params=None):
    query = {"per_page": 100, **(params or {})}
    pages = gh_api(endpoint + "?" + urlencode(query), paginate=True)
    if not all(isinstance(page, list) for page in pages):
        raise ValueError(f"Expected list pages: {endpoint}")
    return [row for page in pages for row in page]


def verify_diff(diff, file_count, stats):
    headers = additions = deletions = 0
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            headers += 1
            in_hunk = False
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk:
            additions += line.startswith("+")
            deletions += line.startswith("-")
    return {
        "file_headers": headers,
        "additions": additions,
        "deletions": deletions,
        "matches_api_file_count_and_stats": (
            headers == file_count
            and additions == stats.get("additions", 0)
            and deletions == stats.get("deletions", 0)
        ),
    }


def inventory_repo(repo, since, until):
    full_name = repo["full_name"]
    endpoint = "repos/" + full_name
    branches = list_api(endpoint + "/branches")
    tags = list_api(endpoint + "/tags")
    tips = {}
    for kind, rows in (("branch", branches), ("tag", tags)):
        for ref in rows:
            tips.setdefault(ref["commit"]["sha"], []).append({"kind": kind, "name": ref["name"]})
    commits = {}
    for tip_sha, refs in tips.items():
        rows = list_api(endpoint + "/commits", {"sha": tip_sha, "since": iso(since), "until": iso(until)})
        for row in rows:
            # The reporting window is explicitly committer time, not push time.
            committed = parse_date(row["commit"]["committer"]["date"])
            if not since <= committed <= until:
                continue
            entry = commits.setdefault(row["sha"], {"repository": full_name, "sha": row["sha"], "refs": []})
            entry["refs"].extend(refs)
    inventory = {
        "repository": full_name,
        "private": repo["private"],
        "default_branch": repo["default_branch"],
        "branch_count": len(branches),
        "tag_count": len(tags),
        "tip_count": len(tips),
        "branches": [{"name": x["name"], "sha": x["commit"]["sha"]} for x in branches],
        "tags": [{"name": x["name"], "sha": x["commit"]["sha"]} for x in tags],
        "commit_count": len(commits),
    }
    return inventory, list(commits.values())


def fetch_detail(entry, output):
    repo = entry["repository"]
    sha = entry["sha"]
    endpoint = f"repos/{repo}/commits/{sha}"
    pages = gh_api(endpoint + "?per_page=100", paginate=True)
    if not pages or any(page.get("sha") != sha for page in pages):
        raise ValueError(f"Unexpected commit response: {repo}@{sha}")
    detail = pages[0]
    files = [f for page in pages for f in page.get("files", [])]
    if len({f["filename"] for f in files}) != len(files):
        raise ValueError(f"Duplicate file pagination: {repo}@{sha}")
    detail["files"] = files
    directory = output / "commits" / repo
    directory.mkdir(parents=True, exist_ok=True)
    detail_file = directory / (sha + ".json")
    diff_file = directory / (sha + ".diff")
    diff_error = None
    diff_verification = None
    try:
        diff = gh_api(endpoint, media="application/vnd.github.diff")
        diff_file.write_text(diff, encoding="utf-8")
        diff_verification = verify_diff(diff, len(files), detail.get("stats", {}))
    except RuntimeError as error:
        diff_error = str(error)
    write_json(detail_file, detail)
    metadata = detail["commit"]
    missing_patch = [f["filename"] for f in files if "patch" not in f and f.get("changes", 0) > 0]
    return {
        **entry,
        "url": detail["html_url"],
        "author": {"login": (detail.get("author") or {}).get("login"), **metadata["author"]},
        "committer": {"login": (detail.get("committer") or {}).get("login"), **metadata["committer"]},
        "message": metadata["message"],
        "parents": [p["sha"] for p in detail["parents"]],
        "stats": detail.get("stats", {}),
        "file_count": len(files),
        "files": [{key: f[key] for key in ("filename", "previous_filename", "status", "additions", "deletions", "changes") if key in f} for f in files],
        "files_without_json_patch": missing_patch,
        "file_limit_may_apply": len(files) >= 3000,
        "detail_file": detail_file.relative_to(output).as_posix(),
        "diff_file": None if diff_error else diff_file.relative_to(output).as_posix(),
        "diff_error": diff_error,
        "diff_verification": diff_verification,
    }


def make_report(output, summary, commits, tz):
    lines = [
        f"{summary['organization']} 最近 {summary['days']} 天的提交详情",
        "=" * 72,
        f"开始：{parse_date(summary['since']).astimezone(tz).isoformat(timespec='seconds')}",
        f"结束：{parse_date(summary['until']).astimezone(tz).isoformat(timespec='seconds')}",
        f"时区：{tz.key}；按 Git committer 时间筛选，包含合并提交。",
        f"仓库 {summary['repositories_scanned']}/{summary['repositories_total']}；分支 {summary['branches_scanned']}；标签 {summary['tags_scanned']}。",
        f"有提交的仓库 {summary['active_repositories']}；仓库内按 SHA 去重后 {summary['repository_commit_count']} 条；跨仓库唯一 SHA {summary['unique_sha_count']}。",
        f"其中 {summary['commits_outside_default_branch']} 条不在默认分支上；作者账号/身份 {summary['author_identity_count']} 个。",
        "范围：当前账号可访问仓库中，扫描时各分支和标签指向的历史；分支 tip SHA 已固定。",
        "时间指提交对象记录的时间，并非 GitHub 收到 push 的时间。未推送和已不可达的提交不在范围内。",
        "JSON 保存作者/提交者、完整消息、父提交、签名验证、行数和文件 patch；.diff 保存 API 返回的统一 diff。",
        f"JSON 中 {summary['json_files_without_patch']} 处文件变更没有内联 patch，独立 .diff 文件另外下载并核对。",
        "二进制文件仅保留变更信息，无法保证有可读文本 diff。",
        f"API 错误 {len(summary['errors'])}；diff 下载失败 {summary['diff_failures']}；diff 文件数/增删行数校验不一致 {summary['diff_verification_failures']}；达到文件上限的提交 {summary['file_limit_commits']}。",
        "",
        "按仓库统计：",
    ]
    lines += [f"  {repo}: {count}" for repo, count in summary["by_repository"].items()]
    lines += ["", "按作者统计（GitHub 账号，无法关联时使用 Git 作者名）："]
    lines += [f"  {author}: {count}" for author, count in summary["by_author"].items()]
    if summary["errors"]:
        lines += ["", "错误："] + [json.dumps(e, ensure_ascii=False) for e in summary["errors"]]
    lines += ["", "逐条提交（按提交时间从新到旧）：", ""]
    for item in commits:
        author = item["author"].get("login") or item["author"]["name"]
        committed = parse_date(item["committer"]["date"]).astimezone(tz).isoformat(timespec="seconds")
        authored = parse_date(item["author"]["date"]).astimezone(tz).isoformat(timespec="seconds")
        lines += [
            "-" * 72,
            f"{item['repository']}  {item['sha']}",
            f"作者：{author} ({item['author']['name']})；作者时间：{authored}",
            f"提交者：{item['committer'].get('login') or item['committer']['name']}；提交时间：{committed}",
            "引用：" + ", ".join(f"{r['kind']}:{r['name']}" for r in item["refs"]),
            "父提交：" + ", ".join(item["parents"]),
            item["url"],
            "",
            item["message"],
            "",
            f"文件 {item['file_count']}；新增 {item['stats'].get('additions', 0)} 行；删除 {item['stats'].get('deletions', 0)} 行。",
        ]
        lines += [f"  {f['status']:10} +{f.get('additions', 0)} -{f.get('deletions', 0)}  {f['filename']}" for f in item["files"]]
        lines += [f"详情：{item['detail_file']}", f"Diff：{item['diff_file'] or item['diff_error']}"]
        if item["files_without_json_patch"]:
            lines += ["JSON 中没有 patch 的文件：" + ", ".join(item["files_without_json_patch"])]
        lines += [""]
    (output / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", default="ucrparlay")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--until", help="ISO 8601 cutoff with timezone; default now")
    parser.add_argument("--timezone", default="America/Los_Angeles")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=Path, help="New output directory (must not already exist)")
    args = parser.parse_args()
    if args.days <= 0 or not 1 <= args.workers <= 8:
        parser.error("--days must be positive; --workers must be 1..8")
    tz = ZoneInfo(args.timezone)
    until = parse_date(args.until) if args.until else datetime.now(timezone.utc).replace(microsecond=0)
    since = until - timedelta(days=args.days)
    output = args.output or Path(__file__).resolve().parent / "private/exports" / f"{args.org}-{until.strftime('%Y%m%dT%H%M%SZ')}-{args.days}d"
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    log(f"Window: {iso(since)} .. {iso(until)}; output: {output}")
    repos = list_api(f"orgs/{quote(args.org, safe='')}/repos", {"type": "all"})
    write_json(output / "repositories.json", repos)
    log(f"Repositories: {len(repos)}. Scanning all current branches and tags.")
    inventory, entries, errors = [], [], []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(inventory_repo, repo, since, until): repo["full_name"] for repo in repos}
        for index, future in enumerate(as_completed(futures), 1):
            repo = futures[future]
            try:
                info, found = future.result()
                inventory.append(info)
                entries.extend(found)
                if found:
                    log(f"  {repo}: {len(found)} commits, {info['branch_count']} branches, {info['tag_count']} tags")
            except Exception as error:
                errors.append({"stage": "inventory", "repository": repo, "error": str(error)})
                log(f"  ERROR {repo}: {error}")
            if index % 10 == 0 or index == len(repos):
                log(f"Inventory: {index}/{len(repos)} repositories; {len(entries)} repository/commit pairs")
    inventory.sort(key=lambda x: x["repository"])
    write_json(output / "inventory.json", inventory)
    write_json(output / "commit-index.json", entries)
    commits = []
    log(f"Fetching JSON details and full API diff for {len(entries)} commits.")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_detail, entry, output): entry for entry in entries}
        for index, future in enumerate(as_completed(futures), 1):
            entry = futures[future]
            try:
                commits.append(future.result())
            except Exception as error:
                errors.append({"stage": "detail", "repository": entry["repository"], "sha": entry["sha"], "error": str(error)})
                log(f"  ERROR {entry['repository']}@{entry['sha']}: {error}")
            if index % 10 == 0 or index == len(entries):
                log(f"Details: {index}/{len(entries)}")
    commits.sort(key=lambda x: (x["committer"]["date"], x["repository"], x["sha"]), reverse=True)
    by_repo = Counter(x["repository"] for x in commits)
    by_author = Counter(x["author"].get("login") or x["author"]["name"] for x in commits)
    defaults = {r["repository"]: r["default_branch"] for r in inventory}
    summary = {
        "organization": args.org,
        "days": args.days,
        "since": iso(since),
        "until": iso(until),
        "display_timezone": args.timezone,
        "time_basis": "committer date; not push date",
        "ref_scope": "all current branches and tags at captured tip SHAs; includes merge commits",
        "repositories_total": len(repos),
        "repositories_scanned": len(inventory),
        "public_repositories": sum(not r["private"] for r in repos),
        "private_repositories": sum(r["private"] for r in repos),
        "branches_scanned": sum(r["branch_count"] for r in inventory),
        "tags_scanned": sum(r["tag_count"] for r in inventory),
        "active_repositories": len(by_repo),
        "repository_commit_count": len(commits),
        "unique_sha_count": len({x["sha"] for x in commits}),
        "author_identity_count": len(by_author),
        "commits_outside_default_branch": sum(
            not any(r["kind"] == "branch" and r["name"] == defaults[c["repository"]] for r in c["refs"])
            for c in commits
        ),
        "by_repository": dict(by_repo.most_common()),
        "by_author": dict(by_author.most_common()),
        "diff_failures": sum(x["diff_file"] is None for x in commits),
        "diff_verification_failures": sum(
            x["diff_verification"] is not None and not x["diff_verification"]["matches_api_file_count_and_stats"]
            for x in commits
        ),
        "file_limit_commits": sum(x["file_limit_may_apply"] for x in commits),
        "json_files_without_patch": sum(len(x["files_without_json_patch"]) for x in commits),
        "errors": errors,
    }
    write_json(output / "commits.json", commits)
    write_json(output / "summary.json", summary)
    make_report(output, summary, commits, tz)
    log(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if errors or summary["diff_failures"] or summary["diff_verification_failures"] or summary["file_limit_commits"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
