import os
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("TRANSFER_CLIENTS_TABLE", "transfer-clients")
os.environ.setdefault("MULTIPART_SESSIONS_TABLE", "multipart-sessions")
os.environ.setdefault("UPLOAD_BUCKET_NAME", "upload-bucket")
os.environ.setdefault("UPLOAD_BUCKET_KMS_KEY_ARN", "arn:aws:kms:eu-west-2:111122223333:key/example")
os.environ.setdefault("PRESIGNED_URL_EXPIRY_SECONDS", "900")
os.environ.setdefault("MAX_PRESIGNED_URL_EXPIRY_SECONDS", "3600")
os.environ.setdefault("SINGLE_PUT_LIMIT_BYTES", "5368709120")
os.environ.setdefault("MULTIPART_DEFAULT_PART_SIZE_BYTES", "67108864")
os.environ.setdefault("MULTIPART_INITIAL_PRESIGN_PARTS", "10")
os.environ.setdefault("MULTIPART_MAX_PARTS", "10000")

sys.modules["boto3"] = SimpleNamespace(
    client=lambda _service_name, **_kwargs: SimpleNamespace(),
    resource=lambda _service_name, **_kwargs: SimpleNamespace(),
)
sys.modules["botocore"] = SimpleNamespace()
sys.modules["botocore.config"] = SimpleNamespace(Config=lambda **_kwargs: SimpleNamespace())

os.environ["ENTRA_TENANT_ID"] = "tenant-test"
os.environ["ENTRA_API_AUDIENCE"] = "api-test"
os.environ["ENTRA_APPLICATION_CLIENTS"] = json.dumps({"consumer-test": ["products-poc"], "other-consumer": ["other-client"]})

import lambda_function as handler


class FakeTable:
    def __init__(self, items=None):
        self.items = items or {}

    def get_item(self, Key):
        key_name, key_value = next(iter(Key.items()))
        item = self.items.get(key_value)
        return {"Item": item} if item is not None else {}

    def put_item(self, Item):
        self.items[Item["transfer_ticket"]] = Item

    def update_item(self, Key, **_kwargs):
        transfer_ticket = Key["transfer_ticket"]
        if transfer_ticket not in self.items:
            raise KeyError(transfer_ticket)


class FakeDynamoResource:
    def __init__(self, tables):
        self.tables = tables

    def Table(self, name):
        return self.tables[name]


class FakeS3Client:
    def __init__(self):
        self.create_calls = []
        self.presign_calls = []
        self.complete_calls = []
        self.abort_calls = []

    def generate_presigned_url(self, ClientMethod, Params, ExpiresIn, HttpMethod):
        self.presign_calls.append(
            {
                "ClientMethod": ClientMethod,
                "Params": Params,
                "ExpiresIn": ExpiresIn,
                "HttpMethod": HttpMethod,
            }
        )
        if ClientMethod == "upload_part":
            return f"https://example.test/upload-part/{Params['PartNumber']}"
        return "https://example.test/upload"

    def create_multipart_upload(self, **kwargs):
        self.create_calls.append(kwargs)
        return {"UploadId": "upload-123"}

    def complete_multipart_upload(self, **kwargs):
        self.complete_calls.append(kwargs)
        return {"ETag": '"completed"'}

    def abort_multipart_upload(self, **kwargs):
        self.abort_calls.append(kwargs)


class UploadLambdaTests(unittest.TestCase):
    def setUp(self):
        self.transfer_clients = FakeTable(
            {
                "products-poc": {
                    "client_id": "products-poc",
                    "enabled": True,
                    "key_prefix": "products-poc/uploads",
                    "max_upload_size_bytes": 107374182400,
                    "allowed_content_types": ["text/csv"],
                }
            }
        )
        self.multipart_sessions = FakeTable()
        self.fake_dynamo = FakeDynamoResource(
            {
                handler.TRANSFER_CLIENTS_TABLE: self.transfer_clients,
                handler.MULTIPART_SESSIONS_TABLE: self.multipart_sessions,
            }
        )
        self.fake_s3 = FakeS3Client()

    def _event(self, route_key, body, path_parameters=None):
        return {
            "routeKey": route_key,
            "body": json.dumps(body) if body is not None else None,
            "pathParameters": path_parameters or {},
            "requestContext": {
                "authorizer": {
                    "jwt": {"claims": {
                        "iss": "https://login.microsoftonline.com/tenant-test/v2.0",
                        "tid": "tenant-test", "aud": "api-test", "ver": "2.0",
                        "idtyp": "app", "azp": "consumer-test",
                        "roles": "[FileTransfer.Upload]",
                    }}
                }
            },
        }

    def test_malformed_create_fields_rejected_before_storage(self):
        valid = {"clientId": "products-poc", "fileName": "example.csv", "contentType": "text/csv", "sizeBytes": 20}
        cases = [("sizeBytes", True), ("sizeBytes", 1.5), ("sizeBytes", "20"),
                 ("requestedExpirySeconds", False), ("requestedExpirySeconds", 2.5),
                 ("fileName", ["file.csv"]), ("fileName", "bad\nname.csv"),
                 ("clientId", ["products-poc"]), ("contentType", {}), ("contentMd5", [])]
        for field, value in cases:
            with self.subTest(field=field, value=value), patch.object(handler, "DYNAMODB") as db, patch.object(handler, "S3_CLIENT") as s3:
                body = dict(valid, **{field: value})
                response = handler.lambda_handler(self._event("POST /transfer-tickets", body), None)
                self.assertEqual(400, response["statusCode"])
                db.Table.assert_not_called()
                self.assertFalse(s3.mock_calls)

    def test_malformed_encoded_body_returns_400(self):
        event = self._event("POST /transfer-tickets", {})
        event.update(body="!!!!", isBase64Encoded=True)
        self.assertEqual(400, handler.lambda_handler(event, None)["statusCode"])

    def test_part_ranges_are_bounded_before_expansion(self):
        for request in ({"partNumberStart": 1, "partNumberEnd": 10**15},
                        {"partNumbers": [True]}, {"partNumbers": [1.5]},
                        {"partNumberStart": "1", "partNumberEnd": 2},
                        {"partNumbers": [1] * (handler.MULTIPART_MAX_PARTS + 1)}):
            with self.subTest(request=str(request)[:80]):
                parts, error = handler._normalise_part_numbers(request, {"total_parts": 3})
                self.assertIsNone(parts)
                self.assertEqual(400, error["statusCode"])

    def test_invalid_identity_rejected_before_any_side_effect(self):
        cases = [
            {"tid": "wrong"}, {"aud": "wrong"}, {"iss": "https://attacker.test"},
            {"ver": "1.0"}, {"idtyp": "user"}, {"idtyp": None},
            {"scp": "FileTransfer.Upload"}, {"azp": "unmapped"}, {"azp": None},
            {"roles": []}, {"roles": "Other.FileTransfer.Upload"},
            {"roles": "FileTransfer.Upload.Extra"}, {"roles": None},
        ]
        for route in ("POST /transfer-tickets", "POST /transfer-tickets/{transferTicket}/parts",
                      "POST /transfer-tickets/{transferTicket}/complete", "DELETE /transfer-tickets/{transferTicket}"):
            for updates in cases:
                with self.subTest(route=route, updates=updates), patch.object(handler, "DYNAMODB") as db, patch.object(handler, "S3_CLIENT") as s3:
                    event = self._event(route, {})
                    event["requestContext"]["authorizer"]["jwt"]["claims"].update(updates)
                    self.assertEqual(403, handler.lambda_handler(event, None)["statusCode"])
                    db.Table.assert_not_called()
                    self.assertFalse(s3.mock_calls)

    def test_legacy_context_and_headers_cannot_grant_access(self):
        event = self._event("POST /transfer-tickets", {})
        event["requestContext"]["authorizer"] = {"lambda": {"allowedClientIds": "products-poc"}}
        event["headers"] = {"authorization": "Bearer products-poc.token"}
        self.assertEqual(403, handler.lambda_handler(event, None)["statusCode"])

    def test_missing_tenant_or_audience_fails_closed(self):
        for field in ("ENTRA_TENANT_ID", "ENTRA_API_AUDIENCE"):
            with patch.dict(os.environ, {field: ""}):
                self.assertEqual(403, handler.lambda_handler(self._event("POST /transfer-tickets", {}), None)["statusCode"])

    def test_other_client_cannot_create_ticket(self):
        event = self._event("POST /transfer-tickets", {"clientId": "other-client", "fileName": "test.txt", "sizeBytes": 5})
        with patch.object(handler, "DYNAMODB") as db, patch.object(handler, "S3_CLIENT") as s3:
            self.assertEqual(403, handler.lambda_handler(event, None)["statusCode"])
            db.Table.assert_not_called()
            self.assertFalse(s3.mock_calls)

    def test_role_claim_serializations(self):
        for roles in (["FileTransfer.Upload"], '["FileTransfer.Upload"]', "[FileTransfer.Upload]", "FileTransfer.Upload"):
            event = self._event("POST /transfer-tickets", {})
            event["requestContext"]["authorizer"]["jwt"]["claims"]["roles"] = roles
            self.assertEqual({"products-poc"}, handler._allowed_client_ids(event))

    def test_missing_or_invalid_mapping_fails_closed(self):
        for mapping in ('{}', 'null', 'invalid', '{"consumer-test": ["*"]}', '{"consumer-test": "products-poc"}'):
            with patch.dict(os.environ, {"ENTRA_APPLICATION_CLIENTS": mapping}):
                self.assertEqual(403, handler.lambda_handler(self._event("POST /transfer-tickets", {}), None)["statusCode"])

    @patch.object(handler, "DYNAMODB")
    @patch.object(handler, "S3_CLIENT")
    @patch.object(handler.uuid, "uuid4", side_effect=["ticket-1", "object-1"])
    def test_small_file_uses_single_upload(self, _uuid4, patched_s3, patched_dynamo):
        patched_s3.generate_presigned_url = self.fake_s3.generate_presigned_url
        patched_dynamo.Table = self.fake_dynamo.Table

        response = handler.lambda_handler(
            self._event(
                "POST /transfer-tickets",
                {
                    "clientId": "products-poc",
                    "fileName": "example.csv",
                    "contentType": "text/csv",
                    "sizeBytes": 1024,
                },
            ),
            None,
        )

        body = json.loads(response["body"])
        self.assertEqual(200, response["statusCode"])
        self.assertEqual("https://example.test/upload", body["upload"]["url"])
        self.assertEqual("1024", body["upload"]["headers"]["x-amz-meta-declared-size-bytes"])
        params = self.fake_s3.presign_calls[0]["Params"]
        self.assertEqual(handler.UPLOAD_BUCKET_NAME, params["Bucket"])
        self.assertEqual(handler.UPLOAD_BUCKET_KMS_KEY_ARN, params["SSEKMSKeyId"])
        self.assertEqual("aws:kms", params["ServerSideEncryption"])
        self.assertTrue(params["Key"].startswith("products-poc/uploads/"))

    @patch.object(handler, "DYNAMODB")
    @patch.object(handler, "S3_CLIENT")
    @patch.object(handler.uuid, "uuid4", side_effect=["ticket-2", "object-2"])
    def test_large_file_uses_multipart_upload(self, _uuid4, patched_s3, patched_dynamo):
        patched_s3.generate_presigned_url = self.fake_s3.generate_presigned_url
        patched_s3.create_multipart_upload = self.fake_s3.create_multipart_upload
        patched_dynamo.Table = self.fake_dynamo.Table

        response = handler.lambda_handler(
            self._event(
                "POST /transfer-tickets",
                {
                    "clientId": "products-poc",
                    "fileName": "example.csv",
                    "contentType": "text/csv",
                    "sizeBytes": handler.SINGLE_PUT_LIMIT_BYTES + 1,
                },
            ),
            None,
        )

        body = json.loads(response["body"])
        self.assertEqual(200, response["statusCode"])
        self.assertEqual("upload-123", body["multipart"]["uploadId"])
        self.assertTrue(body["multipart"]["initialParts"])
        self.assertIn("ticket-2", self.multipart_sessions.items)
        params = self.fake_s3.create_calls[0]
        self.assertEqual(handler.UPLOAD_BUCKET_NAME, params["Bucket"])
        self.assertEqual(handler.UPLOAD_BUCKET_KMS_KEY_ARN, params["SSEKMSKeyId"])
        self.assertTrue(params["Key"].startswith("products-poc/uploads/"))
        self.assertEqual(handler.SINGLE_PUT_LIMIT_BYTES + 1, self.multipart_sessions.items["ticket-2"]["declared_size_bytes"])

    @patch.object(handler, "DYNAMODB")
    @patch.object(handler, "S3_CLIENT")
    def test_part_presign_returns_requested_parts(self, patched_s3, patched_dynamo):
        patched_s3.generate_presigned_url = self.fake_s3.generate_presigned_url
        patched_dynamo.Table = self.fake_dynamo.Table
        self.multipart_sessions.items["ticket-3"] = {
            "transfer_ticket": "ticket-3",
            "status": "initiated",
            "client_id": "products-poc",
            "bucket": "bucket-name",
            "object_key": "products-poc/uploads/object.csv",
            "upload_id": "upload-456",
            "expires_in_seconds": 900,
            "part_size_bytes": 67108864,
            "total_parts": 3,
        }

        response = handler.lambda_handler(
            self._event(
                "POST /transfer-tickets/{transferTicket}/parts",
                {"partNumbers": [1, 3]},
                {"transferTicket": "ticket-3"},
            ),
            None,
        )

        body = json.loads(response["body"])
        self.assertEqual(200, response["statusCode"])
        self.assertEqual([1, 3], [part["partNumber"] for part in body["parts"]])

    def _active_session(self):
        self.multipart_sessions.items["ticket"] = {
            "transfer_ticket": "ticket", "status": "initiated",
            "client_id": "products-poc", "bucket": handler.UPLOAD_BUCKET_NAME,
            "object_key": "products-poc/uploads/file.csv", "upload_id": "upload-1",
            "expires_in_seconds": 900, "total_parts": 2,
        }

    def test_complete_uses_session_bucket_and_sorted_parts(self):
        self._active_session()
        with patch.object(handler, "DYNAMODB", self.fake_dynamo), patch.object(handler, "S3_CLIENT", self.fake_s3):
            response = handler.lambda_handler(self._event(
                "POST /transfer-tickets/{transferTicket}/complete",
                {"parts": [{"partNumber": 2, "eTag": '"two"'}, {"partNumber": 1, "eTag": '"one"'}]},
                {"transferTicket": "ticket"}), None)
        self.assertEqual(200, response["statusCode"])
        call = self.fake_s3.complete_calls[0]
        self.assertEqual(handler.UPLOAD_BUCKET_NAME, call["Bucket"])
        self.assertEqual("products-poc/uploads/file.csv", call["Key"])
        self.assertEqual("upload-1", call["UploadId"])
        self.assertEqual([1, 2], [p["PartNumber"] for p in call["MultipartUpload"]["Parts"]])
        self.assertEqual("completed", json.loads(response["body"])["status"])

    def test_abort_uses_session_bucket_and_upload_id(self):
        self._active_session()
        with patch.object(handler, "DYNAMODB", self.fake_dynamo), patch.object(handler, "S3_CLIENT", self.fake_s3):
            response = handler.lambda_handler(self._event(
                "DELETE /transfer-tickets/{transferTicket}", None, {"transferTicket": "ticket"}), None)
        self.assertEqual(200, response["statusCode"])
        self.assertEqual([{"Bucket": handler.UPLOAD_BUCKET_NAME,
                          "Key": "products-poc/uploads/file.csv", "UploadId": "upload-1"}], self.fake_s3.abort_calls)

    def test_other_client_cannot_manage_multipart_session(self):
        self._active_session()
        for route, body in [
            ("POST /transfer-tickets/{transferTicket}/parts", {"partNumbers": [1]}),
            ("POST /transfer-tickets/{transferTicket}/complete", {"parts": [{"partNumber": 1, "eTag": "one"}]}),
            ("DELETE /transfer-tickets/{transferTicket}", None),
        ]:
            with self.subTest(route=route):
                event = self._event(route, body, {"transferTicket": "ticket"})
                event["requestContext"]["authorizer"]["jwt"]["claims"]["azp"] = "other-consumer"
                with patch.object(handler, "DYNAMODB", self.fake_dynamo), patch.object(handler, "S3_CLIENT", self.fake_s3):
                    response = handler.lambda_handler(event, None)
                self.assertEqual(403, response["statusCode"])
        self.assertEqual([], self.fake_s3.presign_calls)
        self.assertEqual([], self.fake_s3.complete_calls)
        self.assertEqual([], self.fake_s3.abort_calls)


if __name__ == "__main__":
    unittest.main()
