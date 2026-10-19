import unittest
from unittest.mock import patch

from sigma_assessment.client import SigmaClient


class ClientTests(unittest.TestCase):
    def test_paginate_uses_opaque_next_page(self):
        client = SigmaClient("https://example.test", access_token="token")
        calls = []

        def fake_request(path, params):
            calls.append((path, dict(params)))
            if len(calls) == 1:
                return {"entries": [{"id": 1}], "nextPage": "opaque token"}
            return {"entries": [{"id": 2}], "nextPage": None}

        with patch.object(client, "request", side_effect=fake_request):
            self.assertEqual(list(client.paginate("/things")), [{"id": 1}, {"id": 2}])
        self.assertEqual(calls[1][1]["page"], "opaque token")

    def test_paginate_accepts_list_response(self):
        client = SigmaClient("https://example.test", access_token="token")
        with patch.object(client, "request", return_value=[{"id": 1}]):
            self.assertEqual(list(client.paginate("/things")), [{"id": 1}])


if __name__ == "__main__":
    unittest.main()
