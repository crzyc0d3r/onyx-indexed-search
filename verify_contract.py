"""Check consumed API schemas and routes against a local, pinned upstream checkout."""

import argparse
import ast
import json
from pathlib import Path
import subprocess

from indexed_search import ROOT, load_documents


PIN = "a4ed03e6717c85b3d9d4e22196dbc4166e7b965e"


def classes(path: Path) -> dict[str, ast.ClassDef]:
    return {node.name: node for node in ast.parse(path.read_text(encoding="utf-8")).body
            if isinstance(node, ast.ClassDef)}


def fields(model: ast.ClassDef) -> dict[str, tuple[str, bool]]:
    return {node.target.id: (ast.unparse(node.annotation), node.value is None)
            for node in model.body if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)}


def require_fields(model: ast.ClassDef, expected: dict[str, tuple[str, bool]]) -> int:
    actual = fields(model)
    for name, contract in expected.items():
        if actual.get(name) != contract:
            raise ValueError(f"{model.name}.{name}: expected {contract}, found {actual.get(name)}")
    return len(expected)


def verify_route(path: Path, router: str, prefix: str, function: str, route: str) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    prefix_found = False
    route_found = False
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == router for t in node.targets):
            if isinstance(node.value, ast.Call):
                prefix_found = any(k.arg == "prefix" and isinstance(k.value, ast.Constant)
                                   and k.value.value == prefix for k in node.value.keywords)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            for decorator in node.decorator_list:
                if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                        and isinstance(decorator.func.value, ast.Name)
                        and decorator.func.value.id == router and decorator.func.attr == "post"
                        and decorator.args and isinstance(decorator.args[0], ast.Constant)
                        and decorator.args[0].value == route):
                    route_found = True
    if not (prefix_found and route_found):
        raise ValueError(f"Missing expected POST {prefix}{route}")


def verify(checkout: Path) -> dict:
    revision = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    if revision != PIN:
        raise ValueError(f"Expected upstream commit {PIN}, found {revision}")
    base = checkout / "backend" / "onyx"
    connector = classes(base / "connectors" / "models.py")
    ingestion = classes(base / "server" / "onyx_api" / "models.py")
    query = classes(base / "server" / "query_and_chat" / "models.py")
    search = classes(base / "context" / "search" / "models.py")
    checked = 0
    expected_models = [
        (connector["TextSection"], {"type": ("Literal[SectionType.TEXT]", False), "text": ("str", True)}),
        (connector["Section"], {"link": ("str | None", False)}),
        (connector["DocumentBase"], {
            "id": ("str | None", False), "semantic_identifier": ("str", True),
            "sections": ("Sequence[TextSection | ImageSection | TabularSection]", True),
            "metadata": ("dict[str, str | list[str]]", True), "source": ("DocumentSource | None", False)}),
        (ingestion["IngestionDocument"], {"document": ("DocumentBase", True), "cc_pair_id": ("int | None", False)}),
        (ingestion["IngestionResult"], {"document_id": ("str", True), "already_existed": ("bool", True)}),
        (query["AdminSearchRequest"], {"query": ("str", True), "filters": ("BaseFilters", True)}),
        (query["AdminSearchResponse"], {"documents": ("list[SearchDoc]", True)}),
        (search["BaseFilters"], {"tags": ("list[Tag] | None", False)}),
        (search["Tag"], {"tag_key": ("str", True), "tag_value": ("str", True)}),
        (search["SearchDoc"], {
            "document_id": ("str", True), "semantic_identifier": ("str", True),
            "link": ("str | None", False), "blurb": ("str", True),
            "hidden": ("bool", True), "score": ("float | None", False)}),
    ]
    for model, expected in expected_models:
        checked += require_fields(model, expected)
    verify_route(base / "server" / "onyx_api" / "ingestion.py", "router", "/onyx-api", "upsert_ingestion_doc", "/ingestion")
    verify_route(base / "server" / "query_and_chat" / "query_backend.py", "admin_router", "/admin", "admin_search", "/search")
    documents = load_documents()
    for document in documents:
        required = {k for k, (_, required) in fields(connector["DocumentBase"]).items() if required}
        missing = required - document.keys()
        unknown = document.keys() - fields(connector["DocumentBase"]).keys()
        if missing or unknown:
            raise ValueError(f"Document payload differs from actual upstream fields: missing={missing}, unknown={unknown}")
        for section in document["sections"]:
            required = {k for k, (_, required) in fields(connector["TextSection"]).items() if required}
            if required - section.keys():
                raise ValueError("Text section is missing required upstream fields.")
    return {"upstream_commit": revision, "schema_fields_checked": checked,
            "post_routes_checked": 2, "sample_documents_checked": len(documents),
            "method": "AST schema and required-field verification; no upstream service started"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.checkout), indent=2))
