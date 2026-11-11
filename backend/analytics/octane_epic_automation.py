from __future__ import annotations

import argparse
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def _numeric_id(value: str) -> str:
    normalized = str(value).strip()
    if not re.fullmatch(r"[0-9]+", normalized):
        raise ValueError("An Octane ID must be numeric")
    return normalized


def _reference_ids(value: Any) -> list[str]:
    if isinstance(value, dict):
        value = value.get("data", [value])
    if not isinstance(value, list):
        return []
    return sorted({str(item["id"]) for item in value
                   if isinstance(item, dict) and item.get("id")}, key=int)


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_text(value), encoding="utf-8")


def _field_schema(client: Any, entity_name: str) -> list[dict[str, Any]]:
    response = client._session.get(
        f"{client._api_base}/metadata/fields",
        params={"query": f'"(entity_name=\'{entity_name}\')"'},
        timeout=60, verify=False,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("data", [])
    if len(rows) != int(payload.get("total_count", len(rows))):
        raise ValueError(f"Incomplete field metadata for {entity_name}")
    return rows


def collect_epic_evidence(
    client: Any,
    *,
    epic_id: str,
    batch_size: int = 40,
    workers: int = 4,
    cache_directory: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    epic_id = _numeric_id(epic_id)
    if batch_size < 1 or workers < 1:
        raise ValueError("batch_size and workers must be positive")
    started_at = datetime.now(timezone.utc).isoformat()
    epic = client.fetch_work_item(epic_id)
    if str(epic.get("id")) != epic_id or epic.get("subtype") != "epic":
        raise ValueError("The requested work item is not the expected Epic")
    metadata_schema = {}
    selectable_fields = {}
    for entity_name in ("work_item", "test"):
        schema = _field_schema(client, entity_name)
        fields = sorted({row["name"] for row in schema
                         if row.get("selectable") and row.get("name")})
        if not fields:
            raise ValueError(f"No selectable fields discovered for {entity_name}")
        metadata_schema[entity_name] = schema
        selectable_fields[entity_name] = fields
    root_rows = client.fetch_rows(
        "work_items", fields=selectable_fields["work_item"],
        query=f'"(id=\'{epic_id}\')"', limit=200,
    )
    if (len(root_rows) != 1 or str(root_rows[0].get("id")) != epic_id
            or root_rows[0].get("subtype") != "epic"):
        raise ValueError("Full metadata for the expected Epic is unavailable")
    epic = root_rows[0]
    work_items = {epic_id: epic}
    frontier = [epic_id]
    while frontier:
        next_frontier = []
        for offset in range(0, len(frontier), batch_size):
            batch = frontier[offset:offset + batch_size]
            expression = ",".join(f"'{identifier}'" for identifier in batch)
            rows = client.fetch_rows(
                "work_items", fields=selectable_fields["work_item"],
                query=f'"(parent={{id IN {expression}}})"',
                limit=200,
            )
            for row in rows:
                identifier = _numeric_id(row["id"])
                if identifier not in work_items:
                    work_items[identifier] = row
                    next_frontier.append(identifier)
        frontier = next_frontier
        if progress:
            progress(f"Work items including Epic: {len(work_items)}")

    identifiers = sorted(work_items, key=int)
    tests: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(identifiers), batch_size):
        batch = identifiers[offset:offset + batch_size]
        expression = ",".join(f"'{identifier}'" for identifier in batch)
        rows = client.fetch_rows(
            "tests", fields=selectable_fields["test"],
            query=f'"(covered_content={{id IN {expression}}})"',
            limit=200,
        )
        for row in rows:
            identifier = _numeric_id(row["id"])
            tests.setdefault(identifier, {
                "id": identifier,
                "metadata": row,
                "covered_work_item_ids": _reference_ids(
                    row.get("covered_content") or row.get("backlog_coverage")
                ),
                "script": None,
                "script_error": None,
                "script_status": "not_applicable",
            })
        if progress:
            progress(f"Unique tests: {len(tests)}")

    ordered_tests = [tests[identifier] for identifier in sorted(tests, key=int)]
    manual_tests = [test for test in ordered_tests
                    if test["metadata"].get("subtype") == "test_manual"]

    def fetch_script(test: dict[str, Any]) -> dict[str, Any]:
        metadata = test["metadata"]
        fingerprint = hashlib.sha256(_json_text(metadata).encode("utf-8")).hexdigest()
        cache_path = cache_directory / f'{test["id"]}.json' if cache_directory else None
        try:
            if cache_path and metadata.get("last_modified"):
                try:
                    cached = json.loads(cache_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    cached = None
                cached_payload = cached.get("payload") if isinstance(cached, dict) else None
                if (isinstance(cached_payload, dict)
                        and isinstance(cached_payload.get("script"), str)
                        and cached.get("metadata_sha256") == fingerprint):
                    test["script"] = cached_payload
                    test["script_status"] = "cached"
                    return test
            payload = client.fetch_test_steps(test_id=test["id"])
            if not isinstance(payload, dict) or not isinstance(payload.get("script"), str):
                raise ValueError("The manual-script response has no script text")
            test["script"] = payload
            test["script_status"] = "fetched"
            if cache_path:
                _write_json(cache_path, {"metadata_sha256": fingerprint, "payload": payload})
        except Exception as error:
            test["script_error"] = type(error).__name__
            test["script_status"] = "error"
        return test

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for completed, _test in enumerate(executor.map(fetch_script, manual_tests), start=1):
            if progress and (completed % 100 == 0 or completed == len(manual_tests)):
                progress(f"Manual scripts processed: {completed}/{len(manual_tests)}")

    failures = sum(test["script_error"] is not None for test in manual_tests)
    unresolved_coverage = sum(
        not set(test["covered_work_item_ids"]).intersection(work_items)
        for test in ordered_tests
    )
    return {
        "schema_version": 1,
        "epic": epic,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Current tests covering the Epic or any recursively discovered descendant",
        "historical_execution_versions_included": False,
        "metadata_schema": metadata_schema,
        "work_items": [work_items[identifier] for identifier in identifiers],
        "tests": ordered_tests,
        "completeness": {
            "complete": failures == 0 and unresolved_coverage == 0,
            "work_items": len(work_items),
            "tests": len(ordered_tests),
            "manual_tests": len(manual_tests),
            "scripts_fetched": len(manual_tests) - failures,
            "script_failures": failures,
            "unresolved_coverage": unresolved_coverage,
            "empty_scripts": sum(test["script"] is not None
                                 and not test["script"]["script"].strip()
                                 for test in manual_tests),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export read-only Octane Epic test evidence")
    parser.add_argument("--epic-id", default="1685240")
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--cache-directory", type=Path,
                        default=Path("backend/cache/octane_epic_automation"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=40)
    arguments = parser.parse_args()
    from backend.analytics.ingest.client import build_default_octane_client

    client = build_default_octane_client()
    result = collect_epic_evidence(
        client, epic_id=arguments.epic_id, workers=arguments.workers,
        batch_size=arguments.batch_size,
        cache_directory=arguments.cache_directory / _numeric_id(arguments.epic_id),
        progress=lambda message: print(message, flush=True),
    )
    snapshot_path = arguments.output_directory / "evidence_snapshot.json"
    _write_json(snapshot_path, result)
    manifest = {
        "epic_id": arguments.epic_id,
        "api_workspace": "1002/2001",
        "read_only": True,
        "metadata_fields": "All selectable fields discovered in metadata_schema",
        "completeness": result["completeness"],
        "started_at": result["started_at"],
        "finished_at": result["finished_at"],
        "snapshot_sha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
        "historical_execution_versions_included": False,
        "attachment_binaries_included": False,
    }
    _write_json(arguments.output_directory / "manifest.json", manifest)
    print(_json_text(manifest), end="")
    return 0 if result["completeness"]["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())