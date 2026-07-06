import base64
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch


os.environ.setdefault("AUTH_PRINCIPALS_TABLE", "auth-principals")
os.environ.setdefault("AUTH_ROLES_TABLE", "auth-roles")

sys.modules.setdefault(
    "boto3",
    SimpleNamespace(
        client=lambda _service_name: SimpleNamespace(),
        resource=lambda _service_name: SimpleNamespace(),
    ),
)

import lambda_function as handler


class FakeTable:
    def __init__(self, items=None):
        self.items = items or {}

    def get_item(self, Key):
        key_name, key_value = next(iter(Key.items()))
        item = self.items.get(key_value)
        return {"Item": item} if item is not None else {}


class FakeDynamoResource:
    def __init__(self, tables):
        self.tables = tables

    def Table(self, name):
        return self.tables[name]


class FakeSecretsManager:
    def __init__(self, secrets):
        self.secrets = secrets

    def get_secret_value(self, SecretId):
        return {"SecretString": json.dumps(self.secrets[SecretId])}


class AuthorizerLambdaTests(unittest.TestCase):
    def setUp(self):
        self.principals = FakeTable(
            {
                "basic#products-poc-user": {
                    "principal_id": "products-poc-user",
                    "role_name": "products-poc-upload",
                    "auth_type": "basic",
                    "enabled": True,
                    "secret_name": "user-secret",
                },
                "bearer#products-poc-api": {
                    "principal_id": "products-poc-api",
                    "role_name": "products-poc-upload",
                    "auth_type": "bearer",
                    "enabled": True,
                    "secret_name": "system-secret",
                },
            }
        )
        self.roles = FakeTable(
            {
                "products-poc-upload": {
                    "role_name": "products-poc-upload",
                    "allowed_client_ids": ["products-poc"],
                }
            }
        )
        self.fake_dynamo = FakeDynamoResource(
            {
                handler.AUTH_PRINCIPALS_TABLE: self.principals,
                handler.AUTH_ROLES_TABLE: self.roles,
            }
        )
        self.fake_secrets = FakeSecretsManager(
            {
                "user-secret": {"password": "super-secret"},
                "system-secret": {"bearerToken": "api-token"},
            }
        )

    def _event(self, authorization):
        return {"headers": {"authorization": authorization}}

    @patch.object(handler, "DYNAMODB")
    @patch.object(handler, "SECRETS_MANAGER")
    def test_basic_auth_is_authorized(self, patched_secrets, patched_dynamo):
        patched_dynamo.Table = self.fake_dynamo.Table
        patched_secrets.get_secret_value = self.fake_secrets.get_secret_value

        token = base64.b64encode(b"products-poc-user:super-secret").decode("ascii")
        response = handler.lambda_handler(self._event(f"Basic {token}"), None)

        self.assertTrue(response["isAuthorized"])
        self.assertEqual("products-poc-user", response["context"]["principalId"])
        self.assertEqual("products-poc", response["context"]["allowedClientIds"])

    @patch.object(handler, "DYNAMODB")
    @patch.object(handler, "SECRETS_MANAGER")
    def test_bearer_auth_is_authorized(self, patched_secrets, patched_dynamo):
        patched_dynamo.Table = self.fake_dynamo.Table
        patched_secrets.get_secret_value = self.fake_secrets.get_secret_value

        response = handler.lambda_handler(self._event("Bearer products-poc-api.api-token"), None)

        self.assertTrue(response["isAuthorized"])
        self.assertEqual("products-poc-api", response["context"]["principalId"])
        self.assertEqual("bearer", response["context"]["authType"])

    @patch.object(handler, "DYNAMODB")
    @patch.object(handler, "SECRETS_MANAGER")
    def test_invalid_secret_is_denied(self, patched_secrets, patched_dynamo):
        patched_dynamo.Table = self.fake_dynamo.Table
        patched_secrets.get_secret_value = self.fake_secrets.get_secret_value

        token = base64.b64encode(b"products-poc-user:wrong-secret").decode("ascii")
        response = handler.lambda_handler(self._event(f"Basic {token}"), None)

        self.assertFalse(response["isAuthorized"])
        self.assertEqual({}, response["context"])


if __name__ == "__main__":
    unittest.main()
