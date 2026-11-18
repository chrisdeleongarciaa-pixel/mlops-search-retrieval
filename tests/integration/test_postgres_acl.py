"""The ACL authority against PostgreSQL with the repository migrations applied."""

import os
import unittest
from uuid import uuid4

DSN = os.environ.get("ACCEPT_REVISION_TEST_DSN")
if not DSN:
    raise unittest.SkipTest("Set ACCEPT_REVISION_TEST_DSN for the ACL authority")

from search_retrieval.adapters.postgres_acl import PostgresAcl


class PostgresAclTests(unittest.TestCase):
    def setUp(self):
        self.source = f"acl-test-{uuid4().hex[:8]}"
        self.authority = PostgresAcl(DSN, tenant="bench", source=self.source)

    def test_private_documents_are_denied_until_granted_and_after_revocation(self):
        self.authority.make_private({"d1": "alice", "d2": "bob"})
        self.addCleanup(self.authority.make_public, ["d1", "d2"])
        self.assertEqual(self.authority.denied("alice", ["d1", "d2", "public-doc"]), {"d2"})
        self.assertEqual(self.authority.denied(None, ["d1", "d2"]), {"d1", "d2"})
        self.authority.revoke("d1", "alice")
        self.assertEqual(self.authority.denied("alice", ["d1"]), {"d1"})
        with self.assertRaises(ValueError):
            self.authority.revoke("d1", "alice")
        self.authority.make_public(["d1", "d2"])
        self.assertEqual(self.authority.denied("carol", ["d1", "d2"]), set())

    def test_an_unreachable_authority_fails_closed(self):
        from search_retrieval.query.eligible import SearchUnavailable

        broken = PostgresAcl("postgresql://nobody@127.0.0.1:1/none?connect_timeout=1", tenant="bench", source="x")
        with self.assertRaises(SearchUnavailable):
            broken.denied("alice", ["d1"])
