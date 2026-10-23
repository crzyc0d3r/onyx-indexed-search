"""Index synthetic documents locally or use the Onyx ingestion/search APIs."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parent
PROJECT = "onyx-indexed-search"
MARKER = "onyxindexedsearchfixture"
MAX_RESPONSE = 2 * 1024 * 1024


class SearchError(Exception):
    """An input, transport, or API-contract failure safe to show in a terminal."""


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def load_documents(path: Path = ROOT / "data" / "sample_documents.json") -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise SearchError("The document file must contain a nonempty JSON array.")
    seen = set()
    for doc in data:
        if not isinstance(doc, dict):
            raise SearchError("Each document must be an object.")
        for key in ("id", "semantic_identifier"):
            if not isinstance(doc.get(key), str) or not doc[key].strip():
                raise SearchError(f"Each document needs a nonempty {key}.")
        if not re.fullmatch(r"onyx-indexed-search-demo-[a-z0-9-]+", doc["id"]):
            raise SearchError("Document IDs must use the reserved demo prefix.")
        if doc["id"] in seen:
            raise SearchError("Document IDs must be unique.")
        seen.add(doc["id"])
        if not isinstance(doc.get("metadata"), dict) or doc["metadata"].get("project") != PROJECT:
            raise SearchError("Document metadata must identify this project.")
        sections = doc.get("sections")
        if not isinstance(sections, list) or not sections:
            raise SearchError("Each document needs text sections.")
        for section in sections:
            if not isinstance(section, dict) or section.get("type") != "text":
                raise SearchError("Only inline text sections are supported.")
            if not isinstance(section.get("text"), str) or not section["text"].strip():
                raise SearchError("Text sections must not be empty.")
            link = section.get("link")
            if not isinstance(link, str) or not link.startswith("https://example.com/"):
                raise SearchError("Demo links must use https://example.com/.")
        if MARKER not in " ".join(s["text"] for s in sections):
            raise SearchError("Each document needs the fixture marker for readiness checks.")
    return data


@dataclass(frozen=True)
class Hit:
    document_id: str
    title: str
    link: str | None
    blurb: str
    score: float | None


class LocalIndex:
    """Small BM25 inverted index; a teaching implementation, not the Onyx engine."""

    def __init__(self, documents: list[dict]):
        self.documents = {doc["id"]: doc for doc in documents}
        self.postings: dict[str, dict[str, int]] = defaultdict(dict)
        self.lengths = {}
        for doc in documents:
            text = doc["semantic_identifier"] + " " + " ".join(s["text"] for s in doc["sections"])
            counts = Counter(tokens(text))
            self.lengths[doc["id"]] = sum(counts.values())
            for term, count in counts.items():
                self.postings[term][doc["id"]] = count
        self.average_length = sum(self.lengths.values()) / max(1, len(self.lengths))

    def search(self, query: str, limit: int = 3) -> list[Hit]:
        scores: dict[str, float] = defaultdict(float)
        n = len(self.documents)
        for term in set(tokens(query)):
            posting = self.postings.get(term, {})
            idf = math.log(1 + (n - len(posting) + 0.5) / (len(posting) + 0.5))
            for doc_id, frequency in posting.items():
                length = self.lengths[doc_id]
                denominator = frequency + 1.2 * (0.25 + 0.75 * length / self.average_length)
                scores[doc_id] += idf * frequency * 2.2 / denominator
        ordered = sorted(scores, key=lambda doc_id: (-scores[doc_id], doc_id))[:limit]
        return [Hit(doc_id, self.documents[doc_id]["semantic_identifier"],
                    self.documents[doc_id]["sections"][0]["link"],
                    self.documents[doc_id]["sections"][0]["text"].replace(" " + MARKER, ""),
                    round(scores[doc_id], 6)) for doc_id in ordered]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward Authorization, even to another path on the same host.
        return None


def validate_base_url(base_url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(base_url)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise SearchError("Invalid API base URL.") from None
    if (parsed.scheme not in {"http", "https"} or not host or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or any(c.isspace() for c in base_url)):
        raise SearchError("Use an HTTP(S) API URL without credentials, query, or fragment.")
    try:
        loopback = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    if parsed.scheme == "http" and not loopback:
        raise SearchError("Plain HTTP is allowed only for loopback development servers.")
    if port is not None and port == 0:
        raise SearchError("API port must be positive.")
    return base_url.rstrip("/")


class OnyxClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30):
        self.base_url = validate_base_url(base_url)
        if not api_key or any(c.isspace() for c in api_key):
            raise SearchError("Set ONYX_API_KEY to an API key without whitespace.")
        if not math.isfinite(timeout) or timeout <= 0:
            raise SearchError("The request timeout must be positive and finite.")
        self.api_key = api_key
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, path: str, payload: dict, timeout: float | None = None) -> dict:
        if path not in {"/onyx-api/ingestion", "/admin/search"}:
            raise SearchError("Unsupported API endpoint.")
        request = urllib.request.Request(
            self.base_url + path, json.dumps(payload).encode("utf-8"),
            {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json",
             "Accept": "application/json"}, method="POST")
        try:
            with self.opener.open(request, timeout=timeout or self.timeout) as response:
                body = response.read(MAX_RESPONSE + 1)
                if len(body) > MAX_RESPONSE:
                    raise SearchError("The API response exceeded the 2 MiB limit.")
                result = json.loads(body)
        except urllib.error.HTTPError as error:
            hints = {401: "Check the API key.", 403: "The key lacks the required admin permissions.",
                     404: "Check the /api proxy prefix and upstream version.",
                     501: "Use Standard Onyx; Lite mode has no document index."}
            hint = "Redirect refused." if 300 <= error.code < 400 else hints.get(error.code, "Check server logs.")
            error.close()
            raise SearchError(f"Onyx HTTP {error.code}. {hint}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise SearchError("Onyx request failed or timed out; check server connectivity.") from None
        except (ValueError, UnicodeError):
            raise SearchError("Onyx returned invalid JSON.") from None
        if not isinstance(result, dict):
            raise SearchError("Onyx returned an unexpected response object.")
        return result

    def ingest(self, documents: list[dict], cc_pair_id: int | None = None) -> None:
        if cc_pair_id is not None and (type(cc_pair_id) is not int or cc_pair_id < 1):
            raise SearchError("A supplied connector-credential pair ID must be a positive integer.")
        for doc in documents:
            payload: dict[str, Any] = {"document": doc}
            if cc_pair_id is not None:
                payload["cc_pair_id"] = cc_pair_id
            result = self.request("/onyx-api/ingestion", payload)
            if result.get("document_id") != doc["id"] or type(result.get("already_existed")) is not bool:
                raise SearchError("Ingestion response does not match the submitted document.")

    def search(self, query: str, allowed_ids: set[str], timeout: float | None = None) -> list[Hit]:
        if not query.strip():
            raise SearchError("The search query must not be empty.")
        result = self.request("/admin/search", {
            "query": query, "filters": {"tags": [{"tag_key": "project", "tag_value": PROJECT}]}}, timeout)
        documents = result.get("documents")
        if not isinstance(documents, list):
            raise SearchError("Search response has no documents array.")
        hits = []
        seen = set()
        for doc in documents:
            if not isinstance(doc, dict) or not isinstance(doc.get("document_id"), str):
                raise SearchError("Malformed search document.")
            if doc["document_id"] not in allowed_ids or doc["document_id"] in seen:
                continue
            if type(doc.get("hidden")) is not bool:
                raise SearchError("Search response is missing the hidden flag.")
            if doc["hidden"]:
                continue
            if not all(isinstance(doc.get(k), str) for k in ("semantic_identifier", "blurb")):
                raise SearchError("Search response is missing title or blurb.")
            link, score = doc.get("link"), doc.get("score")
            if link is not None and not isinstance(link, str):
                raise SearchError("Search result link must be a string or null.")
            if score is not None and (type(score) not in {int, float} or not math.isfinite(score)):
                raise SearchError("Search result score must be finite or null.")
            seen.add(doc["document_id"])
            hits.append(Hit(doc["document_id"], doc["semantic_identifier"], link, doc["blurb"], score))
        return hits

    def wait_until_searchable(self, expected_ids: set[str], seconds: float, interval: float = 1) -> None:
        if (not expected_ids or not math.isfinite(seconds) or seconds <= 0
                or not math.isfinite(interval) or interval <= 0):
            raise SearchError("Readiness requires document IDs and a positive finite time budget.")
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SearchError("Timed out waiting for every demo document to become searchable.")
            hits = self.search(MARKER, expected_ids, timeout=min(self.timeout, remaining))
            if {hit.document_id for hit in hits} == expected_ids:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SearchError("Timed out waiting for every demo document to become searchable.")
            time.sleep(min(interval, remaining))


def render(query: str, hits: list[Hit], mode: str) -> str:
    lines = [f"Mode: {mode}", f"Question: {query}", "", "Retrieved evidence (extractive, no LLM):"]
    if not hits:
        return "\n".join(lines + ["No matching indexed evidence found."])
    for i, hit in enumerate(hits[:3], 1):
        lines.append(f"[{i}] {hit.blurb}")
    lines.append("\nSources:")
    for i, hit in enumerate(hits[:3], 1):
        lines.append(f"[{i}] {hit.title} | {hit.link or hit.document_id}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["offline", "live"], nargs="?", default="offline")
    parser.add_argument("--query", default="telemetry retention expiry")
    parser.add_argument("--base-url", default="http://localhost:3000/api")
    parser.add_argument("--ingest", action="store_true", help="Upsert the three synthetic demo documents.")
    parser.add_argument("--cc-pair-id", type=int, help="Existing connector-credential pair, otherwise upstream default.")
    parser.add_argument("--wait-seconds", type=float, default=60)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args(argv)
    try:
        if not args.query.strip():
            raise SearchError("The search query must not be empty.")
        documents = load_documents()
        if args.mode == "offline":
            if args.ingest or args.cc_pair_id is not None:
                raise SearchError("--ingest and --cc-pair-id apply only to live mode.")
            hits = LocalIndex(documents).search(args.query)
            mode = "offline BM25 teaching index"
        else:
            if args.cc_pair_id is not None and not args.ingest:
                raise SearchError("--cc-pair-id requires --ingest.")
            if args.ingest and (not math.isfinite(args.wait_seconds) or args.wait_seconds <= 0):
                raise SearchError("--wait-seconds must be positive and finite.")
            client = OnyxClient(args.base_url, os.environ.get("ONYX_API_KEY", ""), args.timeout)
            allowed_ids = {doc["id"] for doc in documents}
            if args.ingest:
                client.ingest(documents, args.cc_pair_id)
                client.wait_until_searchable(allowed_ids, args.wait_seconds)
            hits = client.search(args.query, allowed_ids)
            mode = "Onyx indexed admin search"
        print(render(args.query, hits, mode))
        return 0
    except (SearchError, ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
