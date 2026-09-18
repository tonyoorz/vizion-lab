#!/usr/bin/env python3
"""
Import defects and test cases from SQLite into PostgreSQL + pgvector.

- Uses Beacon API (qwen3-embedding-8b, 4096-dim) for embedding generation
- High concurrency: 16 workers × 32 texts/batch
- Defects: excludes CoC project, all others from qgate_raw.db
- Test cases: all from qgate_raw.db
- Original SQLite databases are NOT modified

Usage:
    python scripts/pgvector_import.py [--defects] [--testcases] [--batch-size 32] [--workers 16]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Sequence

import httpx
import psycopg
from psycopg.rows import dict_row

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
QGATE_DB = PROJECT_ROOT / "database" / "source" / "qgate_raw.db"

BEACON_URL = os.getenv(
    "DUPLICATE_REMOTE_EMBEDDING_URL",
    "https://beaconapi.bmwbrill.cn/project-poc/v1/embeddings",
)
BEACON_MODEL = os.getenv("DUPLICATE_REMOTE_EMBEDDING_MODEL", "qwen3-embedding-8b")
BEACON_AUTH = os.getenv(
    "DUPLICATE_REMOTE_EMBEDDING_AUTHORIZATION",
    "Bearer 443EE3B2DCFF41878C13E10176076C78",
)
BEACON_TIMEOUT = float(os.getenv("DUPLICATE_REMOTE_EMBEDDING_TIMEOUT_SECONDS", "120"))

PG_CONN_STR = os.getenv(
    "PG_CONNECTION_STRING",
    "postgresql://postgres:admin@localhost:5432/vizion_vectors",
)

DEFAULT_BATCH_SIZE = 32
DEFAULT_WORKERS = 16
DEFAULT_MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# SQLite data loading
# ---------------------------------------------------------------------------

def load_defects(exclude_coc: bool = True) -> list[dict[str, Any]]:
    """Load defects from qgate_raw.db, optionally excluding CoC project."""
    conn = sqlite3.connect(str(QGATE_DB))
    conn.row_factory = sqlite3.Row

    query = """
        SELECT defect_id, name, description, project, tproject,
               problem_severity, team
        FROM octane_defects
    """
    if exclude_coc:
        query += " WHERE UPPER(COALESCE(project, '')) != 'COC'"

    rows = [dict(r) for r in conn.execute(query)]
    conn.close()

    # Build embedding text from relevant fields
    for row in rows:
        parts = [row.get("name") or "", row.get("description") or ""]
        for field in ("project", "team", "problem_severity"):
            val = row.get(field)
            if val:
                parts.append(str(val))
        # Strip HTML from description
        text = " ".join(p for p in parts if p)
        # Simple HTML tag removal
        import re
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        row["embedding_text"] = text
        row["text_hash"] = hashlib.md5(text.encode("utf-8")).hexdigest()

    return rows


def load_testcases() -> list[dict[str, Any]]:
    """Load test cases from qgate_raw.db."""
    conn = sqlite3.connect(str(QGATE_DB))
    conn.row_factory = sqlite3.Row

    rows = [dict(r) for r in conn.execute("""
        SELECT test_id, test_name, test_subtype, scope_team, scope_release
        FROM octane_testcases
    """)]
    conn.close()

    for row in rows:
        parts = [
            row.get("test_name") or "",
            row.get("test_subtype") or "",
            row.get("scope_team") or "",
            row.get("scope_release") or "",
        ]
        text = " ".join(p for p in parts if p).strip()
        row["embedding_text"] = text
        row["text_hash"] = hashlib.md5(text.encode("utf-8")).hexdigest()

    return rows


# ---------------------------------------------------------------------------
# Beacon API embedding (concurrent batch encoding)
# ---------------------------------------------------------------------------

def encode_batch_remote(texts: list[str]) -> list[list[float]]:
    """Encode a batch of texts via Beacon API. Returns list of embedding vectors."""
    headers = {
        "accept": "application/json",
        "Authorization": BEACON_AUTH,
        "Content-Type": "application/json",
    }

    for attempt in range(DEFAULT_MAX_RETRIES):
        try:
            with httpx.Client(timeout=BEACON_TIMEOUT, verify=False) as client:
                response = client.post(
                    BEACON_URL,
                    headers=headers,
                    json={"model": BEACON_MODEL, "input": texts},
                )
            response.raise_for_status()
            payload = response.json()

            # Extract embeddings from response
            data = payload.get("data", [])
            if isinstance(data, list):
                vectors = [item["embedding"] for item in data if isinstance(item, dict)]
            elif isinstance(data, dict) and "embedding" in data:
                vectors = [data["embedding"]]
            elif "embedding" in payload:
                vectors = [payload["embedding"]]
            else:
                raise RuntimeError(f"no embeddings in response: {str(payload)[:200]}")

            if len(vectors) != len(texts):
                raise RuntimeError(f"got {len(vectors)} embeddings for {len(texts)} texts")

            return vectors

        except Exception as exc:
            if attempt < DEFAULT_MAX_RETRIES - 1:
                wait = 2 ** attempt
                print(f"  [retry {attempt+1}/{DEFAULT_MAX_RETRIES}] {exc}, waiting {wait}s...")
                time.sleep(wait)
            else:
                raise RuntimeError(f"batch failed after {DEFAULT_MAX_RETRIES} retries: {exc}") from exc

    return []


def encode_all_concurrent(
    texts: list[str],
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_workers: int = DEFAULT_WORKERS,
    progress_label: str = "encoding",
) -> list[list[float]]:
    """Encode all texts with concurrent batch processing."""
    total = len(texts)
    batches = [texts[i:i+batch_size] for i in range(0, total, batch_size)]
    n_batches = len(batches)
    print(f"  {progress_label}: {total} texts, {n_batches} batches, {max_workers} workers")

    # Results array pre-allocated by batch position
    results: list[list[float] | None] = [None] * n_batches

    with ThreadPoolExecutor(max_workers=min(max_workers, n_batches)) as pool:
        future_to_idx = {
            pool.submit(encode_batch_remote, batch): idx
            for idx, batch in enumerate(batches)
        }

        completed = 0
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                print(f"  [ERROR] batch {idx} failed: {exc}")
                results[idx] = []
            completed += 1
            if completed % 20 == 0 or completed == n_batches:
                print(f"  [{progress_label}] {completed}/{n_batches} batches done ({completed*batch_size}/{total} texts)")

    # Flatten results
    all_embeddings: list[list[float]] = []
    for emb_list in results:
        if emb_list:
            all_embeddings.extend(emb_list)
        else:
            # Fill with zeros for failed batches
            all_embeddings.extend([[0.0] * 4096] * batch_size)

    # Trim to exact count (last batch may be partial)
    return all_embeddings[:total]


# ---------------------------------------------------------------------------
# PostgreSQL writing
# ---------------------------------------------------------------------------

def write_defects_to_pg(rows: list[dict[str, Any]], embeddings: list[list[float]]) -> int:
    """Write defects + embeddings to PostgreSQL."""
    conn = psycopg.connect(PG_CONN_STR, row_factory=dict_row)
    cursor = conn.cursor()

    written = 0
    for row, emb in zip(rows, embeddings):
        emb_str = "[" + ",".join(f"{v:.8f}" for v in emb) + "]"
        cursor.execute("""
            INSERT INTO defect_vectors
                (defect_id, name, project, tproject, problem_severity, team,
                 embedding_text, text_hash, embedding, model_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (defect_id) DO UPDATE SET
                name = EXCLUDED.name,
                project = EXCLUDED.project,
                embedding_text = EXCLUDED.embedding_text,
                text_hash = EXCLUDED.text_hash,
                embedding = EXCLUDED.embedding,
                model_name = EXCLUDED.model_name,
                created_at = NOW()
        """, (
            row["defect_id"], row.get("name"), row.get("project"),
            row.get("tproject"), row.get("problem_severity"), row.get("team"),
            row["embedding_text"], row["text_hash"], emb_str, BEACON_MODEL,
        ))
        written += 1
        if written % 5000 == 0:
            conn.commit()
            print(f"  [pg-write] {written}/{len(rows)} defects written")

    conn.commit()
    conn.close()
    return written


def write_testcases_to_pg(rows: list[dict[str, Any]], embeddings: list[list[float]]) -> int:
    """Write test cases + embeddings to PostgreSQL."""
    conn = psycopg.connect(PG_CONN_STR, row_factory=dict_row)
    cursor = conn.cursor()

    written = 0
    for row, emb in zip(rows, embeddings):
        emb_str = "[" + ",".join(f"{v:.8f}" for v in emb) + "]"
        cursor.execute("""
            INSERT INTO testcase_vectors
                (test_id, test_name, test_subtype, scope_team, scope_release,
                 embedding_text, text_hash, embedding, model_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (test_id) DO UPDATE SET
                test_name = EXCLUDED.test_name,
                embedding_text = EXCLUDED.embedding_text,
                text_hash = EXCLUDED.text_hash,
                embedding = EXCLUDED.embedding,
                model_name = EXCLUDED.model_name,
                created_at = NOW()
        """, (
            row["test_id"], row.get("test_name"), row.get("test_subtype"),
            row.get("scope_team"), row.get("scope_release"),
            row["embedding_text"], row["text_hash"], emb_str, BEACON_MODEL,
        ))
        written += 1
        if written % 5000 == 0:
            conn.commit()
            print(f"  [pg-write] {written}/{len(rows)} testcases written")

    conn.commit()
    conn.close()
    return written


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Import defects + testcases to pgvector")
    parser.add_argument("--defects", action="store_true", help="Import defects")
    parser.add_argument("--testcases", action="store_true", help="Import test cases")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--limit", type=int, default=0, help="Limit rows (0 = all)")
    args = parser.parse_args()

    if not args.defects and not args.testcases:
        args.defects = True
        args.testcases = True

    print(f"=== pgvector import ===")
    print(f"  Source: {QGATE_DB}")
    print(f"  Target: {PG_CONN_STR}")
    print(f"  Model: {BEACON_MODEL} (4096-dim)")
    print(f"  Batch: {args.batch_size}, Workers: {args.workers}")
    print()

    # --- Test cases ---
    if args.testcases:
        print("--- Test Cases ---")
        tc_rows = load_testcases()
        if args.limit:
            tc_rows = tc_rows[:args.limit]
        print(f"  Loaded {len(tc_rows)} test cases")

        tc_texts = [r["embedding_text"] for r in tc_rows]
        t0 = time.time()
        tc_embeddings = encode_all_concurrent(
            tc_texts, batch_size=args.batch_size,
            max_workers=args.workers, progress_label="testcases"
        )
        t1 = time.time()
        print(f"  Encoding done in {t1-t0:.1f}s ({len(tc_embeddings)} embeddings)")

        written = write_testcases_to_pg(tc_rows, tc_embeddings)
        print(f"  Written {written} test cases to PostgreSQL\n")

    # --- Defects ---
    if args.defects:
        print("--- Defects (excluding CoC) ---")
        def_rows = load_defects(exclude_coc=True)
        if args.limit:
            def_rows = def_rows[:args.limit]
        print(f"  Loaded {len(def_rows)} defects")

        def_texts = [r["embedding_text"] for r in def_rows]
        t0 = time.time()
        def_embeddings = encode_all_concurrent(
            def_texts, batch_size=args.batch_size,
            max_workers=args.workers, progress_label="defects"
        )
        t1 = time.time()
        print(f"  Encoding done in {t1-t0:.1f}s ({len(def_embeddings)} embeddings)")

        written = write_defects_to_pg(def_rows, def_embeddings)
        print(f"  Written {written} defects to PostgreSQL\n")

    # --- Summary ---
    print("=== Done ===")
    conn = psycopg.connect(PG_CONN_STR, row_factory=dict_row)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) AS cnt FROM defect_vectors")
    d_count = cursor.fetchone()["cnt"]
    cursor.execute("SELECT COUNT(*) AS cnt FROM testcase_vectors")
    tc_count = cursor.fetchone()["cnt"]
    conn.close()
    print(f"  defect_vectors: {d_count} rows")
    print(f"  testcase_vectors: {tc_count} rows")


if __name__ == "__main__":
    main()
