"""Offline security tests: signed request validation and replay rejection."""
from __future__ import annotations

import hmac
from hashlib import sha256
import os
import unittest
from unittest.mock import patch

from . import retrieval_api


class InternalAuthTests(unittest.TestCase):
    def setUp(self):
        with retrieval_api._nonce_lock:
            retrieval_api._seen_nonces.clear()
        self.secret = "this_is_a_local_test_key_longer_than_32_characters"
        self.body = b'{"organizationId":"cmvw9ro3w000548l1gkuh2gx8"}'
        self.timestamp = "1700000000000"
        self.nonce = "a" * 32
        self.now = int(self.timestamp)
        self.headers = {
            "x-docmind-internal-timestamp": self.timestamp,
            "x-docmind-internal-nonce": self.nonce,
            "x-docmind-internal-signature": hmac.new(
                self.secret.encode(),
                (self.timestamp + "." + self.nonce + ".").encode() + self.body,
                sha256,
            ).hexdigest(),
        }

    def test_valid_signature_and_single_use(self):
        with patch.dict(os.environ, {"DOCMIND_INTERNAL_RETRIEVAL_SECRET": self.secret}):
            self.assertTrue(retrieval_api.verify_signed_request(self.body, self.headers, now_ms=self.now))
            self.assertFalse(retrieval_api.verify_signed_request(self.body, self.headers, now_ms=self.now))

    def test_tampered_body_rejected(self):
        with patch.dict(os.environ, {"DOCMIND_INTERNAL_RETRIEVAL_SECRET": self.secret}):
            self.assertFalse(retrieval_api.verify_signed_request(self.body + b"x", self.headers, now_ms=self.now))

    def test_stale_time_rejected(self):
        with patch.dict(os.environ, {"DOCMIND_INTERNAL_RETRIEVAL_SECRET": self.secret}):
            self.assertFalse(retrieval_api.verify_signed_request(self.body, self.headers, now_ms=self.now + 120000))

    def test_missing_secret_rejected(self):
        with patch.dict(os.environ, {"DOCMIND_INTERNAL_RETRIEVAL_SECRET": "short"}):
            self.assertFalse(retrieval_api.verify_signed_request(self.body, self.headers, now_ms=self.now))


if __name__ == "__main__":
    unittest.main()
