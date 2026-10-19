import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from email.message import Message

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

    def test_authentication_retries_rate_limit(self):
        client = SigmaClient("https://example.test", client_id="id", client_secret="secret", max_retries=1)
        headers = Message()
        error = HTTPError("https://example.test/v2/auth/token", 429, "rate limited", headers, None)
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        with patch("sigma_assessment.client.time.sleep"), patch(
            "sigma_assessment.client.urllib.request.urlopen", side_effect=[error, response]
        ), patch("sigma_assessment.client.json.load", return_value={"access_token": "token", "expires_in": 3600}):
            client._authenticate()
        self.assertEqual(client.access_token, "token")


if __name__ == "__main__":
    unittest.main()
