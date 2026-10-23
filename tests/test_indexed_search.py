"""Offline correctness and local HTTP integration checks without external services."""

import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
import threading
import time
import unittest
from unittest.mock import patch

import indexed_search as app


def search_doc(doc_id="onyx-indexed-search-demo-telemetry", **overrides):
    doc = {"document_id": doc_id, "chunk_ind": 0, "semantic_identifier": "Synthetic policy",
           "link": "https://example.com/demo/telemetry", "blurb": "Retention is 30 days.",
           "source_type": "ingestion_api", "boost": 0, "hidden": False,
           "metadata": {"project": app.PROJECT}, "score": 1.0, "match_highlights": []}
    return dict(doc, **overrides)


@contextlib.contextmanager
def server(responder):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body, self.headers.get("Authorization")))
            status, headers, content = responder(self.path, body)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(content if isinstance(content, bytes) else json.dumps(content).encode())
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/api", requests
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)


class LocalIndexTests(unittest.TestCase):
    def setUp(self):
        self.documents = app.load_documents()
        self.index = app.LocalIndex(self.documents)

    def test_retention_document_ranks_first_with_citation(self):
        hits = self.index.search("telemetry retention expiry")
        self.assertEqual(hits[0].document_id, "onyx-indexed-search-demo-telemetry")
        rendered = app.render("telemetry retention", hits, "offline")
        self.assertIn("30 days", rendered)
        self.assertIn("[1]", rendered)
        self.assertIn("https://example.com/demo/telemetry", rendered)
        self.assertNotIn(app.MARKER, rendered)

    def test_unknown_terms_do_not_invent_an_answer(self):
        self.assertEqual(self.index.search("unfindablequasar"), [])
        self.assertIn("No matching", app.render("unknown", [], "offline"))

    def test_deterministic_ties_and_limit(self):
        first = self.index.search(app.MARKER, 2)
        self.assertEqual(first, self.index.search(app.MARKER, 2))
        self.assertEqual(len(first), 2)
        self.assertTrue(all(math.isfinite(hit.score) and hit.score > 0 for hit in first))

    def test_cli_offline_succeeds_without_key(self):
        with patch.dict(app.os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(app.main(["offline"]), 0)
        self.assertIn("30 days", out.getvalue())

    def test_live_invalid_budget_fails_before_network_or_upsert(self):
        with patch.object(app.OnyxClient, "ingest") as ingest, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(app.main(["live", "--ingest", "--wait-seconds", "nan"]), 1)
        ingest.assert_not_called()


class ClientTests(unittest.TestCase):
    def test_url_and_key_boundaries(self):
        for url in ("https://key@example.com/api", "https://example.com/api?key=x",
                    "https://example.com/api#x", "ftp://localhost/api", "http://example.com/api",
                    "http://localhost:0/api", "https://example.com:bad/api"):
            with self.subTest(url=url), self.assertRaises(app.SearchError):
                app.OnyxClient(url, "placeholder")
        for key in ("", "secret\nkey", "has space"):
            with self.assertRaises(app.SearchError):
                app.OnyxClient("https://example.com/api", key)
        for timeout in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(app.SearchError):
                app.OnyxClient("https://example.com/api", "placeholder", timeout)
        app.OnyxClient("http://[::1]:3000/api", "placeholder")

    def test_real_http_ingestion_then_tag_filtered_search(self):
        doc = app.load_documents()[0]

        def respond(path, body):
            if path.endswith("/ingestion"):
                return 200, {}, {"document_id": body["document"]["id"], "already_existed": False}
            return 200, {}, {"documents": [search_doc()]}

        with server(respond) as (url, requests):
            client = app.OnyxClient(url, "test-only-placeholder")
            client.ingest([doc], 17)
            hits = client.search("retention", {doc["id"]})
            self.assertEqual(len(hits), 1)
            self.assertEqual(requests[0][0], "/api/onyx-api/ingestion")
            self.assertEqual(requests[0][1], {"document": doc, "cc_pair_id": 17})
            self.assertEqual(requests[1][1]["filters"]["tags"], [{"tag_key": "project", "tag_value": app.PROJECT}])
            self.assertEqual(requests[1][2], "Bearer test-only-placeholder")

    def test_redirect_is_never_followed_or_credential_forwarded(self):
        with server(lambda p, b: (200, {}, {})) as (target, target_requests):
            with server(lambda p, b: (307, {"Location": target + "/admin/search"}, {})) as (origin, requests):
                client = app.OnyxClient(origin, "must-not-leak")
                with self.assertRaisesRegex(app.SearchError, "HTTP 307.*Redirect refused"):
                    client.search("retention", {"demo"})
                self.assertEqual(len(requests), 1)
                self.assertEqual(target_requests, [])

    def test_error_response_cannot_echo_a_key(self):
        with server(lambda p, b: (401, {}, {"error": "must-not-leak"})) as (url, requests):
            with self.assertRaises(app.SearchError) as raised:
                app.OnyxClient(url, "must-not-leak").search("retention", {"demo"})
            self.assertNotIn("must-not-leak", str(raised.exception))
            self.assertIn("401", str(raised.exception))

    def test_environment_proxy_is_not_used_for_api_credentials(self):
        with server(lambda p, b: (200, {}, {})) as (proxy_url, proxy_requests):
            with server(lambda p, b: (200, {}, {"documents": []})) as (url, requests):
                with patch.dict(app.os.environ, {"HTTP_PROXY": proxy_url, "HTTPS_PROXY": proxy_url,
                                               "ALL_PROXY": proxy_url, "NO_PROXY": ""}):
                    self.assertEqual(app.OnyxClient(url, "direct-only").search("query", {"demo"}), [])
                self.assertEqual(len(requests), 1)
                self.assertEqual(proxy_requests, [])

    def test_timeout_is_bounded(self):
        def respond(path, body):
            time.sleep(0.15)
            return 200, {}, {}
        with server(respond) as (url, requests):
            with self.assertRaisesRegex(app.SearchError, "timed out"):
                app.OnyxClient(url, "placeholder", timeout=0.02).search("retention", {"demo"})

    def test_malformed_json_response_rejected(self):
        for body in (b"not-json", [], {"documents": "bad"}, {"documents": [None]}):
            with self.subTest(body=body), server(lambda p, b: (200, {}, body)) as (url, requests):
                with self.assertRaises(app.SearchError):
                    app.OnyxClient(url, "placeholder").search("retention", {"demo"})

    def test_unrelated_hidden_and_duplicate_documents_filtered(self):
        response = {"documents": [search_doc("unrelated"), search_doc(hidden=True),
                                  search_doc(), search_doc()]}
        client = app.OnyxClient("https://example.com/api", "placeholder")
        with patch.object(client, "request", return_value=response):
            self.assertEqual(len(client.search("retention", {search_doc()["document_id"]})), 1)

    def test_bad_score_and_missing_visibility_rejected(self):
        client = app.OnyxClient("https://example.com/api", "placeholder")
        for override in ({"score": float("nan")}, {"hidden": None}):
            with patch.object(client, "request", return_value={"documents": [search_doc(**override)]}):
                with self.assertRaises(app.SearchError):
                    client.search("retention", {search_doc()["document_id"]})

    def test_ingestion_result_must_identify_submitted_document(self):
        client = app.OnyxClient("https://example.com/api", "placeholder")
        with patch.object(client, "request", return_value={"document_id": "wrong", "already_existed": False}):
            with self.assertRaises(app.SearchError):
                client.ingest(app.load_documents()[:1])

    def test_readiness_waits_for_every_expected_document(self):
        client = app.OnyxClient("https://example.com/api", "placeholder")
        first = app.Hit("first", "first", None, "text", 1)
        second = app.Hit("second", "second", None, "text", 1)
        with patch.object(client, "search", side_effect=[[first], [first, second]]) as search, patch.object(app.time, "sleep"):
            client.wait_until_searchable({"first", "second"}, 1)
            self.assertEqual(search.call_count, 2)

    def test_readiness_deadline_prevents_unbounded_polling(self):
        client = app.OnyxClient("https://example.com/api", "placeholder")
        with patch.object(client, "search", return_value=[]), patch.object(app.time, "monotonic", side_effect=[0, 0, 2]):
            with self.assertRaisesRegex(app.SearchError, "Timed out"):
                client.wait_until_searchable({"first"}, 1)


if __name__ == "__main__":
    unittest.main()
