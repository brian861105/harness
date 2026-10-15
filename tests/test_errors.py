import unittest

import httpx

from harness_app.errors import HarnessError, check_http


class ErrorTests(unittest.TestCase):
    def test_http_failure_keeps_safe_error_code_without_server_description(self):
        response = httpx.Response(403, json={
            'error': {'code': 'subscription_sharing_user_not_eligible',
                      'message': 'do-not-display-sensitive-diagnostics'},
        })
        with self.assertRaises(HarnessError) as caught:
            check_http(response)
        self.assertIn('403', str(caught.exception))
        self.assertIn('subscription_sharing_user_not_eligible', str(caught.exception))
        self.assertNotIn('do-not-display', str(caught.exception))
