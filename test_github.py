import unittest
import zlib
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from github_bridge import DashboardConnection, encode_dashboard
from github_stats import rankings


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.end = datetime(2026, 10, 15, 20, tzinfo=timezone.utc)
        self.start = self.end - timedelta(days=7)

    def commit(self, sha, repo="one", author="alice", additions=10, deletions=5, when=None, parents=None):
        return {"sha":sha, "repository":"lab/"+repo,
                "author":{"login":author,"name":author,"email":"local@example.test"},
                "committer":{"date":(when or self.end).isoformat()},
                "stats":{"additions":additions,"deletions":deletions}, "parents":parents or ["parent"]}

    def test_churn_sorting_and_branch_deduplication(self):
        a = self.commit("a", additions=1, deletions=100)
        b = self.commit("b", repo="two", author="bob", additions=90, deletions=0)
        result = rankings([a,a,b],"lab",self.start,self.end,2)
        self.assertEqual([r["name"] for r in result["contributors"]],["alice","bob"])
        self.assertEqual(result["contributors"][0]["lines"],101)
        self.assertEqual(result["commit_count"],2)

    def test_merge_and_time_window(self):
        commits = [self.commit("before",when=self.start-timedelta(seconds=1)),
                   self.commit("boundary",when=self.start),
                   self.commit("merge",parents=["a","b"]),
                   self.commit("future",when=self.end+timedelta(seconds=1))]
        result = rankings(commits,"lab",self.start,self.end,1)
        self.assertEqual(result["commit_count"],1)
        self.assertEqual(result["excluded_merge_commits"],1)

    def test_forked_sha_counted_once_per_person_and_per_repo(self):
        result = rankings([self.commit("same"),self.commit("same",repo="two")],"lab",self.start,self.end,2)
        self.assertEqual(result["contributors"][0]["lines"],15)
        self.assertEqual(sum(r["lines"] for r in result["repositories"]),30)

    def test_git_author_name_joins_same_github_login(self):
        fallback = self.commit("b",author="seanxpw")
        fallback["author"]["login"] = None
        result = rankings([self.commit("a",author="seanxpw"),fallback],"lab",self.start,self.end,1)
        self.assertEqual(len(result["contributors"]),1)
        self.assertEqual(result["contributors"][0]["commits"],2)

    def test_negative_counts_are_rejected(self):
        with self.assertRaises(ValueError):
            rankings([self.commit("a",deletions=-1)],"lab",self.start,self.end,1)

    def test_checksum_and_exact_ack_matching(self):
        report = rankings([self.commit("a")],"lab",self.start,self.end,1)
        data, checksum = encode_dashboard(report)
        prefix, received, payload = data.rstrip(b"\n").split(b" ",2)
        self.assertEqual(prefix,b"GHD1")
        self.assertEqual(received.decode(),f"{zlib.crc32(payload):08x}")
        connection = object.__new__(DashboardConnection)
        connection.command = Mock()
        connection.readline = Mock(side_effect=["GHD1 ACK deadbeef rows=1/1",f"GHD1 ACK {checksum} rows=1/1"])
        self.assertEqual(connection.send(report),f"GHD1 ACK {checksum} rows=1/1")


if __name__ == "__main__":
    unittest.main()
