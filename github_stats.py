"""Weekly GitHub changed-line rankings, collected with the logged-in gh account."""
from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from github_fetch import gh_api, inventory_repo, list_api, parse_date


def parallel_map(function, items, workers):
    """Cancel queued requests on Ctrl-C instead of starting more API calls."""
    pool = ThreadPoolExecutor(max_workers=workers)
    futures = [pool.submit(function, item) for item in items]
    try:
        results = [future.result() for future in as_completed(futures)]
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    return results


def _nonnegative(value):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ValueError("GitHub line counts must be nonnegative integers")
    return value


def rankings(commits, org, since, until, repo_count):
    people, repos, seen_repo, seen_author = {}, {}, set(), set()
    skipped_merges = 0

    def add(table, name, additions, deletions):
        row = table.setdefault(name, {"name": name, "additions": 0, "deletions": 0, "lines": 0, "commits": 0})
        row["additions"] += additions
        row["deletions"] += deletions
        row["lines"] += additions + deletions
        row["commits"] += 1

    for commit in commits:
        repository, sha = commit["repository"], commit["sha"]
        if repository.split("/")[0].casefold() != org.casefold():
            raise ValueError("Snapshot contains repositories outside the selected organization")
        when = parse_date(commit["committer"]["date"])
        if not since <= when <= until or (repository, sha) in seen_repo:
            continue
        seen_repo.add((repository, sha))
        # A merge's first-parent diff repeats changes already counted in its commits.
        if len(commit.get("parents", [])) > 1:
            skipped_merges += 1
            continue
        additions = _nonnegative(commit["stats"]["additions"])
        deletions = _nonnegative(commit["stats"]["deletions"])
        add(repos, repository.split("/", 1)[1], additions, deletions)
        if sha not in seen_author:
            seen_author.add(sha)
            author = commit["author"].get("login") or commit["author"].get("name") or commit["author"]["email"]
            add(people, author, additions, deletions)
    order = lambda rows: sorted(rows.values(), key=lambda r: (-r["lines"], -r["additions"], r["name"].casefold()))
    return {
        "org": org,
        "since": int(since.timestamp()),
        "until": int(until.timestamp()),
        "generated_at": int(datetime.now(timezone.utc).timestamp()),
        "metric": "additions + deletions; all files with GitHub text line statistics",
        "repo_count": repo_count,
        "commit_count": sum(r["commits"] for r in repos.values()),
        "excluded_merge_commits": skipped_merges,
        "contributors": order(people),
        "repositories": order(repos),
    }


def from_snapshot(path: Path, org: str):
    """Read a complete export, keeping its original seven-day reporting window."""
    summary = json.loads((path.parent / "summary.json").read_text())
    if summary.get("errors") or summary.get("repositories_scanned") != summary.get("repositories_total"):
        raise ValueError("Refusing an incomplete GitHub snapshot")
    if summary["organization"].casefold() != org.casefold():
        raise ValueError("Snapshot organization does not match --org")
    since, until = parse_date(summary["since"]), parse_date(summary["until"])
    if until - since != timedelta(days=7):
        raise ValueError("The board displays a seven-day window; snapshot must span seven days")
    report = rankings(json.loads(path.read_text()), org, since, until, summary["repositories_total"])
    report["source"] = "snapshot"
    return report


class GithubReader:
    def __init__(self, org="ucrparlay", cache_dir=Path("private/github-cache"), workers=4):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", org):
            raise ValueError("Invalid GitHub organization")
        self.org, self.cache_dir, self.workers = org, Path(cache_dir), workers

    def detail(self, entry):
        repository, sha = entry["repository"], entry["sha"]
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Invalid commit SHA")
        directory = self.cache_dir / repository
        path = directory / (sha + ".json")
        if path.is_file():
            detail = json.loads(path.read_text())
        else:
            pages = gh_api(f"repos/{repository}/commits/{sha}?per_page=100", paginate=True)
            detail = dict(pages[0])
            detail["files"] = [f for page in pages for f in page.get("files", [])]
            if len(detail["files"]) >= 3000:
                raise ValueError(f"GitHub file limit reached: {repository}@{sha}")
            if len({f["filename"] for f in detail["files"]}) != len(detail["files"]):
                raise ValueError("Duplicate files returned by GitHub pagination")
            directory.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(detail, ensure_ascii=False))
        if detail["sha"] != sha:
            raise ValueError("Cached commit SHA mismatch")
        return {
            **entry,
            "author": {**detail["commit"]["author"], "login": (detail.get("author") or {}).get("login")},
            "committer": detail["commit"]["committer"],
            "stats": detail["stats"],
            "parents": [p["sha"] for p in detail["parents"]],
        }

    def read(self, now=None):
        until = (now or datetime.now(timezone.utc)).replace(microsecond=0)
        since = until - timedelta(days=7)
        repos = list_api(f"orgs/{self.org}/repos", {"type": "all"})
        print(f"Scanning {len(repos)} repositories (all branches and tags)...", file=sys.stderr, flush=True)
        entries = []
        results = parallel_map(lambda repo: inventory_repo(repo, since, until), repos, self.workers)
        for _, commits in results:
            entries.extend(commits)
        print(f"Reading {len(entries)} commit details (immutable SHA cache enabled)...", file=sys.stderr, flush=True)
        commits = parallel_map(self.detail, entries, self.workers)
        report = rankings(commits, self.org, since, until, len(repos))
        report["source"] = "live"
        return report
