import unittest
import lambda_function as handler


class RetiredAuthorizerTests(unittest.TestCase):
    def test_all_legacy_credentials_denied(self):
        for value in ("Basic dXNlcjpwYXNz", "Bearer products-poc.token", "Bearer jwt", ""):
            with self.subTest(value=value):
                self.assertEqual({"isAuthorized": False, "context": {}},
                                 handler.lambda_handler({"headers": {"authorization": value}}, None))
