#!/usr/bin/env python3
"""Deduplicate a JSONL file by ticket_id — keeps the last record per ticket."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python dedup_jsonl.py <file.jsonl>")
        return 1

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        return 1

    records: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            tid = str(r.get("ticket_id", ""))
            if tid:
                records[tid] = r

    with open(path, "w", encoding="utf-8") as f:
        for tid in sorted(records.keys(), key=lambda x: int(x) if x.isdigit() else 0):
            f.write(json.dumps(records[tid], ensure_ascii=False, separators=(",", ":")) + "\n")

    print(f"Deduplicated: {len(records)} unique records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
