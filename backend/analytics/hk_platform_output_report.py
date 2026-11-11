"""Read-only, region-attributed DTSV China execution and defect analysis."""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qs, urlsplit


HK_TIMEZONE = timezone(timedelta(hours=8), "Asia/Hong_Kong")
CONFIRMED_PLATFORMS = (
    ("WBA32KF080HY60754", "NB5", "CHNKNB"),
    ("WMW12GC04RTA10044", "J01", "137178"),
    ("WBA31HR040HY42964", "G70", "HULUWA"),
    ("WBY22CF0X0CN18562", "NA5", "553620"),
    ("WBA32JM0X0HY52877", "G50", "CHUXIN"),
    ("WBA82HM070HY57257", "U11", "DALIAN"),
)
CONFIRMED_TW_PLATFORMS = (
    ("WBA81HM020HY56858", "U11"),
    ("WBA11KE03W5648172", "U10"),
    ("WBA25GP040HY25272", "G45"),
    ("WMW11GA010HY26704", "U25"),
    ("WBACV6105KLK20040", "U11"),
    ("WBA31KF020HY60753", "NB5"),
    ("WBY21CF08PCN23000", "NA5"),
    ("WBA31HR030HY44656", "NA5"),
    ("SCA51HA070HY58560", "RR45"),
    ("WBA21HY030HY47120", "G70"),
)
CONFIRMED_TW_V_NUMBERS = {
    "WBA81HM020HY56858": "YUNNAN",
    "WBA11KE03W5648172": "374385",
    "WBA25GP040HY25272": "XINZHU",
    "WMW11GA010HY26704": "WANWAN",
    "WBACV6105KLK20040": "546668",
    "WBA31KF020HY60753": "ZHUHAI",
    "WBY21CF08PCN23000": "558077",
    "WBA31HR030HY44656": "BROLLY",
    "SCA51HA070HY58560": "MARIAO",
    "WBA21HY030HY47120": "CNG70R",
}
REGION_LABELS = {"HK": "HK", "TW": "TW", "CN": "Mainland China"}
REGION_TIMEZONES = {"HK": "Asia/Hong_Kong", "TW": "Asia/Taipei", "CN": "Asia/Shanghai"}
HISTORICAL_CANDIDATES = (
    "WBA42HZ0X0HY47121", "WBA12EH000CJ07626",
    "WBA62FX000HY29503", "WBA32HR020DA03337",
)
CONFIRMED_HISTORICAL_V_NUMBERS = {
    "WBA62FX000HY29503": "WEIHAI",
    "WBACR620X09F64155": "V547984",
    "WBA12EH000CJ07626": "V718601",
    "WBY21CF02PCN18309": "553623",
}
CONFIRMED_V_NUMBER_TYPES = {
    "HKTW70": "Bench", "CNG70R": "Bench", "ZHUDI0": "Bench",
    "V547984": "Vehicle", "V718601": "Vehicle",
}
RUN_FIELDS = (
    "mr_id", "test_id", "test_name", "status", "testplatformid", "raw_json",
    "finished", "started", "creation_time", "year", "top_aida", "product_areas",
    "defect_id", "project", "release", "execution_sw_version", "team", "fetched_at",
    "taxonomies", "testing_tool_type", "exec_model_series", "target_ecu_conf",
)
DEFECT_FIELDS = (
    "defect_id", "name", "vin", "creation_time", "year", "top_aida",
    "product_areas", "aida_businesskey", "aida_english", "market", "detected_by",
    "phase", "status_phase", "problem_severity", "severity", "reporting_class",
    "problem_finder_team", "project", "tproject", "assigned_ecu", "solution_cluster",
    "software_version", "fetched_at",
)
VEHICLE_KEYS = ("testplatformid_udf", "testplatformid", "vin_udf", "vin", "vehicle_vin_udf", "vehicle_vin")
EXECUTED_STATUSES = {"Passed", "Failed", "Requires Attention"}
HK_TITLE_PATTERN = re.compile(r"(?:^|[\s\[\]()/_,\-])HK(?=$|[\s\[\]()/_,\-])|\bHong[\s_-]+Kong\b", re.I)
TW_TITLE_PATTERN = re.compile(r"(?:^|[\s\[\]()/_,\-])TW(?=$|[\s\[\]()/_,\-])|\bTaiwan\b", re.I)
CN_TITLE_PATTERN = re.compile(r"(?:^|[\s\[\]()/_,\-])CN(?=$|[\s\[\]()/_,\-])|\b(?:Mainland[\s_-]+China|China)\b", re.I)


def has_hk_title(value):
    return bool(HK_TITLE_PATTERN.search(_text(value)))


def has_tw_title(value):
    return bool(TW_TITLE_PATTERN.search(_text(value)))


def has_cn_title(value):
    return bool(CN_TITLE_PATTERN.search(_text(value)))


def _text(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True)
    return str(value or "").strip()


def _team_matches(value, team_name):
    if team_name is None:
        return True
    if isinstance(value, dict):
        value = value.get("name")
    return re.sub(r"[\s_-]+", "_", _text(value)).casefold() == re.sub(r"[\s_-]+", "_", _text(team_name)).casefold()


def match_vins(value, known_vins):
    text = _text(value).upper()
    suffix_counts = Counter(vin[-7:] for vin in known_vins)
    matched = []
    for vin in known_vins:
        alternatives = [re.escape(vin)]
        if suffix_counts[vin[-7:]] == 1:
            alternatives.append(re.escape(vin[-7:]))
        expression = r"(?<![A-Z0-9])(?:" + "|".join(alternatives) + r")(?![A-Z0-9])"
        if re.search(expression, text):
            matched.append(vin)
    return matched


def _inventory():
    confirmed = [
        {"vin": vin, "short_vin": vin[-7:], "model": model, "platform_name": name,
         "v_number": name, "actual_environment": classify_v_number(name),
         "evidence": "user_confirmed", "market_override": vin.endswith("HY52877")}
        for vin, model, name in CONFIRMED_PLATFORMS
    ]
    return confirmed + [
        {"vin": vin, "short_vin": vin[-7:], "model": "Unknown", "platform_name": "",
         "v_number": CONFIRMED_HISTORICAL_V_NUMBERS.get(vin, ""),
         "actual_environment": classify_v_number(CONFIRMED_HISTORICAL_V_NUMBERS.get(vin, "")),
         "v_number_source": "user_confirmed_2026-11-02" if vin in CONFIRMED_HISTORICAL_V_NUMBERS else "unavailable",
         "evidence": "legacy_mapping_candidate", "market_override": False}
        for vin in HISTORICAL_CANDIDATES
    ]


def _datetime(value):
    try:
        parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(HK_TIMEZONE)


def _event_date(row, names):
    for name in names:
        parsed = _datetime(row.get(name))
        if parsed is not None:
            return parsed, name
    return None, "missing"


def _aida(row):
    raw = _text(row.get("top_aida") or row.get("product_areas") or row.get("aida_english"))
    businesskey = _text(row.get("aida_businesskey"))
    codes = re.findall(r"\b\d{2}(?:\.\d{2}){2,}\b", raw)
    if not codes:
        codes = re.findall(r"\b\d{2}(?:\.\d{2}){2,}\b", businesskey)
    if len(set(codes)) > 1:
        return "MULTI:" + raw, raw, raw
    code = codes[0] if codes else ""
    name = re.sub(r"^FL\s+", "", raw, flags=re.I)
    name = re.sub(r"\s*\[\d{2}(?:\.\d{2}){2,}\]\s*", " ", name).strip()
    name = re.sub(r"\s+", " ", name) or "Unclassified AIDA"
    return code, name, raw


def _status(value):
    text = re.sub(r"\s+", " ", _text(value)).casefold()
    return {"passed": "Passed", "failed": "Failed", "planned": "Planned",
            "requires attention": "Requires Attention"}.get(text, _text(value) or "Unknown")


ENVIRONMENT_SIGNALS = ("bench_tag_only", "vehicle_tag_only", "both_tags", "other_taxonomy", "no_platform_tag")
PLATFORM_CAPABILITIES = (
    {"dimension": "Repeatability and fault isolation", "bench": "Controlled inputs, power and repeatable configurations; capability depends on the rig.",
     "vehicle": "Real integration, with more variable background states.", "evidence_needed": "Configuration, stimulus and recovery logs; measured active effort."},
    {"dimension": "HMI, language and phone interoperability", "bench": "Can support broad regressions when real target hardware and devices are connected.",
     "vehicle": "Validates final displays, controls, devices and installation effects.", "evidence_needed": "Same testcase revision, device, software and assertion."},
    {"dimension": "Navigation and positioning", "bench": "GNSS/network simulation or replay can control reproducible route conditions.",
     "vehicle": "Validates real routes, positioning, coverage transitions and vehicle motion.", "evidence_needed": "Comparable routes, map versions, GNSS/network inputs and timestamps."},
    {"dimension": "Speech and acoustics", "bench": "Controlled recordings and injected audio can support repeatable language checks.",
     "vehicle": "Validates cabin microphones, speakers, occupants and road/HVAC noise.", "evidence_needed": "Audio path, cabin/noise conditions, language, device and software."},
    {"dimension": "Connectivity and regional services", "bench": "Controlled phones, access points and network impairments where supported.",
     "vehicle": "Validates real antennas, operators, location and regional backend behavior.", "evidence_needed": "SIM/operator, backend region, account, network and device configuration."},
    {"dimension": "Power, lifecycle and recovery", "bench": "Controlled voltage and bus simulation can expose repeatable boundary cases.",
     "vehicle": "Validates whole-vehicle wake/sleep participants, wiring and realistic timing.", "evidence_needed": "Power/bus traces, lifecycle state, timing and recovery observations."},
    {"dimension": "Effort and yield", "bench": "Setup/tooling may be shared across many runs; do not infer savings from ticket counts.",
     "vehicle": "Scheduling, preparation and route time require separate measured effort.", "evidence_needed": "Active/wait minutes, shared setup allocation and distinct verified findings."},
)


def environment_tag_signal(value):
    text = _text(value)
    car = bool(re.search(r"\bdWKP\b", text, re.I))
    rack = bool(re.search(r"\bsWKP\b", text, re.I))
    if car and rack:
        return "both_tags"
    if car:
        return "vehicle_tag_only"
    if rack:
        return "bench_tag_only"
    return "other_taxonomy" if text else "no_platform_tag"


def classify_v_number(value):
    text = _text(value)
    if not text or text.casefold() in {"unknown", "none", "null", "n/a", "na", "not available"}:
        return "Unknown"
    if text.upper() in CONFIRMED_V_NUMBER_TYPES:
        return CONFIRMED_V_NUMBER_TYPES[text.upper()]
    if re.fullmatch(r"[0-9]+", text):
        return "Vehicle"
    if re.fullmatch(r"[A-Za-z]+(?:[ _-][A-Za-z]+)*", text):
        return "Bench"
    return "Unknown"


def load_v_number_mapping(fleet_path=None, *, supplemental_fleet_paths=()):
    mappings = {}
    source_paths = ([fleet_path] if fleet_path is not None else []) + list(supplemental_fleet_paths)
    for source_path in source_paths:
        path = Path(source_path)
        fleet = json.loads(path.read_text(encoding="utf-8"))
        candidates = defaultdict(set)
        for platform in fleet.get("platforms", []):
            identifiers = platform.get("ids") or {}
            vin = _text(identifiers.get("vin")).upper()
            number = _text(identifiers.get("vNumber"))
            if vin and number:
                candidates[vin].add(number)
        for vin, numbers in candidates.items():
            number = next(iter(numbers)) if len(numbers) == 1 else ""
            mappings[vin] = {"vin": vin, "v_number": number, "v_number_candidates": sorted(numbers),
                             "actual_environment": classify_v_number(number), "v_number_source": str(path),
                             "mapping_snapshot_at": _text(fleet.get("fetched_at"))}
    for known in _inventory():
        vin = known["vin"]
        if known["v_number"]:
            previous = mappings.get(vin, {})
            number = known["v_number"]
            aliases = {number}
            if classify_v_number(previous.get("v_number")) == classify_v_number(number):
                aliases.add(previous["v_number"])
            mappings[vin] = {**known, "v_number_aliases": sorted(aliases),
                             "v_number_source": known.get("v_number_source", "user_confirmed_screenshot_vin_mapping"),
                             "mapping_snapshot_at": "2026-11-02" if vin in CONFIRMED_HISTORICAL_V_NUMBERS else "2026-10-30",
                             "supplemental_snapshot_v_number": previous.get("v_number", "")}
        elif vin not in mappings:
            mappings[vin] = {**known, "v_number_source": "unavailable", "mapping_snapshot_at": ""}
    for vin, number in CONFIRMED_HISTORICAL_V_NUMBERS.items():
        previous = mappings.get(vin, {})
        aliases = {number}
        if classify_v_number(previous.get("v_number")) == classify_v_number(number):
            aliases.update(previous.get("v_number_aliases", [previous.get("v_number", "")]))
        mappings[vin] = {**previous, "vin": vin, "v_number": number,
                         "v_number_aliases": sorted(alias for alias in aliases if alias),
                         "actual_environment": classify_v_number(number),
                         "v_number_source": "user_confirmed_2026-11-02", "mapping_snapshot_at": "2026-11-02",
                         "supplemental_snapshot_v_number": previous.get("supplemental_snapshot_v_number", previous.get("v_number", ""))}
    for vin, number in CONFIRMED_TW_V_NUMBERS.items():
        previous = mappings.get(vin, {})
        aliases = {number}
        if classify_v_number(previous.get("v_number")) == classify_v_number(number):
            aliases.update(previous.get("v_number_aliases", [previous.get("v_number", "")]))
        mappings[vin] = {**previous, "vin": vin, "v_number": number,
                         "v_number_aliases": sorted(alias for alias in aliases if alias),
                         "actual_environment": classify_v_number(number),
                         "v_number_source": "user_confirmed_tw_screenshot_2026-11-09",
                         "mapping_snapshot_at": "2026-11-09",
                         "supplemental_snapshot_v_number": previous.get("v_number", "")}
    return list(mappings.values())


def load_regional_inventory(fleet_path=None, market_mapping_path=None):
    root = Path(__file__).resolve().parents[2]
    mapping_path = Path(market_mapping_path) if market_mapping_path else Path(__file__).parent / "assets/data/vin_project_mapping.json"
    fleet_path = Path(fleet_path) if fleet_path else root / "exports/fleem/dtsv-china-active.json"
    fleet = json.loads(fleet_path.read_text(encoding="utf-8")) if fleet_path.exists() else {}
    mapping_rows = json.loads(mapping_path.read_text(encoding="utf-8"))
    markets = defaultdict(set)
    sources = defaultdict(set)
    fleet_by_vin = {}
    for row in mapping_rows:
        vin = _text(row.get("vin_prefix")).upper()
        if re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
            markets[vin].update([_text(row.get("market")).upper()] if row.get("market") else [])
            sources[vin].add(str(mapping_path))
    for row in fleet.get("project_market_classification", {}).get("platforms", []):
        vin = _text(row.get("vin")).upper()
        if re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
            markets[vin].update([_text(row.get("market")).upper()] if row.get("market") else [])
            sources[vin].add(str(fleet_path) + "#project_market_classification")
    for row in fleet.get("platforms", []):
        vin = _text((row.get("ids") or {}).get("vin")).upper()
        if re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
            fleet_by_vin.setdefault(vin, row)
            markets.setdefault(vin, set())
    number_by_vin = {row["vin"]: row for row in load_v_number_mapping(fleet_path if fleet_path.exists() else None)}
    inventory = {}
    for vin, candidates in markets.items():
        mapped = number_by_vin.get(vin, {})
        platform = fleet_by_vin.get(vin, {})
        region = next(iter(candidates)) if len(candidates) == 1 else "Unknown"
        inventory[vin] = {
            "vin": vin, "short_vin": vin[-7:], "region": region,
            "model": platform.get("fa", {}).get("current", {}).get("modelSeries", "Unknown"),
            "platform_name": mapped.get("v_number", ""), "v_number": mapped.get("v_number", ""),
            "actual_environment": mapped.get("actual_environment", "Unknown"),
            "v_number_source": mapped.get("v_number_source", "unavailable"),
            "mapping_snapshot_at": mapped.get("mapping_snapshot_at", ""),
            "evidence": "regional_mapping" if region == "CN" else "legacy_mapping_candidate",
            "region_source": sorted(sources[vin]), "region_observed_at": "undated_project_mapping",
            "mapped_market_candidates": sorted(candidates), "market_override": False,
        }
    for platform in _inventory():
        vin = platform["vin"]
        inventory[vin] = {**inventory.get(vin, {}), **platform, "region": "HK",
                          "region_source": "repository_confirmed_hk_inventory", "region_observed_at": "2026-11-02"}
    for vin, model in CONFIRMED_TW_PLATFORMS:
        mapped = number_by_vin.get(vin, {})
        previous = inventory.get(vin, {})
        inventory[vin] = {
            **previous, "vin": vin, "short_vin": vin[-7:], "model": model, "region": "TW",
            "platform_name": mapped.get("v_number", ""), "v_number": mapped.get("v_number", ""),
            "actual_environment": mapped.get("actual_environment", "Unknown"),
            "v_number_source": mapped.get("v_number_source", "unavailable"),
            "mapping_snapshot_at": mapped.get("mapping_snapshot_at", ""),
            "evidence": "user_confirmed", "region_source": "user_confirmed_fleem_screenshot_2026-11-09",
            "region_observed_at": "2026-11-09", "market_override": bool(set(previous.get("mapped_market_candidates", [])) - {"TW"}),
        }
    return sorted(inventory.values(), key=lambda row: (row["region"], row["vin"]))


def _inventory_basis(matched_vins, inventory):
    evidence = {row["evidence"] for row in inventory if row["vin"] in matched_vins}
    return next((value for value in ("user_confirmed", "regional_mapping", "legacy_mapping_candidate") if value in evidence), "not_in_regional_inventory")


def _platform_identity(value, platforms=None):
    platforms = _inventory() if platforms is None else platforms
    text = _text(value).upper()
    matches = match_vins(text, [row["vin"] for row in platforms])
    by_vin = {row["vin"]: row for row in platforms}
    resolved = [by_vin[vin] for vin in matches]
    source = "vin_to_v_number_user_rule"
    if not resolved:
        resolved = [row for row in platforms if any(re.search(
            r"(?<![A-Z0-9])" + re.escape(number.upper()) + r"(?![A-Z0-9])", text)
            for number in row.get("v_number_aliases", [row.get("v_number", "")]) if number)]
        source = "v_number_alias_user_rule"
    numbers = sorted({_text(row.get("v_number")) for row in resolved if row.get("v_number")})
    types = {classify_v_number(row.get("v_number")) for row in resolved}
    unknown_vins = set(re.findall(r"(?<![A-Z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Z0-9])", text)) - set(matches)
    if len(types - {"Unknown"}) > 1:
        environment, source = "Unknown", "conflicting_v_number_types"
    elif not resolved or "Unknown" in types or unknown_vins:
        environment, source = "Unknown", "unmapped_platform_identifier"
    else:
        environment = next(iter(types))
    return {"actual_environment": environment, "v_numbers": numbers,
            "environment_classification_source": source,
            "v_number_mapping_sources": sorted({row.get("v_number_source", "user_confirmed_screenshot_vin_mapping") for row in resolved}),
            "environment_classification_rule": "User-confirmed 2026-11-02: named V-number = bench; numeric V-number = vehicle; HKTW70/CNG70R confirmed bench; V547984/V718601 confirmed vehicle; ZHUDI0 confirmed by Fleem DTSV_China_R fleet 2026-11-10"}


def _named_text(value):
    if isinstance(value, dict):
        return _text(value["name"]) if value.get("name") else _named_text(value.get("data", []))
    if isinstance(value, list):
        return "; ".join(filter(None, (_named_text(item) for item in value)))
    return _text(value)


def _run_team(row):
    current = _text(row.get("team"))
    if current:
        return current, "team"
    native = _named_text(_raw_payload(row.get("raw_json")).get("run_team_000_udf"))
    return native, "raw_json.run_team_000_udf" if native else "missing"


def mr_platform_capture_info(row):
    identifier = _text(row.get("vehicle_value") or row.get("testplatformid"))
    captured = bool(identifier and identifier.casefold() not in {"none", "null", "n/a", "-"})
    native_checked = row.get("native_platform_lookup") == "Fetched"
    frozen_identifier = _text(row.get("frozen_platform_identifier")) if "frozen_platform_identifier" in row else identifier
    return {
        "mr_platform_capture_status": ("Identifier retrieved from native MR" if captured else "Native MR returned empty platform identifier") if native_checked else ("Platform identifier captured locally" if captured else "Not captured in local MR snapshot"),
        "frozen_mr_platform_capture_status": "Identifier captured in frozen local MR snapshot" if frozen_identifier else "Not captured in frozen local MR snapshot",
        "historical_mr_link_verified": False,
        "native_mr_link_status": ("Native MR not returned by current query" if row.get("native_platform_lookup_error") == "not_returned_by_native_query" else
                      ("Native identifier checked at " + _text(row.get("platform_identifier_observed_at"))) if native_checked else "Not checked against current Octane MR"),
        "native_mr_platform_field": "testplatformid_udf",
        "native_field_scope": "Test platform ID confirmed in native run_manual metadata; Equipment is a separate relation, not assumed to be a VIN",
    }


def _mr_capture_audit(payload):
    records = payload["hk_testcase_analysis"]["runs"]
    for collection in (payload["runs"], records):
        for row in collection:
            row.update(mr_platform_capture_info(row))
    groups = defaultdict(list)
    for row in records:
        groups[(row["year"], row["mr_platform_capture_status"])].append(row)
    native_count = sum(row.get("native_platform_lookup") == "Fetched" for row in records)
    enrichment = payload.get("native_mr_platform_enrichment", {})
    return {
        "entity": "manual_run", "native_platform_field": "testplatformid_udf",
        "native_mr_linkage_checked": bool(records) and native_count == len(records),
        "native_mr_records_checked": native_count,
        "historical_platform_backfill_performed": False,
        "current_native_platform_supplement_performed": bool(enrichment),
        "native_identifier_observed_at": enrichment.get("fetched_at", ""),
        "summary_scope": "Frozen regional platform manual-run records, not the native testcase definition" if payload["scope"].get("region", "HK") != "HK" else "Frozen HK-marked manual-run records, not the native testcase definition",
        "summary": [{"year": year, "capture_status": status, "run_records": len(rows)}
                    for (year, status), rows in sorted(groups.items())],
        "findings": [
            "The collector omitted the verified legacy testplatformid_udf field and the store only handled testplatformid; both paths are corrected in code.",
            "Missing local MR identifiers do not prove the native MR is unlinked; Equipment is a separate relation and is not automatically interpreted as VIN.",
            "Rendering alone does not refetch native MRs. When an explicit native-link supplement is present, only the frozen report MR IDs are enriched; the SQLite source is not updated.",
            "Historical MR IDs need an explicit identifier/relationship backfill after field metadata and existing authentication are verified; incremental modified-since fetches alone may skip unchanged MRs.",
            "A newly fetched MR identifier is current metadata, not proof that the same mutable link existed at the original execution time; original values and observation timestamps are retained.",
        ],
    }


def select_saved_mr_platform_snapshot(payload, snapshot):
    observed_at = _text(snapshot.get("fetched_at"))
    if _datetime(observed_at) is None:
        raise ValueError("Saved native MR snapshot needs its original observation timestamp")
    known = {}
    for rows in (payload["runs"], payload["hk_testcase_analysis"]["runs"], payload.get("vin_scope_candidate_runs", [])):
        for row in rows:
            run_id, test_id = _text(row.get("run_id")), _text(row.get("test_id"))
            if run_id in known and known[run_id] != test_id:
                raise ValueError("Frozen MR testcase binding is inconsistent: " + run_id)
            known[run_id] = test_id
    records = snapshot.get("records", [])
    selected, seen = [], set()
    for row in records:
        run_id = _text(row.get("id"))
        if not run_id or run_id in seen:
            raise ValueError("Missing or duplicate saved native MR ID: " + run_id)
        seen.add(run_id)
        if run_id not in known:
            continue
        test = row.get("test") or {}
        test_id = _text(test.get("id")) if isinstance(test, dict) else ""
        if not test_id or test_id != known[run_id]:
            raise ValueError("Saved native MR testcase differs from frozen evidence: " + run_id)
        if "testplatformid_udf" not in row:
            raise ValueError("Saved native MR platform field was not captured: " + run_id)
        if "observed_at" in row and _datetime(_text(row["observed_at"])) is None:
            raise ValueError("Saved native MR row needs a valid observation timestamp: " + run_id)
        selected.append(deepcopy(row))
    return {"fetched_at": observed_at, "records": selected, "read_mode": "saved_native_snapshot",
            "snapshot_file": snapshot.get("snapshot_file", ""),
            "source_snapshot_file": snapshot.get("snapshot_file", ""),
            "source_snapshot_sha256": snapshot.get("source_snapshot_sha256", ""),
            "source_database_updated": bool(snapshot.get("source_database_updated")),
            "source_backfill_result": deepcopy(snapshot.get("source_backfill_result", {})),
            "errors": [deepcopy(row) for row in snapshot.get("errors", []) if _text(row.get("id")) in known],
            "source_snapshot_records": len(records)}


def enrich_native_mr_platform_links(payload, snapshot):
    result = deepcopy(payload)
    if "vin_scope_candidate_runs" not in result:
        candidate_rows = {row["run_id"]: row for rows in (result["runs"], result["hk_testcase_analysis"]["runs"]) for row in rows}
        result["vin_scope_candidate_runs"] = deepcopy(list(candidate_rows.values()))
    collections = (result["runs"], result["hk_testcase_analysis"]["runs"], result["vin_scope_candidate_runs"])
    known_ids = {row["run_id"] for rows in collections for row in rows}
    native = {}
    for row in snapshot.get("records", []):
        run_id = _text(row.get("id"))
        if run_id not in known_ids or run_id in native:
            raise ValueError("Unexpected or duplicate native MR ID: " + run_id)
        native[run_id] = row
    observed_at = _text(snapshot.get("fetched_at"))
    errors = {_text(row.get("id")): row for row in snapshot.get("errors", []) if _text(row.get("id")) in known_ids}
    for rows in collections:
        for row in rows:
            current = native.get(row["run_id"])
            if current is None:
                if row["run_id"] in errors:
                    row["native_platform_lookup_error"] = errors[row["run_id"]].get("reason", "native_read_error")
                    row.update(mr_platform_capture_info(row))
                continue
            native_test = current.get("test") or {}
            native_test_id = _text(native_test.get("id")) if isinstance(native_test, dict) else ""
            if native_test_id and native_test_id != row["test_id"]:
                raise ValueError("Native MR testcase differs from frozen evidence: " + row["run_id"])
            if "testplatformid_udf" not in current:
                continue
            row.setdefault("frozen_platform_identifier", row.get("vehicle_value", ""))
            row["vehicle_value"] = _text(current.get("testplatformid_udf"))
            row["vin_source"] = "native_mr.testplatformid_udf"
            row["native_platform_lookup"] = "Fetched"
            row["platform_identifier_observed_at"] = _text(current.get("observed_at")) or observed_at
            row.pop("native_platform_lookup_error", None)
            row["native_equipment_relation"] = current.get("equipment_udf")
            row["native_mr_last_modified"] = _text(current.get("last_modified"))
            row.update(mr_platform_capture_info(row))
    mapping = result.get("v_number_mapping") or load_v_number_mapping()
    result = refresh_platform_types(result, mapping)
    result["native_mr_platform_enrichment"] = {
        "fetched_at": observed_at, "native_records_read": len(native), "report_mr_ids": len(known_ids),
        "snapshot_file": snapshot.get("snapshot_file", ""), "source_database_updated": False,
        "source_snapshot_database_updated": bool(snapshot.get("source_database_updated")),
        "source_snapshot_backfill_result": deepcopy(snapshot.get("source_backfill_result", {})),
        "unavailable_mr_records": len(errors), "native_read_errors": list(errors.values()),
        "frozen_execution_results_preserved": True, "frozen_case_and_defect_sets_preserved": True,
        "identifier_temporal_basis": "Current native MR metadata observed after the frozen execution/result snapshot",
        "derived_vin_cohort_rebuilt": True,
    }
    result["supplemental_read"] = {"status": "native_mr_platform_fields_read", "fetched_at": observed_at,
                                   "scope": "Existing frozen report MR IDs only", "source_sync": False,
                                   "upstream_updates": False, "source_database_updates": False}
    if snapshot.get("read_mode") == "saved_native_snapshot":
        result["native_mr_platform_enrichment"].update({
            "read_mode": "saved_native_snapshot", "reused_at": datetime.now(timezone.utc).isoformat(),
            "source_snapshot_file": snapshot.get("source_snapshot_file", ""),
            "source_snapshot_sha256": snapshot.get("source_snapshot_sha256", ""),
            "source_snapshot_records": snapshot.get("source_snapshot_records", 0),
            "network_requests_performed": False,
        })
        result["supplemental_read"].update({
            "status": "saved_native_mr_snapshot_reused", "read_mode": "saved_native_snapshot",
            "native_mr_records_read": 0, "local_native_mr_records_reused": len(native),
            "previous_native_read_attempt": deepcopy(payload.get("supplemental_read", {})),
            "network_requests_performed": False,
        })
    return result


def _environment_fields(row, raw):
    taxonomy = _text(row.get("taxonomies")) or _named_text(raw.get("taxonomies"))
    identifier = _text(row.get("testplatformid"))
    if not identifier:
        identifier = next((_text(raw.get(key)) for key in VEHICLE_KEYS if raw.get(key)), "")
    return {"environment_taxonomies": taxonomy, "environment_tag_signal": environment_tag_signal(taxonomy),
            "testing_tool_type": _text(row.get("testing_tool_type")) or _named_text(raw.get("testing_tool_type")),
            "exec_model_series": _text(row.get("exec_model_series")),
            "target_ecu_conf": _text(row.get("target_ecu_conf")), **_platform_identity(identifier)}


def _environment_comparison(runs):
    evidence_keys = ("run_id", "test_id", "test_name", "year", "date", "date_basis", "status", "is_executed",
                     "aida_code", "aida_name", "release", "software_version", "vehicle_value", "vin_source",
                     "environment_taxonomies", "environment_tag_signal", "testing_tool_type", "exec_model_series",
                     "target_ecu_conf", "actual_environment", "v_numbers", "environment_classification_source",
                     "environment_classification_rule", "v_number_mapping_sources", "mr_platform_capture_status",
                     "native_mr_link_status", "platform_identifier_observed_at", "frozen_platform_identifier", "native_platform_lookup_error")
    evidence = [{key: row.get(key, "") for key in evidence_keys} for row in runs]
    summary = [{"environment_tag_signal": signal, **_metrics(
        [row for row in runs if row.get("environment_tag_signal", "no_platform_tag") == signal], None)}
        for signal in ENVIRONMENT_SIGNALS]
    groups = defaultdict(list)
    for row in runs:
        groups[row["aida_key"]].append(row)
    aida_rows = []
    for records in groups.values():
        counts = Counter(row.get("environment_tag_signal", "no_platform_tag") for row in records)
        aida_rows.append({"aida_code": records[0]["aida_code"], "aida_name": records[0]["aida_name"],
                          **{signal: counts[signal] for signal in ENVIRONMENT_SIGNALS}, **_metrics(records, None)})
    aida_rows.sort(key=lambda row: (-row["executed_runs"], row["aida_name"]))
    actual_summary = [{"actual_environment": environment, **_metrics(
        [row for row in runs if row.get("actual_environment", "Unknown") == environment], None)}
        for environment in ("Bench", "Vehicle", "Unknown")]
    bench, vehicle, unknown = actual_summary
    return {
        "actual_comparison_ready": bool(bench["executed_runs"] and vehicle["executed_runs"]),
        "classification_available": bool(bench["run_records"] or vehicle["run_records"]),
        "actual_environment_unknown_records": unknown["run_records"],
        "actual_bench_executions": bench["executed_runs"], "actual_vehicle_executions": vehicle["executed_runs"],
        "actual_summary": actual_summary,
        "matched_case_version_comparison": None, "signal_summary": summary,
        "aida_summary": aida_rows, "evidence": evidence,
        "capability_guidance": [dict(row) for row in PLATFORM_CAPABILITIES],
        "required_evidence": [
            "V-number mapping classifies known platforms; dWKP/sWKP remain separate taxonomy observations, not the classification authority.",
            "Resolve V-number for execution identifiers not covered by the known VIN-to-V-number inventory; retain event-time validity.",
            "For matched outcome comparisons: same testcase revision, software, release and relevant device/region configuration.",
            "For discovery/efficiency comparisons: first-discovery provenance and measured active/wait/setup effort.",
        ],
        "limitations": [
            "dWKP and sWKP are observed taxonomy signals, not independently verified actual execution environments.",
            "Both-tagged records are one separate group, never duplicated into both bench and vehicle totals.",
            "Platform type follows the user's V-number rule, not taxonomy, VIN format, model name, Manual Runner or ECU configuration alone.",
            "Known-inventory V-number mapping is projected to historical records; dated platform-type changes are not available.",
            "Unknown identifiers remain separate; zero classified samples is not proof of no work. Matched same-case/software outcomes remain unassessed.",
            "Capability guidance is engineering context conditional on rig/vehicle configuration, not measured findings from this dataset.",
        ],
    }


def _read_rows(connection, table, wanted, *, where="", params=()):
    available = {row[1] for row in connection.execute("PRAGMA table_info(" + table + ")")}
    columns = [name for name in wanted if name in available]
    if not columns:
        return
    query = "SELECT " + ", ".join('"' + name + '"' for name in columns) + " FROM " + table
    if where:
        query += " WHERE " + where
    for row in connection.execute(query, params):
        yield dict(row)


def _raw_payload(value):
    try:
        payload = json.loads(value or "{}")
    except (ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _linked_ids(value):
    if isinstance(value, dict):
        if value.get("id"):
            return {_text(value["id"])}
        return _linked_ids(value.get("data", []))
    if isinstance(value, list):
        return set().union(*(_linked_ids(item) for item in value)) if value else set()
    return {part.strip() for part in re.split(r"[,;]", _text(value)) if part.strip()}


def _latest_vin_history(connection):
    columns = {row[1] for row in connection.execute("PRAGMA table_info(octane_defect_history_events)")}
    required = {"defect_id", "event_timestamp", "field_name", "new_value"}
    if not required.issubset(columns):
        return {}
    ordinal_columns = [name for name in ("entry_index", "change_index") if name in columns]
    selected = ["defect_id", "event_timestamp", "new_value", *ordinal_columns]
    query = "SELECT " + ", ".join(selected) + " FROM octane_defect_history_events WHERE field_name IN ('vin_udf','vin')"
    latest = {}
    for row in connection.execute(query):
        timestamp = _datetime(row["event_timestamp"])
        if timestamp is None:
            continue
        key = (timestamp, *(int(row[name] or 0) for name in ordinal_columns))
        defect_id = _text(row["defect_id"])
        previous = latest.get(defect_id)
        if previous is None or key > previous["key"]:
            latest[defect_id] = {"key": key, "value": _text(row["new_value"]),
                                 "timestamp": _text(row["event_timestamp"])}
        elif key == previous["key"] and _text(row["new_value"]) != previous["value"]:
            previous["value"] = ""
    return latest


def _metrics(runs, defects):
    defect_metrics_assessed = defects is not None
    defects = defects or []
    executed = [row for row in runs if row["is_executed"]]
    statuses = Counter(row["status"] for row in runs)
    passed_failed = statuses["Passed"] + statuses["Failed"]
    return {
        "run_records": len(runs), "executed_runs": len(executed),
        "planned_runs": statuses["Planned"], "passed_runs": statuses["Passed"],
        "failed_runs": statuses["Failed"], "attention_runs": statuses["Requires Attention"],
        "other_status_runs": sum(count for status, count in statuses.items() if status not in EXECUTED_STATUSES | {"Planned"}),
        "executed_testcases": len({row["test_id"] for row in executed if row["test_id"]}),
        "testcases_with_records": len({row["test_id"] for row in runs if row["test_id"]}),
        "pass_rate": round(100 * statuses["Passed"] / passed_failed, 2) if passed_failed else None,
        "pass_rate_denominator": passed_failed,
        "defect_count": len(defects) if defect_metrics_assessed else None,
        "phase06_defects": sum(row["phase_code"] == "06" for row in defects) if defect_metrics_assessed else None,
        "phase09_defects": sum(row["phase_code"] == "09" for row in defects) if defect_metrics_assessed else None,
        "other_phase_defects": sum(row["phase_code"] not in {"06", "09"} for row in defects) if defect_metrics_assessed else None,
        "run_creation_date_fallback": sum(row["date_basis"] == "creation_time" for row in runs),
        "linked_defects": len(set().union(*(set(row["linked_defect_ids"]) for row in runs))) if runs else 0,
        "first_execution_date": min((row["date"] for row in executed), default=""),
        "last_execution_date": max((row["date"] for row in executed), default=""),
        "first_defect_date": min((row["date"] for row in defects), default=""),
        "last_defect_date": max((row["date"] for row in defects), default=""),
    }


def _group_summaries(runs, defects, key):
    groups = defaultdict(lambda: {"runs": [], "defects": []})
    for kind, records in (("runs", runs), ("defects", defects or [])):
        for row in records:
            groups[row[key]][kind].append(row)
    return [{key: value, **_metrics(group["runs"], group["defects"] if defects is not None else None)}
            for value, group in sorted(groups.items(), key=lambda item: str(item[0]))]


def _hk_testcase_analysis(connection, years, known_vins, vin_runs, vin_defects, *, team_name=None, region="HK"):
    catalog = {}
    excluded_regional_testcase_ids = set()
    uses_title_cohort = region in {"HK", "TW"}
    matches_title = has_tw_title if region == "TW" else has_hk_title
    title_pattern = TW_TITLE_PATTERN if region == "TW" else HK_TITLE_PATTERN
    team_test_ids = {_text(row.get("test_id"))
                     for row in _read_rows(connection, "octane_manual_runs", ("test_id", "team", "raw_json"))
                     if _team_matches(_run_team(row)[0], team_name)} if team_name is not None else set()

    def register(test_id, title, source):
        case = catalog.setdefault(test_id, {"test_id": test_id, "title_variants": set(),
                                           "hk_marked_titles": set(), "classification_sources": set(),
                                           "regional_marked_titles": set(),
                                           "catalog_scopes": set(), "catalog_record_count": 0,
                                           "aida_codes": set(), "aida_names": set()})
        if title:
            case["title_variants"].add(title)
        if has_hk_title(title):
            case["hk_marked_titles"].add(title)
            case["classification_sources"].add(source)
        if uses_title_cohort and matches_title(title):
            case["regional_marked_titles"].add(title)
            case["classification_sources"].add(source)
        if not uses_title_cohort:
            case["classification_sources"].add(source)
            if has_cn_title(title):
                case["regional_marked_titles"].add(title)
        return case

    vin_runs_by_id = {row["run_id"]: row for row in vin_runs}
    if not uses_title_cohort:
        for row in _read_rows(connection, "octane_manual_runs", ("test_id", "test_name", "team", "raw_json")):
            test_id, title = _text(row.get("test_id")), _text(row.get("test_name"))
            if not test_id or not _team_matches(_run_team(row)[0], team_name):
                continue
            register(test_id, title, "run.team")
            if has_hk_title(title) or has_tw_title(title):
                excluded_regional_testcase_ids.add(test_id)
    for row in _read_rows(connection, "octane_testcases", ("test_id", "test_name", "scope_team", "scope_release")):
        test_id, title = _text(row.get("test_id")), _text(row.get("test_name"))
        team_evidenced = (_team_matches(row.get("scope_team"), team_name)
                          or (_text(row.get("scope_team")).upper() == "ALL" and test_id in team_test_ids))
        if not team_evidenced or not test_id:
            continue
        if uses_title_cohort and not matches_title(title):
            continue
        if region == "CN" and (has_hk_title(title) or has_tw_title(title)):
            excluded_regional_testcase_ids.add(test_id)
        case = register(test_id, title, "catalog.test_name")
        case["catalog_record_count"] += 1
        case["catalog_scopes"].add(_text(row.get("scope_team")) + " / " + _text(row.get("scope_release")))
    for row in _read_rows(connection, "octane_manual_runs", ("test_id", "test_name", "team", "raw_json")):
        test_id, title = _text(row.get("test_id")), _text(row.get("test_name"))
        if uses_title_cohort and _team_matches(_run_team(row)[0], team_name) and test_id and matches_title(title):
            register(test_id, title, "run.test_name")
    if region == "CN":
        catalog = {test_id: case for test_id, case in catalog.items() if test_id not in excluded_regional_testcase_ids}
        for case in catalog.values():
            case["testcase_scope_basis"] = "explicit_cn_title" if case["regional_marked_titles"] else "team_non_hk_tw_unmarked"
    selected_runs = []
    quality = Counter()
    if catalog:
        placeholders = ",".join("?" for test_id in catalog)
        for row in _read_rows(connection, "octane_manual_runs", RUN_FIELDS,
                              where="CAST(test_id AS TEXT) IN (" + placeholders + ")", params=tuple(catalog)):
            run_team, team_source = _run_team(row)
            if not _team_matches(run_team, team_name):
                quality["outside_team_scope_records"] += 1
                continue
            case = catalog[_text(row["test_id"])]
            title = _text(row.get("test_name"))
            if title:
                case["title_variants"].add(title)
            code, name, original = _aida(row)
            if code:
                case["aida_codes"].add(code)
            case["aida_names"].add(name)
            event_date, basis = _event_date(row, ("finished", "started", "creation_time"))
            if event_date is None or event_date.year not in years:
                quality["missing_date_records" if event_date is None else "outside_period_records"] += 1
                continue
            raw = _raw_payload(row.get("raw_json"))
            vehicle_value = _text(row.get("testplatformid"))
            vin_source = "testplatformid" if vehicle_value else "missing"
            if not vehicle_value:
                for key in VEHICLE_KEYS:
                    candidate = _text(raw.get(key))
                    if candidate and candidate.casefold() not in {"none", "null", "n/a", "-"}:
                        vehicle_value, vin_source = candidate, "raw_json." + key
                        break
            matched = match_vins(vehicle_value, known_vins)
            if region != "HK":
                matched = list(vin_runs_by_id.get(_text(row["mr_id"]), {}).get("matched_vins", []))
            status = _status(row.get("status"))
            linked = _linked_ids(row.get("defect_id")) | _linked_ids(raw.get("defect")) | _linked_ids(raw.get("linked_defects"))
            vehicle_basis = ("known_hk_vin" if region == "HK" else "known_regional_platform") if matched else "other_or_unmapped_identifier" if vehicle_value else "missing_identifier"
            quality[vehicle_basis] += 1
            selected_runs.append({
                "run_id": _text(row["mr_id"]), "test_id": _text(row["test_id"]), "test_name": title,
                "status": status, "is_executed": status in EXECUTED_STATUSES,
                "date": event_date.isoformat(), "date_basis": basis,
                "year": event_date.year, "source_year": _text(row.get("year")), "month": event_date.strftime("%Y-%m"),
                "aida_code": code, "aida_name": name, "aida_raw": original, "aida_key": code or "NAME:" + name,
                "matched_vins": matched, "vehicle_value": vehicle_value, "vin_source": vin_source,
                "vehicle_attribution": vehicle_basis, "title_on_run_has_hk_marker": has_hk_title(title),
                "title_on_run_has_regional_marker": uses_title_cohort and matches_title(title),
                "classification_sources": sorted(case["classification_sources"]), "linked_defect_ids": sorted(linked),
                "project": _text(row.get("project")), "release": _text(row.get("release")),
                "software_version": _text(row.get("execution_sw_version")),
                "team": run_team, "team_source": team_source,
                **_environment_fields(row, raw),
            })
    selected_runs.sort(key=lambda row: (row["date"], _sortable_run_id(row["run_id"])))
    runs_by_case = defaultdict(list)
    for row in selected_runs:
        runs_by_case[row["test_id"]].append(row)
    cases = []
    for test_id, data in sorted(catalog.items()):
        records = runs_by_case[test_id]
        executions = [row for row in records if row["is_executed"]]
        latest_record = records[-1] if records else {}
        latest_execution = executions[-1] if executions else {}
        cases.append({
            **{key: sorted(value) if isinstance(value, set) else value for key, value in data.items()},
            "test_name": next(iter(sorted(data["regional_marked_titles"] or data["hk_marked_titles"] or data["title_variants"])), "Test " + test_id), **_metrics(records, None),
            "latest_record_status": latest_record.get("status", "No record"),
            "latest_record_date": latest_record.get("date", ""),
            "latest_executed_status": latest_execution.get("status", "No execution"),
            "latest_execution_date": latest_execution.get("date", ""),
            "ever_failed": any(row["status"] == "Failed" for row in executions),
            "ever_requires_attention": any(row["status"] == "Requires Attention" for row in executions),
            "additional_execution_records": max(0, len(executions) - 1),
            "linked_defect_ids": sorted({defect_id for row in records for defect_id in row["linked_defect_ids"]}),
        })
    aida_groups = defaultdict(list)
    for row in selected_runs:
        aida_groups[row["aida_key"]].append(row)
    aida_summary = [{"aida_code": records[0]["aida_code"], "aida_name": records[0]["aida_name"],
                     "raw_name_variants": sorted({row["aida_raw"] for row in records if row["aida_raw"]}),
                     **_metrics(records, None)} for records in aida_groups.values()]
    aida_summary.sort(key=lambda row: (-row["executed_runs"], row["aida_name"]))
    selected_ids = {row["run_id"] for row in selected_runs}
    overlap = [row for row in vin_runs if row["run_id"] in selected_ids]
    yearly = {row["year"]: row for row in _group_summaries(selected_runs, None, "year")}
    linked_ids = {defect_id for row in selected_runs for defect_id in row["linked_defect_ids"]}
    vin_defect_ids = {row["defect_id"] for row in vin_defects}
    overview = {**_metrics(selected_runs, None), "testcase_ids": len(cases),
                "title_marked_testcases": sum(bool(row["regional_marked_titles"]) for row in cases) if region == "CN" else len(cases),
                "catalog_backed_testcases": sum(row["catalog_record_count"] > 0 for row in cases),
                "run_title_only_testcases": sum(row["catalog_record_count"] == 0 for row in cases),
                "cases_without_period_records": sum(not row["run_records"] for row in cases),
                "cases_without_period_execution": sum(not row["executed_runs"] for row in cases),
                "cases_ever_failed": sum(row["ever_failed"] for row in cases),
                "cases_ever_requires_attention": sum(row["ever_requires_attention"] for row in cases),
                "additional_execution_records": sum(row["additional_execution_records"] for row in cases),
                "linked_defects_in_vin_scope": len(linked_ids & vin_defect_ids),
                "linked_defects_not_in_vin_scope": len(linked_ids - vin_defect_ids)}
    return {
          "scope": {"classification": "explicit_" + region.lower() + "_title" if uses_title_cohort else "dtsv_china_non_hk_tw_testcase",
                "region": region, "title_pattern": title_pattern.pattern if uses_title_cohort else None,
                  "team_name": team_name,
                  "years": sorted(years), "time_basis": "finished_then_started_then_labelled_creation_fallback",
                  "native_testcase_definition_complete": False, "physical_execution_location_proven": False},
        "overview": overview, "cases": cases, "runs": selected_runs, "aida_summary": aida_summary,
        "excluded_regional_testcase_ids": sorted(excluded_regional_testcase_ids),
        "year_summary": [yearly.get(year, {"year": year, **_metrics([], None)}) for year in sorted(years)],
        "month_summary": _group_summaries(selected_runs, None, "month"), "quality": dict(quality),
        "environment_comparison": _environment_comparison(selected_runs),
        "latest_executed_status_counts": dict(Counter(row["latest_executed_status"] for row in cases)),
        "latest_record_status_counts": dict(Counter(row["latest_record_status"] for row in cases)),
        "overlap_with_vin_scope": {**_metrics(overlap, None), "run_ids": sorted(row["run_id"] for row in overlap)},
        "limitations": [
            "The primary testcase cohort is DTSV China supported testcase IDs after excluding HK/Hong Kong and TW/Taiwan title evidence from catalog or team-run snapshots; their MRs are joined by test_id.",
            "Retain unmarked cases, unmarked repeat-run titles, missing-platform MRs and cases without period execution; this non-HK/TW test scope is not proof of physical mainland location or approved CN-only applicability.",
            "Explicit CN/China title evidence and unmarked non-HK/TW cases are separately labelled through testcase_scope_basis; an absent regional title is not positive geographic evidence.",
            "Catalog ALL rows are used only with a matching team-run testcase ID; they are not proof of testcase ownership or historical native revisions.",
            "HK-named JSON keys are retained for compatibility; scope.classification defines the regional platform cohort.",
            "Strict positive-CN VIN executions and defects remain separate from this testcase cohort. Known HK/TW or other-platform executions are not reassigned to a mainland VIN through a shared testcase ID.",
            "Linked defects remain associations, not first-discovery evidence or additional platform defects.",
        ] if region == "CN" else [
            "TW/Taiwan title marking selects regional testcase intent, not exclusive applicability or proof of a TW platform or physical location.",
            "Use catalog and run titles to select testcase IDs, then include their team-evidenced calendar-period MRs through test_id even when a run title is unmarked or its platform field was not captured.",
            "Cases without a local period execution remain in the catalog; absence of a local record is not proof of no upstream testing.",
            "TW-title executions and strict TW-platform executions are separate overlapping cohorts and must not be added without deduplicating IDs.",
            "A TW-title case run on an HK or other platform is testcase-intent evidence only; it is not reassigned to a TW VIN.",
            "Catalog ALL rows require a matching team-evidenced MR/test ID; missing platform identifiers remain capture gaps until native evidence is observed.",
            "Platform defects are separately VIN-attributed; linked defects do not establish first discovery or fill an MR platform gap.",
        ] if region == "TW" else [
            "HK title marking indicates regional intent, not exclusive HK applicability or physical execution in Hong Kong.",
            "Catalog rows are local run-derived snapshots, not full native testcase definitions, approved market fields, scripts or expected results.",
            "Catalog-title evidence selects matching test IDs even when an individual run title lacks HK; original title variants and sources remain visible.",
            "A title snapshot does not prove that the case had the same market applicability at every historical execution date.",
            "Cases without period execution have no matching local record, which does not establish that no testing happened upstream.",
            "Catalog AIDA names come from observed local runs across available dates; AIDA execution totals use only selected-period records.",
            "Repeated execution records are not automatically defect retests; latest outcome is per testcase across platforms, not a release certification.",
            "HK-marked and HK-VIN cohorts overlap and must never be added without deduplicating run IDs and testcase IDs.",
            "Linked defects are associations, not proof of initial discovery or HK vehicle attribution, and do not increase the VIN defect count.",
            "VIN defect counts and phase metrics are unassessed (null/blank) in the title cohort; only linked defect IDs are counted as associations.",
        ],
    }


def _sortable_run_id(value):
    text = _text(value)
    return (0, int(text)) if text.isdigit() else (1, text)


def build_report_views(payload):
    analysis = payload["hk_testcase_analysis"]
    runs, defects = analysis["runs"], payload["defects"]
    region = payload["scope"].get("region", "HK")
    label = REGION_LABELS[region]
    summary = []
    for environment in ("Bench", "Vehicle"):
        summary.append({"actual_environment": environment,
                        "display_name": "Rack / Static-origin" if environment == "Bench" else "Vehicle / Dynamic-origin",
                        "management_origin": "Static-origin" if environment == "Bench" else "Dynamic-origin",
                        "actual_vehicle_motion": "Unknown", **_metrics(
                            [row for row in runs if row.get("actual_environment") == environment],
                            [row for row in defects if row.get("actual_environment") == environment])})
    grouped = defaultdict(lambda: {"runs": [], "defects": []})
    for kind, records in (("runs", runs), ("defects", defects)):
        for row in records:
            grouped[row["aida_key"]][kind].append(row)
    aida_rows = []
    for group in grouped.values():
        representative = (group["runs"] + group["defects"])[0]
        result = {"aida_code": representative["aida_code"], "aida_name": representative["aida_name"]}
        for environment, prefix in (("Bench", "rack"), ("Vehicle", "vehicle")):
            selected = [row for row in group["runs"] if row.get("actual_environment") == environment and row["is_executed"]]
            result[prefix + "_executed_runs"] = len(selected)
            result[prefix + "_testcases"] = len({row["test_id"] for row in selected if row["test_id"]})
            result[prefix + "_defects"] = sum(row.get("actual_environment") == environment for row in group["defects"])
        aida_rows.append(result)
    aida_rows.sort(key=lambda row: (-row["rack_executed_runs"] - row["vehicle_executed_runs"], row["aida_name"]))
    return {"hk_overview": {"testcase_ids": len(analysis["cases"]), **_metrics(runs, defects),
                             "defects_are_testcase_discoveries": False,
                             "scope": "TW-title testcase/execution evidence and TW-platform defect evidence are separate sets; no discovery attribution is implied." if region == "TW" else "DTSV China non-HK/TW testcase and MR evidence plus separately positive-CN-platform defects; unmarked testcase scope does not prove physical mainland location." if region == "CN" else "HK-title testcase/execution evidence and HK-platform defect evidence are separate sets; no discovery attribution is implied."},
            "platform_comparison": {"summary": summary, "aida_summary": aida_rows,
                                    "unclassified_executed_runs": sum(row["is_executed"] and row.get("actual_environment", "Unknown") == "Unknown" for row in runs),
                                    "unclassified_defects": sum(row.get("actual_environment", "Unknown") == "Unknown" for row in defects),
                                    "same_case_version_equivalence_verified": False,
                                    "scope": "TW-title execution evidence by platform type plus separate TW-platform defect evidence; not restricted to the strict TW-VIN execution subset." if region == "TW" else "DTSV China non-HK/TW testcase MR evidence by platform type plus separate positive-CN-platform defects; source type and testcase intent do not establish physical mainland location." if region == "CN" else "HK-title execution evidence by platform type plus separate HK-platform defect evidence; not restricted to the known-VIN execution subset.",
                                    "limitations": ["Dynamic/static are management source labels, not measured motion states.",
                                                    "Raw platform outcomes have different testcase/software mixes and are not a controlled effectiveness comparison.",
                                                    "Unclassified records are not reassigned or investigated in this presentation update."]}}


AUTOMATION_GATES = (
    {"gate": "Versioned testcase definition", "evidence_needed": "Approved preconditions, actions, expected results and testcase revision.", "default_status": "Unreviewed"},
    {"gate": "Machine-verifiable oracle", "evidence_needed": "Observable API, bus/log signal or validated visual/audio assertion with tolerances and timing.", "default_status": "Unreviewed"},
    {"gate": "Controllable setup and cleanup", "evidence_needed": "Device/account/configuration setup, repeatable stimulus, isolation and reset/recovery.", "default_status": "Unreviewed"},
    {"gate": "Physical-context equivalence", "evidence_needed": "Identify real motion, GNSS/network, cabin acoustics and power dependencies; validate replay and retained vehicle checks.", "default_status": "Unreviewed"},
    {"gate": "Comparable repeat volume", "evidence_needed": "Separate testcase/software/device/region revisions and select eligible regression executions, not all historical repeats.", "default_status": "Unreviewed"},
    {"gate": "Pilot effectiveness", "evidence_needed": "Human-reviewed outcomes, false-pass/false-fail rate, flakiness, recovery behavior and evidence retention.", "default_status": "Unreviewed"},
    {"gate": "Measured effort and ownership", "evidence_needed": "Sample active manual/supervision time; one-time build cost, period maintenance, validation effort and accountable owner.", "default_status": "Unreviewed"},
)
AUTOMATION_ROUTES = (
    ("Physical-context review", r"\b(?:driv\w*|speed\w*|road\w*|tunnel\w*|gnss|position\w*|parking|microphone\w*|acoustic\w*|noise|echo)\b",
     "Capture/replay or simulated stimuli for reproducible checks; retain targeted real-vehicle validation until equivalence is demonstrated."),
    ("Timing/lifecycle review", r"\b(?:sleep|wake\w*|power|restart|reboot|lifecycle|ignition|timing|timeout)\b",
     "Review controlled power/bus instrumentation, timestamps and automated recovery before a repeatable pilot."),
    ("Input/output matrix review", r"\b(?:speller|keyboard|input|search|contact\w*|language|traditional|alphabet\w*|character\w*|cangjie|strokes?)\b",
     "Review data-driven input matrices and machine-observable UI/API assertions with deterministic setup and reset."),
    ("State/interaction review", r"\b(?:login|logout|account\w*|profile\w*|permission\w*|pair\w*|connect\w*|disconnect\w*|switch\w*|user)\b",
     "Review device/account orchestration, state assertions, isolation and cleanup; retain human handling of unresolved external dependencies."),
)


def effort_scenario(execution_count=None, manual_minutes=None, adoption_fraction=None,
                    supervision_minutes=None, build_hours=None, maintenance_hours=None, validation_hours=None):
    inputs = {"execution_count": execution_count, "manual_minutes": manual_minutes,
              "adoption_fraction": adoption_fraction, "supervision_minutes": supervision_minutes,
              "build_hours": build_hours, "maintenance_hours": maintenance_hours, "validation_hours": validation_hours}
    for key, value in inputs.items():
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
            raise ValueError(key + " must be a finite nonnegative number")
    if adoption_fraction is not None and adoption_fraction > 1:
        raise ValueError("adoption_fraction must be between 0 and 1")
    if execution_count is not None and int(execution_count) != execution_count:
        raise ValueError("execution_count must be a whole execution count")
    result = {"inputs": inputs, "status": "Not configured", "is_measured_saving": False,
              "manual_baseline_hours": None, "automated_scenario_hours": None,
              "net_scenario_hours_saved": None, "break_even_executions": None}
    if any(value is None for value in inputs.values()):
        return result
    unit_gain = adoption_fraction * (manual_minutes - supervision_minutes) / 60
    fixed_cost = build_hours + maintenance_hours + validation_hours
    baseline = execution_count * manual_minutes / 60
    proposed = execution_count * ((1 - adoption_fraction) * manual_minutes + adoption_fraction * supervision_minutes) / 60 + fixed_cost
    result.update({"status": "Hypothetical scenario", "manual_baseline_hours": round(baseline, 2),
                   "automated_scenario_hours": round(proposed, 2), "net_scenario_hours_saved": round(baseline - proposed, 2),
                   "break_even_executions": math.ceil(fixed_cost / unit_gain) if unit_gain > 0 else None})
    return result


def build_capacity_insights(payload, planning_year=2027, person_capacity_hours=1840):
    if not math.isfinite(person_capacity_hours) or person_capacity_hours <= 0:
        raise ValueError("person_capacity_hours must be positive")
    analysis = payload["hk_testcase_analysis"]
    region = payload["scope"].get("region", "HK")
    label = REGION_LABELS[region]
    cohort_label = region + "-title" if region in {"HK", "TW"} else "DTSV China non-HK/TW testcase"
    automation = build_automation_analysis(payload)
    executions = [row for row in analysis["runs"] if row["is_executed"]]
    baseline_year = planning_year - 1
    recent = [row for row in executions if row["year"] == baseline_year]
    by_case = defaultdict(set)
    for row in executions:
        by_case[row["test_id"]].add(row.get("actual_environment", "Unknown"))
    both_ids = {test_id for test_id, platforms in by_case.items() if {"Bench", "Vehicle"}.issubset(platforms)}
    cases = {row["test_id"]: row for row in automation["cases"]}
    automation_ids = {test_id for test_id, row in cases.items()
                      if row["review_route"] in {"Input/output matrix review", "State/interaction review"}}
    automation_runs = [row for row in recent if row["test_id"] in automation_ids]
    transfer_runs = [row for row in recent if row["actual_environment"] == "Vehicle"
                     and row["test_id"] in both_ids and row["test_id"] not in automation_ids
                     and cases[row["test_id"]]["review_route"] in {"General feasibility review", "Timing/lifecycle review"}]
    cutoff = _datetime(payload["source"]["latest_fetched_at"].get("runs"))
    days_in_year = (datetime(baseline_year + 1, 1, 1) - datetime(baseline_year, 1, 1)).days
    observed_days = cutoff.timetuple().tm_yday if cutoff and cutoff.year == baseline_year else None
    factor = days_in_year / observed_days if observed_days else None
    auto_volume = round(len(automation_runs) * factor) if factor else None
    transfer_volume = round(len(transfer_runs) * factor) if factor else None
    all_volume = round(len(recent) * factor) if factor else None
    profiles = (
        ("Conservative / build-year downside", 0.4, 10, 3, 50, 30, 20, 0.3, 5, 6),
        ("Targeted pilot expansion", 0.7, 15, 2, 50, 20, 10, 0.5, 10, 8),
        ("Stretch / validated broad reuse", 0.9, 20, 2, 60, 25, 15, 0.7, 15, 10),
    )
    scenarios = []
    for name, share, manual, supervision, build, maintenance, validation, transfer_share, transfer_minutes, transfer_validation in profiles:
        assumed = {"eligible_automation_executions": auto_volume, "automation_adoption_fraction": share,
                   "manual_active_minutes": manual, "supervision_minutes": supervision,
                   "build_hours": build, "maintenance_hours": maintenance, "additional_validation_hours": validation,
                   "eligible_transfer_executions": transfer_volume, "transfer_adoption_fraction": transfer_share,
                   "per_transfer_active_minutes_saved": transfer_minutes, "transfer_validation_hours": transfer_validation}
        model = effort_scenario(execution_count=auto_volume, manual_minutes=manual, adoption_fraction=share,
                                supervision_minutes=supervision, build_hours=build,
                                maintenance_hours=maintenance, validation_hours=validation)
        transfer_net = round(transfer_volume * transfer_share * transfer_minutes / 60 - transfer_validation, 2) if transfer_volume is not None else None
        auto_net = model["net_scenario_hours_saved"]
        combined = round(auto_net + transfer_net, 2) if auto_net is not None and transfer_net is not None else None
        scenarios.append({"scenario": name, "automation_net_hours": auto_net, "rack_transfer_net_hours": transfer_net,
                          "combined_net_hours": combined,
                          "capacity_gap_coverage_percent": round(100 * combined / person_capacity_hours, 2) if combined is not None else None,
                          "remaining_capacity_gap_hours": round(max(0, person_capacity_hours - combined), 2) if combined is not None else None,
                          "is_measured_saving": False, "inputs": assumed})
    platform_counts = Counter(row.get("actual_environment", "Unknown") for row in executions)
    aidas = sorted(analysis["aida_summary"], key=lambda row: -row["executed_runs"])
    top_three = aidas[:3]
    total = len(executions)
    route_rows = []
    for row in automation["route_summary"]:
        members = [case for case in automation["cases"] if case["review_route"] == row["review_route"]]
        route_rows.append({**row, "executed_testcases": sum(case["executed_runs"] > 0 for case in members)})
    pilot_queue = []
    recent_counts = Counter(row["test_id"] for row in recent)
    for row in automation["cases"]:
        if row["test_id"] in automation_ids and recent_counts[row["test_id"]]:
            pilot_queue.append({"test_id": row["test_id"], "test_name": row["test_name"],
                                "review_route": row["review_route"], "baseline_year_executions": recent_counts[row["test_id"]],
                                "observed_on_both_platforms": row["test_id"] in both_ids,
                                "candidate_approach": row["candidate_approach"], "approval_status": "Pilot review required"})
    pilot_queue.sort(key=lambda row: (-row["baseline_year_executions"], not row["observed_on_both_platforms"], row["test_id"]))
    facts = [
        {"topic": "Vehicle workload concentration", "finding": f"{platform_counts['Vehicle']} / {total} execution records ({100 * platform_counts['Vehicle'] / total:.1f}%) were vehicle-source; rack {platform_counts['Bench']}, unknown {platform_counts['Unknown']}." if total else "No local execution records.",
         "action": "Keep real-route, acoustic and full-vehicle lifecycle checks on vehicles; review stationary deterministic regression for rack or hybrid delivery."},
        {"topic": "Repeated execution opportunity", "finding": f"{analysis['overview']['additional_execution_records']} records are beyond the first execution per case; {len(both_ids)} case IDs appear on both rack and vehicle.",
         "action": "Prioritize repeatable regression pilots. These are not proven redundant tests or same-version equivalence."},
        {"topic": "Concentrated functional investment", "finding": f"The top three observed functions account for {sum(row['executed_runs'] for row in top_three)} / {total} executions: " + "; ".join(row["aida_name"] for row in top_three) + ".",
         "action": "Start with parameterized character/input/contact-search checks and observable UI/API assertions. Split navigation search/UI from real positioning and driving checks."},
        {"topic": f"{planning_year} one-person capacity gap", "finding": f"Planning target: {person_capacity_hours:,.0f} hours for one person-year (assumption, not measured {label} availability).",
         "action": "Visible testcase automation alone is not evidence that one person can be removed. Add measured preparation, flashing, logs/reporting and cross-team reuse savings before committing coverage reductions."},
    ]
    return {"planning_year": planning_year, "person_capacity_hours_assumption": person_capacity_hours,
            "one_person_replacement_proven": False, "realized_savings_hours": None,
            "planning_baseline": {"year": baseline_year, "snapshot_cutoff": cutoff.isoformat() if cutoff else "Unavailable",
                                  "calendar_days_observed": observed_days, "calendar_days_in_year": days_in_year,
                                  "observed_executions": len(recent), "observed_automation_screened_executions": len(automation_runs),
                                  "observed_transfer_screened_executions": len(transfer_runs),
                                  "annualized_automation_volume": auto_volume, "annualized_transfer_volume": transfer_volume,
                                  "annualized_all_execution_volume": all_volume,
                                  "all_direct_execution_hours_if_15_active_minutes": round(all_volume * 15 / 60, 2) if all_volume is not None else None,
                                  "annualization_is_forecast": False},
            "automation_lane_run_ids": sorted(row["run_id"] for row in automation_runs),
            "rack_transfer_lane_run_ids": sorted(row["run_id"] for row in transfer_runs),
            "platform_execution_counts": dict(platform_counts), "cases_observed_on_both_platforms": len(both_ids),
            "facts": facts, "route_opportunities": route_rows, "pilot_queue": pilot_queue, "scenarios": scenarios,
            "actions": [
                "Pilot parameterized input/speller/contact-search regression first; confirm native steps, expected results, reset and machine oracles.",
                "Review state/account/device orchestration for semi-automation; retain human setup or visual/audio review until validated.",
                "Move only approved stationary, equivalent checks to racks. Keep a risk-based real-vehicle slice for routes, coverage transitions, cabin acoustics and integrated wake/sleep.",
                "Measure active minutes, automation supervision and build/maintenance costs in a representative pilot before using savings for next-year staffing.",
                "Do not remove coverage to fit the scenario. Escalate the remaining capacity gap through shared automation, preparation/log/report tooling and explicit scope/resource decisions.",
            ],
            "limits": [
                f"2026 local snapshot data are annualized by calendar exposure only for illustrations, not as a 2027 demand forecast; the local {cohort_label} cohort is not all {label} team work.",
                "Automation and rack-transfer lanes use disjoint 2026 run IDs. Transfer candidates require prior observations on both platforms but no same-version equivalence is proven.",
                "All adoption, active-time and cost inputs are illustrative assumptions; negative net savings are retained and no measured effort savings or feasibility approval is claimed.",
                "Shared setup minutes must be allocated once. Transfer minutes are assumed per-execution active-time reductions, not elapsed driving or waiting time.",
                f"One person-year uses an explicit 1,840-hour planning assumption; replace it with approved {label} productive capacity before staffing decisions.",
            ]}


def build_automation_analysis(payload):
    analysis = payload["hk_testcase_analysis"]
    by_case = defaultdict(list)
    for row in analysis["runs"]:
        by_case[row["test_id"]].append(row)
    cases = []
    for case in analysis["cases"]:
        titles = [case["test_name"], *case.get("title_variants", [])]
        title_text = "\n".join(titles)
        route, approach = "General feasibility review", "Review the native procedure and expected-result oracle before choosing a UI/API, device, HIL or hybrid pilot."
        for candidate_route, pattern, candidate_approach in AUTOMATION_ROUTES:
            if re.search(pattern, title_text, re.I):
                route, approach = candidate_route, candidate_approach
                break
        records = by_case[case["test_id"]]
        environments = Counter(row.get("actual_environment", "Unknown") for row in records if row["is_executed"])
        cases.append({
            "test_id": case["test_id"], "test_name": case["test_name"], "aida_names": case["aida_names"],
            "aida_codes": case["aida_codes"], "executed_runs": case["executed_runs"],
            "additional_execution_records": case["additional_execution_records"],
            "failed_runs": case["failed_runs"], "attention_runs": case["attention_runs"],
            "latest_executed_status": case["latest_executed_status"],
            "bench_source_executions": environments["Bench"], "vehicle_source_executions": environments["Vehicle"],
            "unknown_source_executions": environments["Unknown"], "actual_vehicle_motion": "Unknown",
            "review_route": route, "candidate_approach": approach, "review_status": "Unreviewed",
            "automation_feasible": None, "approved_for_full_automation": False,
            "screening_basis": "Observed title signals and selected-period execution counts only",
            "required_evidence": [row["gate"] for row in AUTOMATION_GATES],
        })
    cases.sort(key=lambda row: (-row["additional_execution_records"], -row["executed_runs"], row["test_id"]))
    for index, row in enumerate(cases, 1):
        row["review_priority_rank"] = index
    source_summary = []
    for environment in ("Bench", "Vehicle", "Unknown"):
        existing = next((row for row in payload["platform_type_summary"] if row["actual_environment"] == environment), None)
        source_summary.append({"actual_environment": environment,
                               "management_origin": {"Bench": "Static-origin", "Vehicle": "Dynamic-origin"}.get(environment, "Unknown-origin"),
                               "actual_vehicle_motion": "Unknown",
                               **({key: value for key, value in existing.items() if key != "actual_environment"} if existing else _metrics([], []))})
    route_summary = []
    for route in sorted({row["review_route"] for row in cases}):
        selected = [row for row in cases if row["review_route"] == route]
        route_summary.append({"review_route": route, "case_count": len(selected),
                              "executed_runs": sum(row["executed_runs"] for row in selected),
                              "additional_execution_records": sum(row["additional_execution_records"] for row in selected)})
    return {"scope": f"{REGION_LABELS[payload['scope'].get('region', 'HK')]} testcase pre-screening; selected-period counts are not an annualized saving forecast",
            "source_summary": source_summary, "cases": cases, "route_summary": route_summary,
            "review_gates": [dict(row) for row in AUTOMATION_GATES], "effort_scenario": effort_scenario(),
            "observed_savings_hours": None, "approved_automatable_case_count": None,
            "limitations": [
                "Dynamic-origin means recorded vehicle platform under the user's management convention; it does not prove motion. Static-origin means recorded bench platform, not a fully static stimulus.",
                "Recorded platform/VIN is not proof of first discovery. Unknown platform sources remain unknown rather than being allocated to bench or vehicle.",
                "A vehicle-origin case may be automatable in a stationary vehicle, bench, simulator or hybrid workflow; bench-origin does not establish feasibility either.",
                "Native steps, expected results, validated machine oracles and implementation/effort measurements are missing, so every feasibility decision remains unreviewed.",
                "Routes use title signals only. Rank is observed repeat volume, not engineering feasibility, homogeneous regression demand or guaranteed effort savings.",
                "Physical-context review does not mean manual-only. Replay/simulation equivalence and residual real-vehicle validation need explicit review.",
                "Savings are unmeasured. The blank what-if model requires eligible volume, active minutes, automation share, supervision, build, maintenance and additional validation inputs for one consistent horizon.",
            ]}


def unknown_platform_followup(runs):
    groups = defaultdict(list)
    for row in runs:
        if row.get("actual_environment", "Unknown") != "Unknown":
            continue
        identifier = _text(row.get("vehicle_value"))
        numbers = row.get("v_numbers", [])
        source = row.get("environment_classification_source", "")
        if not identifier:
            reason = "Native MR identifier returned empty" if row.get("native_platform_lookup") == "Fetched" else "Platform identifier not captured locally"
        elif source == "conflicting_v_number_types":
            reason = "Conflicting platform types in identifier"
        elif any(classify_v_number(number) == "Unknown" for number in numbers):
            reason = "V-number type requires user confirmation"
        else:
            reason = "VIN to V-number mapping unavailable"
        groups[(identifier or "<EMPTY>", reason)].append(row)
    result = []
    for (identifier, reason), records in groups.items():
        executed = sum(bool(row["is_executed"]) for row in records)
        planned = sum(row.get("status") == "Planned" for row in records)
        result.append({"platform_identifier": identifier, "unknown_reason": reason,
                       "v_numbers": sorted({number for row in records for number in row.get("v_numbers", [])}),
                       "run_records": len(records), "executed_runs": executed, "planned_runs": planned,
                       "other_status_records": len(records) - executed - planned,
                       "testcase_count": len({row["test_id"] for row in records}),
                       "sample_mr_ids": sorted({row["run_id"] for row in records}, key=_sortable_run_id)[:3],
                       "native_observed_at": sorted({row.get("platform_identifier_observed_at", "") for row in records if row.get("platform_identifier_observed_at")}),
                       "user_confirmed_v_number": "", "user_confirmed_platform_type": "", "user_note": ""})
    return sorted(result, key=lambda row: (-row["executed_runs"], -row["run_records"], row["platform_identifier"]))


def _known_hk_vin_matches(identifier, inventory, platforms, *, include_mixed_aliases=False):
    known_vins = [row["vin"] for row in inventory]
    matched = match_vins(identifier, known_vins)
    if matched and not include_mixed_aliases:
        return matched
    text = _text(identifier).upper()
    for platform in platforms:
        if platform["vin"] not in known_vins:
            continue
        aliases = platform.get("v_number_aliases", [platform.get("v_number", "")])
        if any(re.search(r"(?<![A-Z0-9])" + re.escape(_text(number).upper()) + r"(?![A-Z0-9])", text)
               for number in aliases if number):
            matched.append(platform["vin"])
    return list(dict.fromkeys(matched))


def _regional_vin_matches(identifier, inventory, all_inventory, platforms, region):
    matched = _known_hk_vin_matches(identifier, all_inventory, platforms, include_mixed_aliases=True)
    if region == "CN":
        excluded = {row["vin"] for row in all_inventory if row["region"] in {"HK", "TW"}}
        if excluded.intersection(matched):
            return []
    selected = {row["vin"] for row in inventory}
    return [vin for vin in matched if vin in selected]


def _rebuild_vin_scope(result, platforms):
    result.setdefault("frozen_vin_scope_overview", deepcopy(result["overview"]))
    candidate_rows = {}
    for collection in (result.get("vin_scope_candidate_runs", []), result["runs"], result["hk_testcase_analysis"]["runs"]):
        for row in collection:
            candidate_rows[row["run_id"]] = deepcopy(row)
    confirmed = {row["vin"] for row in result["inventory"] if row["evidence"] == "user_confirmed"}
    for row in candidate_rows.values():
        region = result["scope"].get("region", "HK")
        row["matched_vins"] = (_known_hk_vin_matches(row.get("vehicle_value"), result["inventory"], platforms)
                               if region == "HK" else _regional_vin_matches(row.get("vehicle_value"), result["inventory"], result["regional_inventory"], platforms, region))
        row.update(_platform_identity(row.get("vehicle_value"), platforms))
        row["inventory_basis"] = _inventory_basis(row["matched_vins"], result["inventory"])
    result["vin_scope_candidate_runs"] = sorted(candidate_rows.values(), key=lambda row: (row["date"], _sortable_run_id(row["run_id"])))
    result["runs"] = [deepcopy(row) for row in result["vin_scope_candidate_runs"] if row["matched_vins"]]
    for row in result["hk_testcase_analysis"]["runs"]:
        resolved = candidate_rows[row["run_id"]]
        row["matched_vins"] = list(resolved["matched_vins"])
    runs, defects = result["runs"], result["defects"]
    result["overview"] = _metrics(runs, defects)
    for key, evidence in (("confirmed_overview", "user_confirmed"), ("legacy_candidate_overview", "legacy_mapping_candidate")):
        result[key] = _metrics([row for row in runs if row["inventory_basis"] == evidence],
                               [row for row in defects if row["inventory_basis"] == evidence])
    result["year_summary"] = _group_summaries(runs, defects, "year")
    result["month_summary"] = _group_summaries(runs, defects, "month")
    result["confirmed_year_summary"] = _group_summaries(
        [row for row in runs if row["inventory_basis"] == "user_confirmed"],
        [row for row in defects if row["inventory_basis"] == "user_confirmed"], "year")
    groups = defaultdict(lambda: {"runs": [], "defects": []})
    for kind, records in (("runs", runs), ("defects", defects)):
        for row in records:
            groups[row["aida_key"]][kind].append(row)
    result["aida_summary"] = []
    for group in groups.values():
        records = group["runs"] + group["defects"]
        result["aida_summary"].append({
            "aida_code": records[0]["aida_code"], "aida_name": records[0]["aida_name"],
            "raw_name_variants": sorted({row["aida_raw"] for row in records if row["aida_raw"]}),
            "confirmed_defects": sum(row["inventory_basis"] == "user_confirmed" for row in group["defects"]),
            "legacy_candidate_defects": sum(row["inventory_basis"] == "legacy_mapping_candidate" for row in group["defects"]),
            **_metrics(group["runs"], group["defects"]),
        })
    result["aida_summary"].sort(key=lambda row: (-row["executed_runs"], -row["defect_count"], row["aida_name"]))
    title_ids = {row["run_id"] for row in result["hk_testcase_analysis"]["runs"]}
    overlap = [row for row in runs if row["run_id"] in title_ids]
    result["hk_testcase_analysis"]["overlap_with_vin_scope"] = {**_metrics(overlap, None), "run_ids": sorted(row["run_id"] for row in overlap)}
    result["vin_scope_rebuild"] = {
        "candidate_mr_ids": len(candidate_rows), "matched_mr_ids": len(runs),
        "executed_runs_before_native_rebuild": result["frozen_vin_scope_overview"]["executed_runs"],
        "executed_runs_after_native_rebuild": result["overview"]["executed_runs"],
        "scope": "Existing frozen report MR candidate pool only; not a full native all-platform execution census",
        "platform_temporal_basis": "Native supplement is current observed linkage, not verified historical execution-time linkage",
    }
    included = {row["run_id"] for row in runs}
    result["linked_only_runs_excluded"] = [row for row in result["linked_only_runs_excluded"] if row["run_id"] not in included]


def refresh_platform_types(payload, platforms):
    result = deepcopy(payload)
    result.setdefault("data_snapshot_generated_at", result["generated_at"])
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    result["schema_version"] = "4.0"
    result["v_number_mapping"] = deepcopy(platforms)
    result["source"].setdefault("unchanged_at_data_build", result["source"].get("unchanged"))
    result["source"]["unchanged"] = None
    result["source"]["source_read_for_classification_refresh"] = False
    result["source"]["source_modified_by_classification_refresh"] = False
    source_path = Path(result["source"]["database"])
    result["source"]["database_matches_frozen_snapshot_at_refresh"] = (
        source_path.exists() and source_path.stat().st_mtime_ns == result["source"]["mtime_ns_before"])
    result["classification_refresh"] = {
        "rule_confirmed_at": "2026-11-02", "basis": "user_confirmed_v_number_rule",
        "frozen_data_preserved": True, "source_database_queried": False,
        "mixed_letter_digit_v_numbers": "Unknown unless separately confirmed",
    }
    by_vin = {row["vin"]: row for row in platforms}
    for platform in result["inventory"]:
        mapped = by_vin.get(platform["vin"], {})
        platform.update({"v_number": mapped.get("v_number", ""),
                         "actual_environment": classify_v_number(mapped.get("v_number")),
                         "v_number_source": mapped.get("v_number_source", "unavailable")})
    analysis = result["hk_testcase_analysis"]
    for records in (result["runs"], analysis["runs"]):
        for row in records:
            row.update(_platform_identity(row.get("vehicle_value"), platforms))
    for row in result["defects"]:
        row.update(_platform_identity(row.get("vin"), platforms))
    _rebuild_vin_scope(result, platforms)
    result["environment_comparison"] = _environment_comparison(result["runs"])
    analysis["environment_comparison"] = _environment_comparison(analysis["runs"])
    result["platform_type_summary"] = _group_summaries(result["runs"], result["defects"], "actual_environment")
    result["platform_summary"] = [
        {**platform, **_metrics([row for row in result["runs"] if platform["vin"] in row["matched_vins"]],
                               [row for row in result["defects"] if platform["vin"] in row["matched_vins"]])}
        for platform in result["inventory"]
    ]
    grouped = defaultdict(lambda: {"runs": [], "defects": []})
    for kind, records in (("runs", result["runs"]), ("defects", result["defects"])):
        for row in records:
            grouped[(row["actual_environment"], row["aida_key"])][kind].append(row)
    result["platform_type_aida_summary"] = []
    for (environment, key), group in grouped.items():
        representative = (group["runs"] + group["defects"])[0]
        result["platform_type_aida_summary"].append({
            "actual_environment": environment, "aida_code": representative["aida_code"],
            "aida_name": representative["aida_name"], **_metrics(group["runs"], group["defects"]),
        })
    return result


def build_report(db_path, years=(2025, 2026), *, team_name=None, region="HK", fleet_snapshot=None, market_mapping=None):
    if region not in REGION_LABELS:
        raise ValueError("Supported regions are HK, TW and CN")
    if region != "HK" and team_name is None:
        team_name = "DTSV_China"
    source_path = Path(db_path).resolve()
    before = source_path.stat()
    all_inventory = _inventory() if region == "HK" else load_regional_inventory(fleet_snapshot, market_mapping)
    inventory = all_inventory if region == "HK" else [row for row in all_inventory if row["region"] == region]
    default_fleet = Path(__file__).resolve().parents[2] / "exports/fleem/dtsv-china-active.json"
    fleet_path = Path(fleet_snapshot) if fleet_snapshot else default_fleet
    platforms = load_v_number_mapping(fleet_path if fleet_path.exists() else None) if region != "HK" else None
    known_vins = [row["vin"] for row in inventory]
    confirmed = {row["vin"] for row in inventory if row["evidence"] == "user_confirmed"}

    def regional_matches(value):
        return match_vins(value, known_vins) if region == "HK" else _regional_vin_matches(value, inventory, all_inventory, platforms, region)

    selected_years = {int(year) for year in years}
    quality = Counter()
    quality_years = defaultdict(Counter)
    run_date_bases = Counter()
    runs, defects, unresolved_links, market_only = [], [], [], []
    source_fetched = {"runs": "", "defects": ""}
    connection = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    try:
        for row in _read_rows(connection, "octane_manual_runs", RUN_FIELDS):
            quality["source_runs_total"] += 1
            run_team, team_source = _run_team(row)
            if not _team_matches(run_team, team_name):
                quality["source_runs_outside_team_scope"] += 1
                continue
            if team_source == "raw_json.run_team_000_udf":
                quality["source_runs_native_team_recovered"] += 1
            source_fetched["runs"] = max(source_fetched["runs"], _text(row.get("fetched_at")))
            vehicle_value = _text(row.get("testplatformid"))
            vin_source = "testplatformid" if vehicle_value else "missing"
            matched = regional_matches(vehicle_value)
            raw = {}
            if not vehicle_value or matched:
                raw = _raw_payload(row.get("raw_json"))
            if not vehicle_value:
                for name in VEHICLE_KEYS:
                    candidate = _text(raw.get(name))
                    if candidate and candidate.casefold() not in {"none", "null", "n/a", "-"}:
                        vehicle_value = candidate
                        vin_source = "raw_json." + name
                        matched = regional_matches(vehicle_value)
                        break
            event_date, basis = _event_date(row, ("finished", "started", "creation_time"))
            if event_date is None or event_date.year not in selected_years:
                quality["source_runs_missing_date" if event_date is None else "source_runs_outside_calendar_scope"] += 1
                if matched:
                    quality["hk_runs_outside_calendar_scope"] += 1
                continue
            source_year = _text(row.get("year"))
            quality["source_runs_in_calendar_scope"] += 1
            year_quality = quality_years[event_date.year]
            year_quality["source_runs"] += 1
            year_quality["with_vehicle_identifier" if vehicle_value else "without_vehicle_identifier"] += 1
            if source_year and source_year != str(event_date.year):
                quality["source_run_year_label_differs_from_calendar"] += 1
            if not matched:
                quality["source_runs_non_hk_vehicle" if vehicle_value else "source_runs_missing_vehicle"] += 1
                if row.get("defect_id"):
                    unresolved_links.append({"run_id": _text(row["mr_id"]), "test_id": _text(row.get("test_id")),
                                             "defect_id": _text(row["defect_id"]), "date": event_date.isoformat(),
                                             "vehicle_value": vehicle_value, "status": _status(row.get("status"))})
                continue
            year_quality["matched_hk_run_records"] += 1
            code, name, original = _aida(row)
            status = _status(row.get("status"))
            linked = _linked_ids(row.get("defect_id")) | _linked_ids(raw.get("defect")) | _linked_ids(raw.get("linked_defects"))
            run_date_bases[basis] += 1
            runs.append({
                "run_id": _text(row["mr_id"]), "test_id": _text(row.get("test_id")),
                "test_name": _text(row.get("test_name")), "status": status,
                "is_executed": status in EXECUTED_STATUSES,
                "date": event_date.isoformat(), "date_basis": basis,
                "year": event_date.year, "source_year": source_year, "month": event_date.strftime("%Y-%m"),
                "matched_vins": matched, "vin_source": vin_source, "vehicle_value": vehicle_value,
                "inventory_basis": _inventory_basis(matched, inventory),
                "aida_code": code, "aida_name": name, "aida_raw": original,
                "aida_key": code or "NAME:" + name, "linked_defect_ids": sorted(linked),
                "project": _text(row.get("project")), "release": _text(row.get("release")),
                "software_version": _text(row.get("execution_sw_version")),
                "team": run_team, "team_source": team_source,
                **_environment_fields(row, raw),
            })
        hk_testcase_analysis = _hk_testcase_analysis(connection, selected_years, known_vins, runs, [], team_name=team_name, region=region)
        vin_history = _latest_vin_history(connection)
        quality["tickets_with_vin_history"] = len(vin_history)
        for row in _read_rows(connection, "octane_defects", DEFECT_FIELDS):
            quality["source_defects_total"] += 1
            if not _team_matches(row.get("problem_finder_team"), team_name):
                quality["source_defects_outside_team_scope"] += 1
                continue
            source_fetched["defects"] = max(source_fetched["defects"], _text(row.get("fetched_at")))
            event_date, basis = _event_date(row, ("creation_time",))
            if event_date is None or event_date.year not in selected_years:
                quality["source_defects_missing_date" if event_date is None else "source_defects_outside_calendar_scope"] += 1
                continue
            quality["source_defects_in_calendar_scope"] += 1
            current_vin = _text(row.get("vin"))
            effective_vin = current_vin
            vin_source = "vin"
            history = vin_history.get(_text(row["defect_id"]), {})
            if not current_vin:
                quality["source_defects_current_vin_missing"] += 1
                if history.get("value"):
                    effective_vin = history["value"]
                    vin_source = "history.latest_vin"
            matched = regional_matches(effective_vin)
            if not matched:
                quality["source_defects_missing_vin" if not _text(row.get("vin")) else "source_defects_non_hk_vin"] += 1
                if region in re.split(r"[;,\s]+", _text(row.get("market"))):
                    market_only.append({"defect_id": _text(row["defect_id"]), "name": _text(row.get("name")),
                                        "date": event_date.isoformat(), "vin": _text(row.get("vin")),
                                        "market": _text(row.get("market")), "reason": "market_" + region + "_without_known_regional_VIN_match"})
                continue
            if vin_source == "history.latest_vin":
                quality["hk_defects_recovered_from_vin_history"] += 1
            code, name, original = _aida(row)
            phase = _text(row.get("phase") or row.get("status_phase"))
            phase_match = re.match(r"(\d{2})", phase)
            defects.append({
                "defect_id": _text(row["defect_id"]), "name": _text(row.get("name")),
                "date": event_date.isoformat(), "date_basis": basis, "year": event_date.year,
                "month": event_date.strftime("%Y-%m"), "matched_vins": matched, "vin": effective_vin,
                "vin_current": current_vin, "vin_source": vin_source,
                "vin_history_at": history.get("timestamp", "") if vin_source == "history.latest_vin" else "",
                "inventory_basis": _inventory_basis(matched, inventory),
                "aida_code": code, "aida_name": name, "aida_raw": original, "aida_key": code or "NAME:" + name,
                "phase": phase, "phase_code": phase_match[1] if phase_match else "Unknown",
                "severity": _text(row.get("problem_severity") or row.get("severity")) or "Unknown",
                "reporting_class": _text(row.get("reporting_class")) or "Unknown",
                "detected_by": _text(row.get("detected_by")) or "Unknown",
                "finder_team": _text(row.get("problem_finder_team")) or "Unknown",
                "market_raw": _text(row.get("market")),
                "project": _text(row.get("tproject") or row.get("project")) or "Unknown",
                "assigned_ecu": _text(row.get("assigned_ecu")) or "Unknown",
                "solution_cluster": _text(row.get("solution_cluster")) or "Unknown",
                "software_version": _text(row.get("software_version")),
                **_platform_identity(effective_vin, platforms),
            })
    finally:
        connection.close()
    runs.sort(key=lambda row: (row["date"], row["run_id"]))
    defects.sort(key=lambda row: (row["date"], row["defect_id"]))
    included_defects = {row["defect_id"] for row in defects}
    title_linked_ids = {defect_id for row in hk_testcase_analysis["runs"] for defect_id in row["linked_defect_ids"]}
    hk_testcase_analysis["overview"]["linked_defects_in_vin_scope"] = len(title_linked_ids & included_defects)
    hk_testcase_analysis["overview"]["linked_defects_not_in_vin_scope"] = len(title_linked_ids - included_defects)
    linked_only = [row for row in unresolved_links if row["defect_id"] in included_defects]
    aida_summary = []
    grouped = defaultdict(lambda: {"runs": [], "defects": []})
    for kind, records in (("runs", runs), ("defects", defects)):
        for row in records:
            grouped[row["aida_key"]][kind].append(row)
    for group in grouped.values():
        records = group["runs"] + group["defects"]
        variants = sorted({_text(row["aida_raw"]) for row in records if row["aida_raw"]})
        aida_summary.append({"aida_code": records[0]["aida_code"], "aida_name": records[0]["aida_name"],
                     "raw_name_variants": variants,
                     "confirmed_defects": sum(row["inventory_basis"] == "user_confirmed" for row in group["defects"]),
                     "legacy_candidate_defects": sum(row["inventory_basis"] == "legacy_mapping_candidate" for row in group["defects"]),
                     **_metrics(group["runs"], group["defects"])})
    aida_summary.sort(key=lambda row: (-row["executed_runs"], -row["defect_count"], row["aida_name"]))
    platform_summary = []
    for platform in inventory:
        vin = platform["vin"]
        platform_summary.append({**platform, **_metrics([row for row in runs if vin in row["matched_vins"]],
                                                       [row for row in defects if vin in row["matched_vins"]])})
    after = source_path.stat()
    return {
        "schema_version": "3.0", "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {"years": sorted(selected_years), "timezone": REGION_TIMEZONES[region],
              "region": region, "region_label": REGION_LABELS[region],
                  "team_name": team_name,
                  "team_attribution": {"runs": "octane_manual_runs.team; only when empty, saved raw_json.run_team_000_udf.name", "testcases": "octane_testcases.scope_team or matching MR team evidence", "defects": "octane_defects.problem_finder_team"},
                  "vehicle_attribution": "Direct full VIN, globally bounded unique known 7-character suffix or saved V-number mapping; never linked-defect inference" if region != "HK" else "Direct full VIN or bounded unique known 7-character suffix; never linked-defect inference",
                  "mainland_exclusion": "Strict platform lane only: positive CN VIN mapping; exclude HK/TW platform associations; the primary testcase cohort is separately defined by non-HK/TW testcase evidence" if region == "CN" else None,
                  "primary_testcase_classification": hk_testcase_analysis["scope"]["classification"],
                  "run_date_priority": ["finished", "started", "creation_time"],
                  "defect_date": "creation_time", "source_sync_performed": False,
                  "source_updates_performed": False, "legacy_candidates_in_combined_totals": any(row["evidence"] == "legacy_mapping_candidate" for row in inventory)},
        "source": {"database": str(source_path), "size_bytes": before.st_size,
                   "mtime_ns_before": before.st_mtime_ns, "mtime_ns_after": after.st_mtime_ns,
                   "unchanged": (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
                   "latest_fetched_at": source_fetched},
        "inventory": inventory, "regional_inventory": all_inventory if region != "HK" else [], "overview": _metrics(runs, defects),
        "platform_type_summary": _group_summaries(runs, defects, "actual_environment"),
        "environment_comparison": _environment_comparison(runs),
        "hk_testcase_analysis": hk_testcase_analysis,
        "confirmed_overview": _metrics([row for row in runs if row["inventory_basis"] == "user_confirmed"],
                                        [row for row in defects if row["inventory_basis"] == "user_confirmed"]),
        "legacy_candidate_overview": _metrics([row for row in runs if row["inventory_basis"] == "legacy_mapping_candidate"],
                                               [row for row in defects if row["inventory_basis"] == "legacy_mapping_candidate"]),
        "quality": {**dict(quality), "run_date_bases": dict(run_date_bases),
                    "by_year": [{"year": year, **dict(values)} for year, values in sorted(quality_years.items())]},
        "year_summary": _group_summaries(runs, defects, "year"),
        "confirmed_year_summary": _group_summaries(
            [row for row in runs if row["inventory_basis"] == "user_confirmed"],
            [row for row in defects if row["inventory_basis"] == "user_confirmed"], "year"),
        "month_summary": _group_summaries(runs, defects, "month"),
        "aida_summary": aida_summary, "platform_summary": platform_summary,
        "runs": runs, "defects": defects, "linked_only_runs_excluded": linked_only,
        "legacy_market_only_defects_excluded": market_only,
        "defect_distributions": {key: dict(Counter(row[key] for row in defects).most_common())
                                 for key in ("phase", "severity", "reporting_class", "detected_by", "project", "finder_team")},
        "limitations": [
            "Regions use repository VIN-market mappings and the saved fleet snapshot; explicit user HK/TW confirmations take priority without changing the source database or mapping asset.",
            "The 2026-11-09 TW screenshot confirms ten platform VINs, not historical ownership, V-numbers or moving-vehicle execution.",
            "Other TW/HK mappings are labelled legacy candidates; mainland requires positive CN mapping and excludes HK/TW associations, including mixed-region VIN records.",
            "Unknown or other-country platform records stay outside strict TW-VIN totals; TW-title testcase MRs remain in their independent intent cohort." if region == "TW" else "The primary CN report is the requested DTSV China non-HK/TW testcase workload, not a complete physical-mainland census. Unmarked and missing-platform MRs remain visible without inventing CN platform ownership.",
            "Runs require exact team evidence and defects require exact finder-team evidence; ALL catalog rows require a matching team-run testcase ID.",
            "Platform types still use the confirmed V-number rule; missing or ambiguous V-numbers remain Unknown.",
            "Calendar events use finished, started, then labelled creation-time fallback; status totals are not person-hours.",
            "Defects are unique IDs within each report; multi-VIN regional reports may overlap and their totals must not be added blindly.",
            "Current phase 06/09 is not proof of verified repair, issue quality or first discovery; linked defects do not assign an MR platform.",
            "Only locally stored evidence is analysed; no Octane/Fleem synchronization, login or write-back was performed, and 2026 is incomplete.",
        ] if region != "HK" else [
            "Known VIN inventory is not proof of a complete historical fleet; four legacy mapping candidates are labelled separately.",
            "No dated ownership history is available; VIN attribution is not proof of physical testing in Hong Kong at the event time.",
            "Source runs without a direct vehicle identifier cannot be assigned to HK, including passed runs; linked defects do not fill that gap.",
            "Execution date uses finished then started; creation-time fallback is explicitly labelled and is not measured execution timing.",
            "Passed, Failed and Requires Attention are executed-status records; Planned and other statuses are separate. No counts imply person-hours.",
            "Pass rate is Passed / (Passed + Failed), excluding attention, planned and unknown statuses.",
            "AIDA names with a single matching business code are grouped; raw variants remain available. No complete requirement denominator is available.",
            "Defects are unique IDs; multi-VIN tickets appear in each vehicle row but only once in overall and AIDA counts.",
            "Current phase 06/09 is a lifecycle status, not proof of validated repair, valid/invalid issue quality or independent root cause.",
            "Execution-linked defects are associations, not proof of first discovery or a defect-per-execution productivity rate.",
            "Blank defect VINs may use the latest recorded VIN history event; the source and timestamp remain explicit. A later clear is never undone and a nonempty current VIN is never overwritten.",
            "Only locally stored evidence is analysed; no Octane or Fleem synchronization was performed, and 2026 is incomplete.",
        ],
    }


def _testcase_rows(payload):
    groups = defaultdict(list)
    for row in payload["runs"]:
        if row["test_id"]:
            groups[row["test_id"]].append(row)
    result = []
    for test_id, runs in sorted(groups.items()):
        latest_by_vin = {}
        for row in sorted(runs, key=lambda item: (item["date"], item["run_id"])):
            for vin in row["matched_vins"]:
                latest_by_vin[vin] = row["status"]
        result.append({"test_id": test_id, "test_name": runs[-1]["test_name"],
                       "aida_names": sorted({row["aida_name"] for row in runs}),
                       "aida_codes": sorted({row["aida_code"] for row in runs if row["aida_code"]}),
                       "matched_vins": sorted({vin for row in runs for vin in row["matched_vins"]}),
                       "latest_status_by_vin": latest_by_vin, **_metrics(runs, [])})
    return result


def _flat(value):
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value if value is not None else ""


def _spreadsheet_safe(value):
    value = _flat(value)
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _regional_markdown(payload):
    region = payload["scope"]["region"]
    label = REGION_LABELS[region]
    basename = region + "_Test_Output_2025_2026"
    strict_overview = payload["overview"]
    inventory = payload["inventory"]
    analysis = payload["hk_testcase_analysis"]
    overview = payload["report_views"]["hk_overview"]
    annual_rows = _group_summaries(analysis["runs"], payload["defects"], "year")
    representatives = {row["aida_key"]: row for row in analysis["runs"] + payload["defects"]}
    aida_rows = [{**row, "aida_code": representatives[row["aida_key"]]["aida_code"],
                 "aida_name": representatives[row["aida_key"]]["aida_name"]}
                for row in _group_summaries(analysis["runs"], payload["defects"], "aida_key")]
    aida_rows.sort(key=lambda row: (-row["executed_runs"], -row["defect_count"], row["aida_name"]))
    source = payload["source"]
    lines = [
        f"# {label} Test Output Analysis: 2025-2026", "", "## Decision Summary", "",
        f"- Team: **{payload['scope']['team_name']}**. Runs use the local team column, falling back only when empty to saved native run_team_000_udf evidence; defects use problem-finder team.",
        f"- **{overview['executed_runs']} executed-status records**, **{overview['executed_testcases']} unique executed testcase IDs**, **{overview['defect_count']} unique platform defects**.",
        f"- All status records: {overview['run_records']}; Passed: {overview['passed_runs']}; Failed: {overview['failed_runs']}; Requires Attention: {overview['attention_runs']}; Planned: {overview['planned_runs']}; other states: {overview['other_status_runs']}.",
        f"- Inventory: {len(inventory)} unique VINs; {sum(row['evidence'] == 'user_confirmed' for row in inventory)} user-confirmed, {sum(row['evidence'] == 'regional_mapping' for row in inventory)} positive CN mappings, {sum(row['evidence'] == 'legacy_mapping_candidate' for row in inventory)} separately labelled legacy candidates.",
        "- TW/Taiwan catalog and run titles select testcase IDs, then their DTSV China MRs are joined by test_id, including unmarked run titles and missing local platform identifiers." if region == "TW" else "- DTSV China supported testcase IDs exclude HK/Hong Kong and TW/Taiwan catalog or team-run title evidence, then their MRs are joined by test_id even with unmarked run titles or missing platform identifiers.",
        "- Testcase execution and platform-defect counts are separate evidence sets, not first-discovery or defect-per-run productivity measures.",
    ]
    if region == "TW":
        confirmed = payload["confirmed_overview"]
        lines += [f"- TW-title testcase cohort: **{len(analysis['cases'])} distinct testcase IDs**, **{analysis['overview']['run_records']} related MR records**, **{analysis['overview']['executed_runs']} executed-status records**.",
                  f"- The strict TW-platform subset is separate: {strict_overview['executed_runs']} executed records and {strict_overview['executed_testcases']} unique executed testcases. It is not the full TW-title testcase cohort.",
                  "- Cases without local period execution remain in the catalog. A title-only MR does not prove execution on a TW platform, and the two cohorts must not be added without ID deduplication.",
                  "- The ten TW VINs follow the user-confirmed 2026-11-09 Fleem screenshot; it does not confirm V-numbers or historical ownership.",
                  f"- Confirmed-platform subset: {confirmed['executed_runs']} executed records, {confirmed['executed_testcases']} unique executed testcases and {confirmed['defect_count']} unique defects. Legacy candidates remain separate in inventory evidence."]
    else:
        explicit_cn = sum(row.get("testcase_scope_basis") == "explicit_cn_title" for row in analysis["cases"])
        lines += [f"- Non-HK/TW testcase cohort: **{len(analysis['cases'])} distinct testcase IDs**, **{analysis['overview']['run_records']} related MR records**, **{analysis['overview']['executed_runs']} executed-status records**.",
                  f"- The strict CN-platform subset is separate: {strict_overview['executed_runs']} executed records and {strict_overview['executed_testcases']} unique executed testcases; it is not the full non-HK/TW testcase cohort.",
                  f"- testcase_scope_basis separates {explicit_cn} explicit_cn_title cases from {len(analysis['cases']) - explicit_cn} team_non_hk_tw_unmarked cases. Unmarked titles do not establish CN-only applicability or physical mainland location.",
                  f"- {len(analysis.get('excluded_regional_testcase_ids', []))} HK/TW testcase IDs are excluded, including their unmarked repeat MRs. Cases without period execution remain visible.",
                  "- The primary scope is the requested DTSV China non-HK/TW testing workload, not a physical-mainland execution census; unknown platforms are not silently assigned to a CN VIN.",
                  "- Strict mainland platform counts still require positive CN VIN mapping and exclude HK/TW associations, including mixed-region records; market text or linked defects do not fill a missing MR platform."]
    lines += ["", "## Evidence and Dates", "",
              f"- Source database: {source['database']}.",
              f"- Latest local run fetch: {source['latest_fetched_at']['runs']}.",
              f"- Latest local defect fetch: {source['latest_fetched_at']['defects']}.",
              f"- Execution interval: {overview['first_execution_date']} to {overview['last_execution_date']}.",
              f"- Defect interval: {overview['first_defect_date']} to {overview['last_defect_date']}.",
              f"- Calendar timezone: {payload['scope']['timezone']}; finished, started, then explicitly labelled creation-time fallback.",
              "- The source database was read-only. No Octane/Fleem synchronization, new login, source mutation or write-back occurred. 2026 is incomplete.",
              "", "## Annual Results", "",
              "| Year | All run records | Executed records | Unique executed testcases | Passed | Failed | Attention | Planned | Defects |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in annual_rows:
        lines.append(f"| {row['year']} | {row['run_records']} | {row['executed_runs']} | {row['executed_testcases']} | {row['passed_runs']} | {row['failed_runs']} | {row['attention_runs']} | {row['planned_runs']} | {row['defect_count']} |")
    lines += ["", "## Platform Evidence", "",
              "| VIN suffix | Model | Region source | Inventory evidence | V-number | Platform type | Executed records | Defects |",
              "| --- | --- | --- | --- | --- | --- | ---: | ---: |"]
    for row in payload["platform_summary"]:
        lines.append(f"| {row['short_vin']} | {row['model']} | {_flat(row.get('region_source'))} | {row['evidence']} | {row.get('v_number', '')} | {row.get('actual_environment', 'Unknown')} | {row['executed_runs']} | {row['defect_count']} |")
    lines += ["", "## AIDA Evidence", "",
              "| AIDA code | Function | Executed records | Unique executed testcases | Defects |",
              "| --- | --- | ---: | ---: | ---: |"]
    for row in aida_rows[:25]:
        lines.append(f"| {row['aida_code']} | {row['aida_name']} | {row['executed_runs']} | {row['executed_testcases']} | {row['defect_count']} |")
    lines += ["", "## Rack vs Vehicle", "",
              "| Platform source | Executed records | Unique executed testcases | Defects |",
              "| --- | ---: | ---: | ---: |"]
    for row in payload["report_views"]["platform_comparison"]["summary"]:
        lines.append(f"| {row['display_name']} | {row['executed_runs']} | {row['executed_testcases']} | {row['defect_count']} |")
    comparison = payload["report_views"]["platform_comparison"]
    lines += [f"- Unclassified platform type: {comparison['unclassified_executed_runs']} executed records and {comparison['unclassified_defects']} defects; these remain in overall totals.",
              "- Rack/vehicle are confirmed V-number source conventions, not measured motion or a controlled testcase/software equivalence comparison.",
              "", "## Automation and Capacity", "",
              f"- {len(payload['automation_analysis']['cases'])} testcase pilot-review candidates; none are domain-approved or certified automatable by this report.",
              "- The HTML and Excel retain the observed repeat-volume pilot queue and explicitly hypothetical 2027 capacity scenarios. Unmeasured effort is not realized saving or staffing replacement.",
              "", "## Counting Boundaries", ""]
    lines += ["- " + value for value in payload["limitations"]]
    lines += [f"- Team-excluded source records: {payload['quality'].get('source_runs_outside_team_scope', 0)} MRs and {payload['quality'].get('source_defects_outside_team_scope', 0)} defects, including missing team evidence.",
              f"- Matching market text without eligible platform attribution: {len(payload['legacy_market_only_defects_excluded'])} separately exported defects.",
              "- HK-named JSON analysis keys are retained for backward compatibility; the testcase scope explicitly uses " + analysis["scope"]["classification"] + ".",
              "", "## Artifacts", "",
              f"- [Interactive HTML]({basename}.html)", f"- [Excel workbook]({basename}.xlsx)",
              f"- [Machine-readable report]({basename}.json)",
              f"- [Regional testcase catalog]({region.lower()}_regional_testcases.csv)",
              f"- [Regional execution records]({region.lower()}_regional_executions.csv)",
              "- [Platform defects](defects.csv)", "- [Platform inventory](platforms.csv)",
              "- [Unknown platform types](unknown_platform_followup.csv)", ""]
    return "\n".join(lines)


def _markdown(payload):
    if payload["scope"].get("region", "HK") != "HK":
        return _regional_markdown(payload)
    overview = payload["overview"]
    confirmed = payload["confirmed_overview"]
    lines = [
        "# HK Test Output Analysis: 2025-2026", "",
        "## Decision Summary", "",
        "This is a local evidence report, not a complete historical execution census.", "",
        f"- Screenshot-confirmed six vehicles: **{confirmed['defect_count']} unique defects**, "
        f"**{confirmed['executed_runs']} attributable executed-status records**, "
        f"**{confirmed['executed_testcases']} unique executed testcases**.",
        f"- Including four separately labelled legacy candidates: **{overview['defect_count']} unique defects**.",
        f"- HY52877 is included as HK by explicit user confirmation; the source market field is not changed.",
        "- Zero attributed executions does not mean zero testing. Missing vehicle fields prevent assignment.",
        "- 2026 is incomplete; annual counts are not a like-for-like productivity comparison.", "",
        "## Evidence and Dates", "",
        f"- Source database: `{payload['source']['database']}`.",
        f"- Latest local run fetch: {payload['source']['latest_fetched_at']['runs']}.",
        f"- Latest local defect fetch: {payload['source']['latest_fetched_at']['defects']}.",
        "- V-number classification was refreshed using the 2026-11-02 user rule; the original local evidence cutoff is unchanged.",
        f"- Live database matches the frozen data snapshot at classification refresh: {payload['source'].get('database_matches_frozen_snapshot_at_refresh', 'not checked')}. Classification refresh does not query the live database.",
        f"- Actual attributable execution interval: {overview['first_execution_date']} to {overview['last_execution_date']}.",
        f"- Actual matched defect interval: {overview['first_defect_date']} to {overview['last_defect_date']}.",
        "- Calendar timezone: Asia/Hong_Kong. Runs use finished, then started, then labelled creation-time fallback.",
        "- A release/ingest year label is not an execution calendar year.", "",
        "## Annual Results", "",
        "| Year | Attributed execution records | Unique executed testcases | Defects incl. candidates | Defects on confirmed six |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    confirmed_years = {row["year"]: row for row in payload["confirmed_year_summary"]}
    for row in payload["year_summary"]:
        lines.append(f"| {row['year']} | {row['executed_runs']} | {row['executed_testcases']} | {row['defect_count']} | "
                     f"{confirmed_years.get(row['year'], {}).get('defect_count', 0)} |")
    lines += ["", "## Vehicle Evidence", "",
              "| VIN suffix | Model | Inventory evidence | Executed records | Defects |", "| --- | --- | --- | ---: | ---: |"]
    for row in payload["platform_summary"]:
        lines.append(f"| {row['short_vin']} | {row['model']} | {row['evidence']} | {row['executed_runs']} | {row['defect_count']} |")
    lines += ["", "## AIDA Function Evidence", "",
              "Defect-only AIDA entries indicate issue evidence, not proof of complete testcase execution coverage.", "",
              "| AIDA code | Function | Executed records | Executed testcases | Defects | Confirmed-six defects |",
              "| --- | --- | ---: | ---: | ---: | ---: |"]
    for row in sorted(payload["aida_summary"], key=lambda item: (-item["defect_count"], -item["executed_runs"])):
        name = row["aida_name"].replace("|", "/").replace("\n", " ")
        lines.append(f"| {row['aida_code']} | {name} | {row['executed_runs']} | {row['executed_testcases']} | "
                     f"{row['defect_count']} | {row['confirmed_defects']} |")
    analysis = payload.get("hk_testcase_analysis")
    if analysis:
        summary = analysis["overview"]
        lines += ["", "## HK-Marked Testcase Analysis", "",
                  "This independent cohort uses explicit HK / Hong Kong title evidence from catalog or run snapshots. It does not prove execution on an HK vehicle.", "",
                  f"- Local HK-marked cohort: {summary['title_marked_testcases']} distinct testcase IDs; {summary['catalog_backed_testcases']} have catalog-title evidence and {summary['run_title_only_testcases']} appear only in run-title evidence.",
                  "- All candidates are retained, including cases without local execution records in the selected years.",
                  f"- Calendar-period run records: {summary['run_records']}; executed-status records: {summary['executed_runs']}; Planned: {summary['planned_runs']}.",
                  f"- Distinct executed testcases across both years: {summary['executed_testcases']}; do not add annual testcase counts without deduplication.",
                  f"- Cases without period records: {summary['cases_without_period_records']}; cases without period execution: {summary['cases_without_period_execution']}.",
                  f"- Run-ID overlap with VIN-attributed evidence: {analysis['overlap_with_vin_scope']['run_records']} records / {analysis['overlap_with_vin_scope']['executed_runs']} executed records. The two cohorts must not be added directly.", "",
                  "| Year | Unique executed testcases | Executed records | Passed | Failed | Attention | Planned | Pass rate P/(P+F) |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for row in analysis["year_summary"]:
            lines.append(f"| {row['year']} | {row['executed_testcases']} | {row['executed_runs']} | {row['passed_runs']} | {row['failed_runs']} | {row['attention_runs']} | {row['planned_runs']} | {row['pass_rate']} |")
        lines += ["", "### HK Case AIDA Execution", "",
                  "| AIDA code | Function | Executed testcases | Executed records | Failed | Attention |",
                  "| --- | --- | ---: | ---: | ---: | ---: |"]
        for row in analysis["aida_summary"]:
            name = row["aida_name"].replace("|", "/").replace("\n", " ")
            lines.append(f"| {row['aida_code']} | {name} | {row['executed_testcases']} | {row['executed_runs']} | {row['failed_runs']} | {row['attention_runs']} |")
        lines += ["", "### Latest Observed Case Outcomes", "",
                  "Latest executed outcome is across the selected local period, not release-specific certification.", ""]
        lines += [f"- {status}: {count}" for status, count in analysis["latest_executed_status_counts"].items()]
        lines += ["", f"- Cases with at least one failed execution: {summary['cases_ever_failed']}.",
                  f"- Cases with at least one Requires Attention result: {summary['cases_ever_requires_attention']}.",
                  f"- Additional execution records beyond the first per case: {summary['additional_execution_records']}; these are not automatically defect retests.",
                  f"- Linked unique defect IDs: {summary['linked_defects']}; {summary['linked_defects_in_vin_scope']} are also in the VIN defect cohort. Links are not proof of initial discovery.", "",
                  "- [Full HK-marked testcase catalog](hk_marked_testcases.csv)",
                  "- [All HK-marked testcase execution records](hk_marked_executions.csv)",
                  "- [HK testcase annual summary](hk_case_annual.csv)",
                  "- [HK testcase monthly summary](hk_case_monthly.csv)",
                  "- [HK testcase AIDA summary](hk_case_aida.csv)",
                  "- [Latest testcase status distribution](hk_case_results.csv)", ""]
        lines += ["- " + value for value in analysis["limitations"]]
        comparison = analysis["environment_comparison"]
        lines += ["", "## Bench / Vehicle Comparison", "",
                  "User-confirmed V-number rule (2026-11-02): named platform numbers such as CHUXIN/HULUWA mean Bench; numeric numbers such as 553620 mean Vehicle.",
                  "Classification follows a resolved VIN-to-V-number mapping or a known V-number alias. Unknown and conflicting identifiers remain separate.", "",
                  "| Platform type by V-number | Records | Executed | Testcases | Passed | Failed | Attention | Planned |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for row in comparison["actual_summary"]:
            lines.append(f"| {row['actual_environment']} | {row['run_records']} | {row['executed_runs']} | {row['executed_testcases']} | {row['passed_runs']} | {row['failed_runs']} | {row['attention_runs']} | {row['planned_runs']} |")
        lines += ["", "### VIN-Attributed Defects by Platform Type", "",
                  "These are distinct linked-to-platform defect IDs, not issue-per-hour productivity or proof of first discovery on that platform.", "",
                  "| Platform type | VIN-attributed execution records | Unique defects | Current phase 06 | Current phase 09 | Other phases |",
                  "| --- | ---: | ---: | ---: | ---: | ---: |"]
        for row in payload["platform_type_summary"]:
            lines.append(f"| {row['actual_environment']} | {row['executed_runs']} | {row['defect_count']} | {row['phase06_defects']} | {row['phase09_defects']} | {row['other_phase_defects']} |")
        lines += ["", "### Original Platform Taxonomy Evidence", "",
                  "The table below compares observed taxonomy signals, not verified physical execution environments.", "",
                  "| Observed platform taxonomy | Records | Executed | Testcases | Passed | Failed | Attention | Planned |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for row in comparison["signal_summary"]:
            lines.append(f"| {row['environment_tag_signal']} | {row['run_records']} | {row['executed_runs']} | {row['executed_testcases']} | {row['passed_runs']} | {row['failed_runs']} | {row['attention_runs']} | {row['planned_runs']} |")
        lines += ["", "Known-sample platform counts use the confirmed V-number rule; zero classified samples does not mean no testing occurred on that type. Same-case/software outcome comparisons remain unassessed.",
              "A dual-tagged record is not counted twice. Taxonomy, VIN format, model, Manual Runner and ECU configuration do not replace the V-number mapping.",
              "The mapping is applied retrospectively; platform-type changes at each historical event are not available.", "",
                  "### Capability and Test Allocation Guidance", "",
                  "This is conditional engineering guidance, not measured findings or proof that the local tests used either environment.", "",
                  "| Dimension | Bench contribution | Vehicle contribution | Evidence needed for comparison |",
                  "| --- | --- | --- | --- |"]
        for row in comparison["capability_guidance"]:
            lines.append("| " + " | ".join(row[key].replace("|", "/") for key in ("dimension", "bench", "vehicle", "evidence_needed")) + " |")
        lines += ["", "### Missing Evidence for a Complete Comparison", ""]
        lines += ["- " + value for value in comparison["required_evidence"]]
        lines += ["", "- [Per-execution environment evidence](environment_evidence.csv)",
                  "- [Observed platform tag comparison](environment_signals.csv)",
                  "- [AIDA and platform tag matrix](environment_aida.csv)",
                  "- [Classified execution samples](environment_classified.csv)",
                  "- [Platform-type defect totals](platform_type_totals.csv)",
                  "- [Platform-type AIDA totals](platform_type_aida.csv)",
                  "- [VIN-to-V-number mapping](v_number_mapping.csv)",
                  "- [Capability guidance](platform_capabilities.csv)", ""]
        lines += ["- " + value for value in comparison["limitations"]]
    automation = payload.get("automation_analysis")
    if automation:
        lines += ["", "## Automation Opportunity Review", "",
                  "The purpose is to review transferable manual work and pilot automation, not to infer feasibility from platform type.",
                  "Static-origin = recorded bench platform; Dynamic-origin = recorded vehicle platform. Actual motion remains Unknown unless directly evidenced.", "",
                  "| Recorded platform | Management origin | VIN defects | Actual motion |",
                  "| --- | --- | ---: | --- |"]
        for row in automation["source_summary"]:
            lines.append(f"| {row['actual_environment']} | {row['management_origin']} | {row['defect_count']} | {row['actual_vehicle_motion']} |")
        lines += ["", "### Title-Based Review Routes", "",
                  "These routes are unreviewed title signals. Priority is observed repeated execution volume, not an automation approval or savings forecast.", "",
                  "| Review route | Case IDs | Executed records | Additional records after first per case |",
                  "| --- | ---: | ---: | ---: |"]
        for row in automation["route_summary"]:
            lines.append(f"| {row['review_route']} | {row['case_count']} | {row['executed_runs']} | {row['additional_execution_records']} |")
        lines += ["", "### Review Gates", ""]
        lines += ["- " + row["gate"] + ": " + row["evidence_needed"] for row in automation["review_gates"]]
        lines += ["", "### Effort Scenario", "",
                  "No observed savings or approved automatable case count is available. Model inputs are intentionally blank.",
                  "For one consistent horizon, manual baseline hours = N * manual active minutes / 60.",
                  "Automation scenario hours = N * ((1 - adoption share) * manual minutes + adoption share * supervision minutes) / 60 + build hours + maintenance hours + additional validation hours.",
                  "Hypothetical net hours saved = manual baseline minus automation scenario. Negative savings remain visible; fixed/shared costs are counted once.",
                  "N must be an eligible, version/configuration-reviewed execution volume; the two-year historical total is not an annual forecast or automatically eligible demand.",
                  "Browser what-if inputs do not alter the frozen Excel/JSON evidence. Pilot measurements and owner approval are required before claiming savings.", "",
                  "- [Prioritized review candidates](automation_candidates.csv)",
                  "- [Review routes](automation_routes.csv)",
                  "- [Evidence gates](automation_gates.csv)",
                  "- [Source management labels](automation_sources.csv)",
                  "- [Blank effort-model assumptions](effort_scenario.csv)", ""]
        lines += ["- " + value for value in automation["limitations"]]
    capture = payload.get("mr_platform_capture_audit")
    if capture:
        lines += ["", "## Manual Run Platform Capture", "",
                  "Platform attribution belongs to each manual run (MR), not the reusable testcase definition.",
                  "The verified legacy field is testplatformid_udf; other native VIN/platform relation fields are not yet verified.",
                  "Missing local capture is not evidence that the Octane MR lacks a vehicle/platform link. Native lookup status and observation time are recorded separately.", "",
                  "| Calendar year | MR platform metadata evidence state | Records |", "| --- | --- | ---: |"]
        lines += [f"| {row['year']} | {row['capture_status']} | {row['run_records']} |" for row in capture["summary"]]
        lines += ["", "- [MR platform capture audit](mr_platform_capture.csv)", ""]
        lines += ["- " + value for value in capture["findings"]]
    if payload.get("native_mr_platform_enrichment"):
        supplement = payload["native_mr_platform_enrichment"]
        lines += ["", "### Native MR Platform Supplement", "",
                  f"Native MR metadata observed at {supplement['fetched_at']}: {supplement['native_records_read']} / {supplement['report_mr_ids']} report MR IDs read.",
                  "Native Test platform ID contains VIN/platform identifiers; Equipment is retained as a distinct relation.",
                  "Frozen execution results, dates, testcase/defect sets and the SQLite source remain unchanged. Current platform links are not retroactively certified historical links.", ""]
    insights = payload.get("capacity_insights")
    if insights:
        baseline = insights["planning_baseline"]
        lines += ["", "## HK Testing Insights and 2027 Capacity", "",
                  "Decision: prioritize repeatable regression automation and validated rack transfer, but do not assume the visible testcase work can replace one tester.", ""]
        for row in insights["facts"]:
            lines += ["### " + row["topic"], "", row["finding"], row["action"], ""]
        lines += ["### 2026 Evidence Baseline and 2027 Illustrative Scenarios", "",
                  f"Local run snapshot: {baseline['snapshot_cutoff']}; {baseline['observed_executions']} executed records in {baseline['year']} over {baseline['calendar_days_observed']} calendar days.",
                  f"Automation-screened records: {baseline['observed_automation_screened_executions']}; non-overlapping vehicle-to-rack review records: {baseline['observed_transfer_screened_executions']}.",
                  f"Illustrative annual volumes: {baseline['annualized_automation_volume']} automation / {baseline['annualized_transfer_volume']} transfer. Calendar scaling is not a forecast or eligibility approval.",
                  f"One-person capacity assumption: {insights['person_capacity_hours_assumption']} hours/year. At 15 assumed active minutes each, all visible annualized executions represent {baseline['all_direct_execution_hours_if_15_active_minutes']} gross hours before any automation investment.", "",
                  "| Illustrative scenario | Automation net hours | Rack transfer net hours | Combined net hours | One-person gap coverage % | Remaining gap hours |",
                  "| --- | ---: | ---: | ---: | ---: | ---: |"]
        for row in insights["scenarios"]:
            lines.append("| " + " | ".join(str(row[key]) if row[key] is not None else "Not assessed" for key in (
                "scenario", "automation_net_hours", "rack_transfer_net_hours", "combined_net_hours", "capacity_gap_coverage_percent", "remaining_capacity_gap_hours")) + " |")
        lines += ["", "All scenario inputs are assumptions, not measured savings. Fixed costs are counted once and negative savings are retained.", ""]
        for row in insights["scenarios"]:
            lines.append("- " + row["scenario"] + ": " + json.dumps(row["inputs"], ensure_ascii=True))
        lines += ["", "### Recommended Capacity Actions", ""]
        lines += ["- " + value for value in insights["actions"]]
        lines += ["", "- [Capacity scenarios](hk_capacity_scenarios.csv)",
                  "- [Prioritized pilot queue](hk_capacity_pilot_queue.csv)", ""]
        lines += ["- " + value for value in insights["limits"]]
    lines += ["", "## Defect Findings", ""]
    for field in ("phase", "severity", "reporting_class", "detected_by", "project"):
        lines.append("- " + field + ": " + "; ".join(f"{name}: {count}" for name, count in payload["defect_distributions"][field].items()))
    lines += ["", "Current phase 06 is not proof of verified repair; phase 09 is not proof that the report was invalid.",
              "Discovery method is not inferred from execution links or lifecycle status.", "", "## Execution Data Quality", "",
              "| Calendar year | Source run records | Vehicle identifier present | Vehicle identifier missing | Matched HK records |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for row in payload["quality"]["by_year"]:
        lines.append(f"| {row['year']} | {row['source_runs']} | {row.get('with_vehicle_identifier', 0)} | "
                     f"{row.get('without_vehicle_identifier', 0)} | {row.get('matched_hk_run_records', 0)} |")
    lines += ["", f"- HK-VIN records excluded because their actual dates were outside 2025-2026: {payload['quality'].get('hk_runs_outside_calendar_scope', 0)}.",
              f"- Runs linked to matched HK defects but without matching execution VIN, excluded from HK execution counts: {len(payload['linked_only_runs_excluded'])}.",
              f"- Old HK-market defects without a known HK VIN match, excluded pending review: {len(payload['legacy_market_only_defects_excluded'])}.",
              "- Raw JSON vehicle fields were checked as a fallback; a linked defect's VIN was never substituted.",
              f"- Defects recovered using a non-cleared latest VIN history event: {payload['quality'].get('hk_defects_recovered_from_vin_history', 0)}; each row retains its VIN source and history timestamp.",
              "- Missing execution vehicle data prevents any defensible defect-per-run or person-hour productivity calculation.", "",
              "## Supplemental Read Status", "",
              json.dumps(payload.get("supplemental_read", {"status": "local_only"}), ensure_ascii=True), "",
              "No new login, upstream write, database synchronization or source update was performed.", "",
              "## Deliverables", "",
              "- [Interactive HTML](HK_Test_Output_2025_2026.html)",
              "- [Excel workbook](HK_Test_Output_2025_2026.xlsx)",
              "- [Machine-readable report](HK_Test_Output_2025_2026.json)",
              "- [Execution details](executions.csv)", "- [Defect details](defects.csv)",
              "- [Testcase inventory](testcases.csv)", "- [AIDA summary](aida_summary.csv)",
              "- [Platform summary](platforms.csv)", "- [Linked-only execution audit](linked_only_runs_excluded.csv)",
              "- [Legacy market-only defect audit](legacy_market_only_defects_excluded.csv)", "",
              "## Limits", ""]
    lines += ["- " + value for value in payload["limitations"]]
    rebuild = payload.get("vin_scope_rebuild")
    if rebuild:
        lines += ["", "## Corrected VIN Execution Scope", "",
                  f"The original frozen VIN execution count was {rebuild['executed_runs_before_native_rebuild']}; it is now {rebuild['executed_runs_after_native_rebuild']} after rebuilding membership from the supplemented MR identifiers.",
                  f"Candidate MR IDs: {rebuild['candidate_mr_ids']}; matched MR records: {rebuild['matched_mr_ids']}.",
                  rebuild["scope"], rebuild["platform_temporal_basis"], ""]
    followup = payload.get("unknown_platform_followup", [])
    if followup:
        lines += ["", "## Unknown Platform Follow-up", "",
                  "Scope: HK-marked testcase MRs. These unresolved platform types are separate from membership in the known HK VIN inventory.",
                  "No unknown type is inferred from VIN format, model, taxonomy or current failure status. User confirmation is pending.", "",
                  "| Platform identifier | Reason | Records | Executed | Planned | Example MR IDs |",
                  "| --- | --- | ---: | ---: | ---: | --- |"]
        for row in followup:
            identifier = row["platform_identifier"].replace("|", "/")
            lines.append(f"| {identifier} | {row['unknown_reason']} | {row['run_records']} | {row['executed_runs']} | {row['planned_runs']} | {'; '.join(row['sample_mr_ids'])} |")
        lines += ["", "- [Platform mapping questions and editable confirmation columns](unknown_platform_followup.csv)",
                  "- Native empty identifiers need the MR page/field or alternative relationship identified; an empty current field does not prove no historical link existed.", ""]
    return "\n".join(lines) + "\n"


HTML_TEMPLATE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HK Test Output | 2025-2026</title>
<style>
:root{--ink:#172b2a;--muted:#596761;--teal:#176c63;--red:#b64038;--amber:#9a6514;--line:#d7dfdc;--paper:#fff;--wash:#f3f6f4}
*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;color:var(--ink);background:var(--wash);font:14px/1.5 "Bahnschrift","Trebuchet MS",sans-serif;letter-spacing:0}
header{background:var(--paper);border-top:5px solid var(--teal);border-bottom:1px solid var(--line);padding:24px max(20px,calc((100% - 1440px)/2)) 18px}
.heading{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;flex-wrap:wrap}h1{font-size:30px;line-height:1.15;margin:0 0 8px}h2{font-size:17px;margin:0 0 14px}p{margin:0}a{color:var(--teal);text-underline-offset:3px}nav.links{display:flex;gap:18px;flex-wrap:wrap}.muted{color:var(--muted)}main{max-width:1480px;margin:auto;padding:20px}
.warning{border-left:4px solid var(--amber);background:#fff8e9;padding:12px 16px;margin-bottom:18px}.warning strong{display:block;color:#714710;margin-bottom:3px}.filters{display:grid;grid-template-columns:125px 180px 200px 180px minmax(170px,1fr);gap:12px;margin-bottom:18px}label{display:flex;flex-direction:column;gap:5px;color:var(--muted);font-size:12px;min-width:0}select,input{font:inherit;font-size:14px;min-width:0;width:100%;height:38px;border:1px solid #adbcb5;border-radius:4px;background:white;color:var(--ink);padding:7px 10px}select:disabled{background:#e8eeea;color:#697b72;border-color:var(--line)}input:focus,select:focus,button:focus-visible{outline:2px solid var(--teal);outline-offset:2px}.cohort-switch{display:flex;gap:0;flex-wrap:wrap;border-bottom:1px solid var(--line);margin-bottom:16px}.cohort-switch button{border:1px solid var(--line);border-bottom-width:3px;background:white;min-height:44px}.cohort-switch button[aria-pressed=true]{border-bottom-color:var(--teal);color:var(--teal);font-weight:bold;background:#e8f1ec}
.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border-top:1px solid var(--line);border-bottom:1px solid var(--line);background:white;margin-bottom:22px}.metric{padding:16px 18px;border-right:1px solid var(--line)}.metric:last-child{border-right:0}.metric span{display:block;font-size:12px;color:var(--muted)}.metric strong{display:block;font-size:28px;line-height:1.25;margin-top:5px}.metric small{font-size:12px;color:var(--muted)}
.charts{display:grid;grid-template-columns:1.1fr 1fr;gap:32px;margin:0 0 24px}.chart{min-width:0}.bar-row{display:grid;grid-template-columns:minmax(90px,210px) minmax(30px,1fr) 42px;gap:8px;align-items:center;margin:8px 0;font-size:12px}.bar-label{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.bar-track{height:13px;background:#dfe8e3}.bar{height:100%;background:var(--teal);min-width:1px}.bar-number{text-align:right;font-variant-numeric:tabular-nums}.chart:nth-child(2) .bar{background:#b3684c}
.chart-legend{display:flex;gap:7px 14px;flex-wrap:wrap;margin:0 0 10px;font-size:11px;color:var(--muted)}.legend-item{display:inline-flex;align-items:center;gap:5px}.legend-swatch{display:inline-block;width:10px;height:10px;flex:none}.stack-data{max-height:420px;overflow:auto;padding-right:7px}.stack-row{display:grid;grid-template-columns:minmax(100px,210px) minmax(30px,1fr) 45px;gap:8px;align-items:center;margin:9px 0;font-size:12px;min-height:20px}.stack-track{height:18px;background:#e4eae6;min-width:0}.stack-fill{display:flex;height:100%;min-width:0}.stack-segment{display:block;height:100%;min-width:0}.chart-note{font-size:11px;color:var(--muted);margin:8px 0 12px}.stack-segment:focus-visible{outline:2px solid var(--ink);outline-offset:2px}
.insight-fact{padding:10px 0;border-bottom:1px solid var(--line);font-size:13px}.insight-fact strong{display:block;margin-bottom:4px}.insight-fact p{margin:3px 0}.scenario-table{overflow:auto;border-top:1px solid var(--line);border-bottom:1px solid var(--line);margin:12px 0}.scenario-row{display:grid;grid-template-columns:minmax(180px,1.5fr) repeat(5,minmax(95px,1fr));min-width:760px;font-size:12px}.scenario-row>span{padding:10px 12px;border-bottom:1px solid var(--line)}.scenario-head{font-weight:bold;background:#eaf0ec}details{margin-top:16px}summary{cursor:pointer;color:var(--teal);font-weight:600;padding:8px 0}#capacity-decision{padding:12px 0;font-weight:600}#capacity-assumptions p{font-size:12px;color:var(--muted);margin:8px 0;overflow-wrap:anywhere}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);overflow-x:auto;margin-bottom:0}button{font:inherit;color:var(--muted);background:none;border:0;border-bottom:3px solid transparent;padding:12px 16px;cursor:pointer;white-space:nowrap}button[aria-selected=true]{border-bottom-color:var(--teal);color:var(--teal);font-weight:bold}.table-wrap{overflow:auto;max-height:620px;border-bottom:1px solid var(--line);background:#fff}table{border-collapse:collapse;width:100%;font-size:12px;min-width:740px}th,td{text-align:left;padding:10px 12px;border-bottom:1px solid #e8ece9;vertical-align:top;overflow-wrap:anywhere;max-width:380px}th{position:sticky;top:0;background:#eaf0ec;z-index:1;font-weight:600;white-space:nowrap}tbody tr:nth-child(even){background:#f8faf8}.number{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.empty{text-align:center;padding:35px;color:var(--muted)}.pager{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 0;flex-wrap:wrap}.pager label{flex-direction:row;align-items:center}.pager input{width:72px}.pager select{width:85px}.pager-controls{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.notes{padding:18px 0;border-top:1px solid var(--line);margin-top:12px}.notes ul{padding-left:20px;margin:0;color:var(--muted);font-size:12px}.notes li{margin:7px 0}.source{font-size:12px;margin:10px 0 0;overflow-wrap:anywhere}.status-failed{color:var(--red);font-weight:bold}.status-attention{color:var(--amber);font-weight:bold}.status-passed{color:var(--teal)}
.effort-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:12px 0}.effort-output{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;border-top:1px solid var(--line);padding:16px 0}.effort-output strong{display:block;font-size:21px}.effort-output span{font-size:12px;color:var(--muted)}
@media(max-width:850px){.filters,.effort-grid,.effort-output{grid-template-columns:1fr 1fr}.charts{grid-template-columns:1fr;gap:24px}.metrics{grid-template-columns:1fr 1fr}.metric:nth-child(2){border-right:0}.metric:nth-child(-n+2){border-bottom:1px solid var(--line)}}
@media(max-width:480px){header{padding:20px 16px 16px}main{padding:16px}h1{font-size:26px}.filters,.effort-grid{grid-template-columns:1fr}.metrics .metric{padding:12px}.metric strong{font-size:25px}button{padding:10px 12px}.bar-row,.stack-row{grid-template-columns:minmax(90px,145px) minmax(20px,1fr) 38px}}
</style></head><body>
<header><div class="heading"><div><h1>HK Test Output</h1><p class="muted">2025-2026 | Testcases, defects and platform comparison</p></div><nav class="links" aria-label="Report downloads"><a href="HK_Test_Output_2025_2026.xlsx">Excel</a><a href="ANALYSIS.md">Analysis</a><a href="HK_Test_Output_2025_2026.json">JSON</a></nav></div><p class="source" id="source"></p></header>
<main><div class="cohort-switch" role="group" aria-label="Report section"><button type="button" id="cohort-title" aria-pressed="true">HK Testcases &amp; Defects</button><button type="button" id="cohort-vin" aria-pressed="false">Rack vs Vehicle</button></div><section class="warning" aria-label="Evidence boundary"><strong id="quality-heading">HK testcase and defect overview</strong><p id="quality-warning"></p></section>
<section class="filters" aria-label="Report filters">
<label>Calendar year<select id="year">
<option value="all">2025 + 2026</option>
<option value="2025">2025</option>
<option value="2026">2026</option>
</select></label>
<label id="case-status-control">Latest case execution<select id="case-status">
<option value="all">All case outcomes</option><option value="Passed">Passed</option>
<option value="Failed">Failed</option><option value="Requires Attention">Requires Attention</option>
<option value="No execution">No local execution</option><option value="Planned only">Planned only</option>
</select></label>
<label hidden>Inventory evidence<select id="evidence">
<option value="all">6 confirmed + 4 legacy candidates</option>
<option value="user_confirmed">Confirmed six vehicles</option>
<option value="legacy_mapping_candidate">Legacy four candidates</option>
</select></label>
<label id="platform-control" hidden>Platform (optional)<select id="vin"><option value="all">All selected platforms</option></select></label>
<label>Table search<input id="search" type="search" aria-label="Search current table" placeholder="AIDA, ticket ID, testcase, VIN"></label></section>
<label id="environment-control" hidden style="max-width:420px;margin-bottom:16px">Platform comparison<select id="environment-view">
<option value="comparison">Rack vs Vehicle (static/dynamic sources)</option>
<option value="classified_aida">AIDA comparison</option><option value="platform_defects">Defect comparison</option>
<option value="capabilities">Capability comparison</option><option value="evidence">Execution evidence</option>
<option value="actual">Platform execution details</option>
</select></label>
<label id="automation-control" hidden style="max-width:420px;margin-bottom:16px">Automation review<select id="automation-view">
<option value="candidates">Prioritized case review</option><option value="routes">Review-route summary</option>
<option value="sources">Static / dynamic source convention</option><option value="gates">Evidence and pilot gates</option>
<option value="effort">Effort what-if model</option>
</select></label>
<section id="effort-panel" hidden aria-label="Hypothetical effort scenario"><h2>Effort What-if Model</h2><p class="muted">One consistent horizon; eligible executions only. Inputs are unmeasured assumptions and do not alter frozen evidence.</p>
<div class="effort-grid">
<label>Eligible executions N<input id="effort-volume" type="number" min="0" step="1"></label>
<label>Manual active minutes / execution<input id="effort-manual" type="number" min="0" step="any"></label>
<label>Automation adoption %<input id="effort-share" type="number" min="0" max="100" step="any"></label>
<label>Supervision minutes / automated execution<input id="effort-supervision" type="number" min="0" step="any"></label>
<label>One-time build hours<input id="effort-build" type="number" min="0" step="any"></label>
<label>Period maintenance hours<input id="effort-maintenance" type="number" min="0" step="any"></label>
<label>Additional validation hours<input id="effort-validation" type="number" min="0" step="any"></label>
</div><p id="effort-state" aria-live="polite">Not configured; eligible volume and measured assumptions unavailable.</p>
<div class="effort-output"><div><span>Manual baseline hours</span><strong id="effort-baseline">Not assessed</strong></div><div><span>Automation scenario hours</span><strong id="effort-proposed">Not assessed</strong></div><div><span>Hypothetical net hours saved</span><strong id="effort-net">Not assessed</strong></div><div><span>Break-even eligible executions</span><strong id="effort-break-even">Not assessed</strong></div></div></section>
<section class="metrics" aria-label="Selected evidence totals"><div class="metric"><span id="metric-runs-label">HK testcase executions</span><strong id="metric-runs"></strong><small id="run-status"></small></div><div class="metric"><span id="metric-tests-label">Unique executed testcases</span><strong id="metric-tests"></strong><small id="metric-tests-note">Distinct test IDs</small></div><div class="metric"><span id="metric-third-label">HK platform defects</span><strong id="metric-defects"></strong><small id="metric-third-note">Separate platform defect evidence</small></div><div class="metric"><span id="metric-fourth-label">AIDA codes with executions</span><strong id="metric-aidas"></strong><small id="metric-fourth-note">Not a coverage percentage</small></div></section>
<section class="charts" aria-label="Execution results and defect phases"><div class="chart"><h2 id="aida-chart-title">HK Execution Results by AIDA</h2><div id="aida-chart" role="img" aria-label="Execution records stacked by result status"></div></div><div class="chart"><h2 id="month-chart-title">HK Defects by AIDA and Phase</h2><div id="month-chart" role="img" aria-label="Unique defect IDs stacked by current phase"></div></div><div class="chart"><h2 id="execution-detail-title">Monthly Execution Results</h2><div id="execution-detail-chart" role="img" aria-label="Execution result detail distribution"></div></div><div class="chart"><h2 id="defect-detail-title">Monthly Defect Phases</h2><div id="defect-detail-chart" role="img" aria-label="Defect phase detail distribution"></div></div></section>
<nav class="tabs" role="tablist" aria-label="Analysis views"></nav><p class="source muted" id="table-scope"></p><section class="table-wrap" tabindex="0" aria-label="Scrollable analysis table"><table><thead></thead><tbody></tbody></table></section>
<div class="pager"><span id="row-count" aria-live="polite"></span><div class="pager-controls"><label>Rows<select id="page-size"><option>50</option><option>100</option><option>250</option></select></label><label>Page<input type="number" id="page" min="1" value="1" aria-label="Current page"></label><span id="page-count"></span></div></div>
<section class="notes" id="hk-insights"><h2>HK Testing Insights and 2027 Capacity</h2><p id="capacity-baseline" class="muted"></p><div id="insight-facts"></div><h3>Illustrative Capacity Scenarios</h3><p class="muted">Assumptions, not measured savings. The staffing target is one person-year at the stated capacity assumption.</p><div id="capacity-scenarios" class="scenario-table" role="table" aria-label="Illustrative 2027 capacity scenarios"></div><p id="capacity-decision"></p><details id="capacity-assumptions"><summary>Scenario inputs and modelling basis</summary><div id="capacity-assumption-details"></div></details><details><summary>Recommended pilot and workload actions</summary><ul id="capacity-actions"></ul></details></section>
<section class="notes"><details id="counting-boundaries"><summary>Detailed Counting Boundaries and Evidence Limits</summary><ul id="limits"></ul></details></section></main>
<script id="report-data" type="application/json">__REPORT_DATA__</script>
<script>
const report=JSON.parse(document.querySelector('#report-data').textContent);
const element=selector=>document.querySelector(selector);
const format=value=>typeof value==='number'?value.toLocaleString('en-US'):String(value??'');
const caseAnalysis=report.hk_testcase_analysis??{cases:[],runs:[],limitations:[],overlap_with_vin_scope:{executed_runs:0}};
const automation=report.automation_analysis??{cases:[],review_gates:[],limitations:[]};
const capacityInsights=report.capacity_insights;
let currentView='Testcases',currentCohort='hk_title';
const views=['Testcases','Automation','AIDA','Annual','Monthly','Results','Runs','Environment','Defects','Platforms','Quality'];
const environmentSignals=['bench_tag_only','vehicle_tag_only','both_tags','other_taxonomy','no_platform_tag'];
const environmentLabels={bench_tag_only:'sWKP only (bench signal)',vehicle_tag_only:'dWKP only (vehicle signal)',both_tags:'Both dWKP + sWKP',other_taxonomy:'Other taxonomy',no_platform_tag:'No platform tag'};
const inventory=new Map(report.inventory.map(item=>[item.vin,item]));
function node(tag,text){const item=document.createElement(tag);if(text!==undefined)item.textContent=text;return item;}
function renderCapacityInsights(){if(!capacityInsights){element('#hk-insights').hidden=true;return;}const baseline=capacityInsights.planning_baseline,capacity=capacityInsights.person_capacity_hours_assumption;element('#capacity-baseline').textContent=`Snapshot baseline: ${baseline.year}, ${format(baseline.observed_executions)} execution records through ${baseline.snapshot_cutoff}. Full frozen-cohort planning context, independent of the chart filters above.`;for(const fact of capacityInsights.facts){const item=node('div');item.className='insight-fact';item.append(node('strong',fact.topic),node('p',fact.finding),node('p',fact.action));element('#insight-facts').append(item);}const headers=['Illustrative scenario','Automation net h','Rack transfer net h','Combined net h','One-person gap %','Remaining gap h'],head=node('div');head.className='scenario-row scenario-head';head.setAttribute('role','row');for(const text of headers){const cell=node('span',text);cell.setAttribute('role','columnheader');head.append(cell);}element('#capacity-scenarios').append(head);for(const scenario of capacityInsights.scenarios){const row=node('div');row.className='scenario-row';row.setAttribute('role','row');for(const key of ['scenario','automation_net_hours','rack_transfer_net_hours','combined_net_hours','capacity_gap_coverage_percent','remaining_capacity_gap_hours']){const value=scenario[key],cell=node('span',value===null?'Not assessed':format(value));cell.setAttribute('role','cell');row.append(cell);}element('#capacity-scenarios').append(row);const details=node('p',scenario.scenario+': '+Object.entries(scenario.inputs).map(([key,value])=>key+' = '+(value===null?'not assessed':value)).join('; '));element('#capacity-assumption-details').append(details);}const assessed=capacityInsights.scenarios.filter(row=>row.combined_net_hours!==null),maximum=assessed.length?Math.max(...assessed.map(row=>row.combined_net_hours)):null;element('#capacity-decision').textContent=maximum===null?'No capacity estimate: snapshot date or required baseline is unavailable.':maximum<capacity?`Under these explicit example assumptions, the best scenario covers ${Math.max(...assessed.map(row=>row.capacity_gap_coverage_percent)).toFixed(1)}% of a ${format(capacity)} h person-year. This does not establish that one fewer tester can retain the same coverage.`:'A numerical scenario reaches the planning target, but pilot validation and full-workload accounting are still required; no staffing replacement is proven.';const basis=node('p',`Calendar-scaled illustration: ${baseline.observed_automation_screened_executions} automation-screened and ${baseline.observed_transfer_screened_executions} non-overlapping rack-transfer review executions over ${baseline.calendar_days_observed??'unavailable'} days. Annualized example volumes: ${baseline.annualized_automation_volume??'not assessed'} / ${baseline.annualized_transfer_volume??'not assessed'}. One-person assumption: ${capacity} productive h/year; replace with approved HK capacity. At 15 assumed active minutes each, all visible annualized executions total ${baseline.all_direct_execution_hours_if_15_active_minutes??'not assessed'} gross hours before investment. This cohort is not all HK team work.`);element('#capacity-assumption-details').prepend(basis);for(const action of capacityInsights.actions)element('#capacity-actions').append(node('li',action));}
function eligible(row,selection){const {year,vin,mapped}=selection;return (year==='all'||String(row.year)===year)&&(vin==='all'||(row.matched_vins??[]).includes(vin)||String(row.vehicle_value??row.vin??'').toUpperCase().includes(vin)||mapped&&(row.v_numbers??[]).includes(mapped.v_number));}
function summarize(runs,defects){return {executed:runs.filter(row=>row.is_executed).length,tests:new Set(runs.filter(row=>row.is_executed&&row.test_id).map(row=>row.test_id)).size,passed:runs.filter(row=>row.status==='Passed').length,failed:runs.filter(row=>row.status==='Failed').length,attention:runs.filter(row=>row.status==='Requires Attention').length,planned:runs.filter(row=>row.status==='Planned').length,defects:defects.length,linkedDefects:new Set(runs.flatMap(row=>row.linked_defect_ids??[])).size};}
function caseRows(runs,catalog=caseAnalysis.cases){const groups=new Map();for(const row of runs){if(!groups.has(row.test_id))groups.set(row.test_id,[]);groups.get(row.test_id).push(row);}return catalog.map(item=>{const records=groups.get(item.test_id)??[],executions=records.filter(row=>row.is_executed),linked=new Set(records.flatMap(row=>row.linked_defect_ids));return {...item,...summarize(records,[]),recordCount:records.length,latest:executions.at(-1)?.status??'No execution',latestRecord:records.at(-1)?.status??'No record',lastDate:records.at(-1)?.date??'',links:linked.size};});}
function selectedData(){const year=element('#year').value,status=element('#case-status').value,periodRuns=caseAnalysis.runs.filter(row=>year==='all'||String(row.year)===year),defects=report.defects.filter(row=>year==='all'||String(row.year)===year);if(currentCohort==='vin'){const vin=element('#vin').value,selection={year,vin,mapped:vin==='all'?undefined:(report.v_number_mapping??[]).find(item=>item.vin===vin)},matches=row=>eligible(row,selection),runs=periodRuns.filter(matches);return {runs,defects:defects.filter(matches),cases:caseRows(runs).filter(item=>item.recordCount)};}let cases=caseRows(periodRuns);if(status!=='all')cases=cases.filter(item=>status==='Planned only'?item.recordCount>0&&item.planned===item.recordCount:item.latest===status);const ids=new Set(cases.map(item=>item.test_id));return {runs:periodRuns.filter(row=>ids.has(row.test_id)),defects,cases};}
function group(runs,defects,key){const groups=new Map();for(const [kind,rows] of [['runs',runs],['defects',defects]])for(const row of rows){const value=row[key];if(!groups.has(value))groups.set(value,{runs:[],defects:[]});groups.get(value)[kind].push(row);}return [...groups.entries()].map(([key,value])=>({key,...summarize(value.runs,value.defects),name:(value.runs[0]??value.defects[0]).aida_name,code:(value.runs[0]??value.defects[0]).aida_code}));}
function bars(selector,rows,labelKey,valueKey){const container=element(selector);container.replaceChildren();const maximum=Math.max(1,...rows.map(row=>row[valueKey]));if(!rows.length){container.append(node('p','No matching evidence'));return;}for(const row of rows){const line=node('div');line.className='bar-row';const label=node('span',row[labelKey]);label.className='bar-label';label.title=row[labelKey];const track=node('div');track.className='bar-track';const bar=node('div');bar.className='bar';bar.style.width=(100*row[valueKey]/maximum)+'%';track.append(bar);const count=node('span',format(row[valueKey]));count.className='bar-number';line.append(label,track,count);container.append(line);}}
const RUN_STATUS_SERIES=[{key:'Passed',label:'Passed',color:'#176c63'},{key:'Failed',label:'Failed',color:'#bb3b41'},{key:'Blocked',label:'Blocked',color:'#855088'},{key:'Requires Attention',label:'Requires Attention',color:'#b2690c'},{key:'Planned',label:'Planned',color:'#397694'},{key:'Not Run',label:'Not Run',color:'#6c7d72'},{key:'Skipped',label:'Skipped',color:'#8a6747'},{key:'Other',label:'Other / Unknown',color:'#434d54'}];
const DEFECT_PHASE_SERIES=[{key:'01',label:'01 New',color:'#637ca4'},{key:'02',label:'02 Q-Gate',color:'#3c577a'},{key:'03',label:'03 In Analysis',color:'#b87c13'},{key:'04',label:'04 In Progress',color:'#b45d38'},{key:'05',label:'05',color:'#8b5989'},{key:'06',label:'06 Concluded',color:'#176c63'},{key:'07',label:'07 Q-Gate',color:'#8b793a'},{key:'08',label:'08 In Verification',color:'#217d91'},{key:'09',label:'09 Without Action',color:'#6e7577'},{key:'Other',label:'Other / Unknown Phase',color:'#41494d'}];
function chartRunStatus(row){const value=String(row.status??'').trim().toLowerCase().replace(/\s+/g,' '),mapping={'passed':'Passed','failed':'Failed','block':'Blocked','blocked':'Blocked','requires attention':'Requires Attention','planned':'Planned','not run':'Not Run','not completed':'Not Run','skipped':'Skipped'};return mapping[value]??'Other';}
function chartDefectPhase(row){const value=String(row.phase_code??String(row.phase??'').match(/^\d{2}/)?.[0]??'').padStart(2,'0');return DEFECT_PHASE_SERIES.some(series=>series.key===value)?value:'Other';}
function stackRows(records,dimension,kind,splitPlatforms=false){const series=kind==='runs'?RUN_STATUS_SERIES:DEFECT_PHASE_SERIES,groups=new Map();for(const record of records){if(splitPlatforms&&!['Bench','Vehicle'].includes(record.actual_environment))continue;const value=dimension==='platform'?record.actual_environment:dimension==='aida'?record.aida_key??record.aida_code??'Unclassified':record[dimension]??'Unknown',key=String(value)+(splitPlatforms&&dimension!=='platform'?'|'+record.actual_environment:'');if(!groups.has(key)){const suffix=splitPlatforms&&dimension!=='platform'?' / '+(record.actual_environment==='Bench'?'Rack':'Vehicle'):'',label=dimension==='platform'?(value==='Bench'?'Rack / Static-origin':'Vehicle / Dynamic-origin'):dimension==='aida'?(record.aida_name||'Unclassified AIDA')+suffix:String(value)+suffix;groups.set(key,{label,total:0,values:Object.fromEntries(series.map(item=>[item.key,0]))});}const group=groups.get(key),status=kind==='runs'?chartRunStatus(record):chartDefectPhase(record);group.total+=1;group.values[status]+=1;}if(dimension==='platform'){return ['Bench','Vehicle'].map(environment=>groups.get(environment)??{label:environment==='Bench'?'Rack / Static-origin':'Vehicle / Dynamic-origin',total:0,values:Object.fromEntries(series.map(item=>[item.key,0]))});}const rows=[...groups.values()];if(dimension==='month'||dimension==='year')rows.sort((first,second)=>first.label.localeCompare(second.label));else rows.sort((first,second)=>second.total-first.total||first.label.localeCompare(second.label));return rows;}
function stackedBars(selector,rows,series,description){const container=element(selector);container.replaceChildren();const legend=node('div');legend.className='chart-legend';for(const item of series){const entry=node('span');entry.className='legend-item';entry.dataset.series=item.key;const swatch=node('span');swatch.className='legend-swatch';swatch.style.backgroundColor=item.color;swatch.setAttribute('aria-hidden','true');entry.append(swatch,node('span',item.label));legend.append(entry);}container.append(legend);const note=node('p',description+' | '+format(rows.reduce((sum,row)=>sum+row.total,0))+' records');note.className='chart-note';container.append(note);const body=node('div');body.className='stack-data';const maximum=Math.max(1,...rows.map(row=>row.total));for(const row of rows){const line=node('div');line.className='stack-row';line.dataset.total=String(row.total);line.dataset.label=row.label;const label=node('span',row.label);label.className='bar-label';label.title=row.label;const track=node('div');track.className='stack-track';const fill=node('div');fill.className='stack-fill';fill.style.width=(100*row.total/maximum)+'%';for(const item of series){const value=row.values[item.key]??0;if(!value)continue;const segment=node('span');segment.className='stack-segment';segment.dataset.series=item.key;segment.dataset.value=String(value);segment.style.backgroundColor=item.color;segment.style.width=(100*value/row.total)+'%';segment.title=row.label+' | '+item.label+': '+format(value);segment.setAttribute('aria-label',segment.title);segment.tabIndex=0;fill.append(segment);}track.append(fill);const total=node('span',format(row.total));total.className='bar-number';line.append(label,track,total);body.append(line);}if(!rows.length)body.append(node('p','No matching evidence'));container.append(body);}
function renderStatusCharts(runs,defects,isOverview){const period=currentView==='Annual'?'year':'month';if(isOverview){element('#aida-chart-title').textContent='HK Execution Results by AIDA';element('#month-chart-title').textContent='HK Defects by AIDA and Phase';element('#execution-detail-title').textContent=period==='year'?'Annual Execution Results':'Monthly Execution Results';element('#defect-detail-title').textContent=period==='year'?'Annual Defect Phases':'Monthly Defect Phases';stackedBars('#aida-chart',stackRows(runs,'aida','runs'),RUN_STATUS_SERIES,'MR result records, including planned/blocked; not distinct testcase IDs');stackedBars('#month-chart',stackRows(defects,'aida','defects'),DEFECT_PHASE_SERIES,'Unique defect IDs by current phase; 06 does not prove verified repair');stackedBars('#execution-detail-chart',stackRows(runs,period,'runs'),RUN_STATUS_SERIES,'MR result records by selected calendar period');stackedBars('#defect-detail-chart',stackRows(defects,period,'defects'),DEFECT_PHASE_SERIES,'Unique defect IDs by creation period and current phase');return;}const dimension=['Annual','Monthly'].includes(currentView)?period:'aida',detail=dimension==='aida'?'AIDA':dimension==='year'?'Annual':'Monthly';element('#aida-chart-title').textContent='Rack vs Vehicle: Execution Results';element('#month-chart-title').textContent='Rack vs Vehicle: Defect Phases';element('#execution-detail-title').textContent=detail+' Execution Results by Platform';element('#defect-detail-title').textContent=detail+' Defect Phases by Platform';stackedBars('#aida-chart',stackRows(runs,'platform','runs',true),RUN_STATUS_SERIES,'MR result records; unclassified platforms excluded');stackedBars('#month-chart',stackRows(defects,'platform','defects',true),DEFECT_PHASE_SERIES,'Separate platform defect evidence; unclassified platforms excluded');stackedBars('#execution-detail-chart',stackRows(runs,dimension,'runs',true),RUN_STATUS_SERIES,'All matched '+detail+' groups by platform; MR result records');stackedBars('#defect-detail-chart',stackRows(defects,dimension,'defects',true),DEFECT_PHASE_SERIES,'All matched '+detail+' groups by platform; unique defect IDs');}
function automationRows(cases){const byId=new Map(automation.cases.map(item=>[item.test_id,item]));return cases.map(item=>({...byId.get(item.test_id),test_id:item.test_id,test_name:item.test_name,executed:item.executed,additional:Math.max(0,item.executed-1),latest:item.latest})).sort((first,second)=>second.additional-first.additional||second.executed-first.executed||first.test_id.localeCompare(second.test_id));}
function renderEffort(){const selectors=['#effort-volume','#effort-manual','#effort-share','#effort-supervision','#effort-build','#effort-maintenance','#effort-validation'],raw=selectors.map(selector=>element(selector).value),outputs=['#effort-baseline','#effort-proposed','#effort-net','#effort-break-even'];for(const selector of outputs)element(selector).textContent='Not assessed';if(raw.some(value=>value==='')){element('#effort-state').textContent='Not configured; eligible volume and measured assumptions unavailable.';return;}const values=raw.map(Number);if(values.some(value=>!Number.isFinite(value)||value<0)||values[2]>100||!Number.isInteger(values[0])){element('#effort-state').textContent='Invalid assumptions: finite nonnegative inputs, whole executions and adoption between 0 and 100% are required.';return;}const [volume,manual,percent,supervision,build,maintenance,validation]=values,share=percent/100,fixed=build+maintenance+validation,baseline=volume*manual/60,proposed=volume*((1-share)*manual+share*supervision)/60+fixed,gain=share*(manual-supervision)/60;element('#effort-baseline').textContent=baseline.toFixed(2);element('#effort-proposed').textContent=proposed.toFixed(2);element('#effort-net').textContent=(baseline-proposed).toFixed(2);element('#effort-break-even').textContent=gain>0?String(Math.ceil(fixed/gain)):'No positive break-even';element('#effort-state').textContent='Hypothetical scenario only; not measured savings, annual forecast or an automation approval.';}
function tableData(runs,defects,cases){
 if(currentCohort==='vin'&&['AIDA','Annual','Monthly','Results'].includes(currentView)){
  if(currentView==='Results'){const rows=[];for(const environment of ['Bench','Vehicle']){const selected=runs.filter(row=>row.actual_environment===environment),catalog=[...new Set(selected.map(row=>row.test_id).filter(Boolean))].map(test_id=>({test_id})),counts=new Map();for(const item of caseRows(selected,catalog))counts.set(item.latest,(counts.get(item.latest)??0)+1);for(const [status,count] of counts)rows.push([environment==='Bench'?'Rack / Static-origin':'Vehicle / Dynamic-origin',status,count]);}return {headers:['Platform / Source Convention','Latest Observed Result per Case/Platform','Distinct Testcases'],rows};}
  if(currentView==='AIDA'){const groups=group(runs,defects,'aida_key');return {headers:['AIDA Code','Function','Rack Executions','Vehicle Executions','Rack Testcases','Vehicle Testcases','Rack Defects','Vehicle Defects'],rows:groups.sort((first,second)=>second.executed-first.executed||second.defects-first.defects).map(item=>{const selected=runs.filter(row=>row.aida_key===item.key),issues=defects.filter(row=>row.aida_key===item.key),rack=summarize(selected.filter(row=>row.actual_environment==='Bench'),issues.filter(row=>row.actual_environment==='Bench')),vehicle=summarize(selected.filter(row=>row.actual_environment==='Vehicle'),issues.filter(row=>row.actual_environment==='Vehicle'));return [item.code,item.name,rack.executed,vehicle.executed,rack.tests,vehicle.tests,rack.defects,vehicle.defects];})};}
  const key=currentView==='Annual'?'year':'month',periods=[...new Set([...runs,...defects].map(row=>row[key]))].sort();return {headers:[currentView==='Annual'?'Year':'Month','Platform / Source Convention','Executed','Testcases','Passed','Failed','Attention','Planned','Pass % P/(P+F)','HK Platform Defects'],rows:periods.flatMap(period=>['Bench','Vehicle'].map(environment=>{const totals=summarize(runs.filter(row=>row[key]===period&&row.actual_environment===environment),defects.filter(row=>row[key]===period&&row.actual_environment===environment)),denominator=totals.passed+totals.failed;return [period,environment==='Bench'?'Rack / Static-origin':'Vehicle / Dynamic-origin',totals.executed,totals.tests,totals.passed,totals.failed,totals.attention,totals.planned,denominator?Math.round(10000*totals.passed/denominator)/100:'N/A',totals.defects];}))};
 }
 if(currentView==='Automation'){
  const view=element('#automation-view').value,records=automationRows(cases);
  if(view==='sources')return {headers:['Recorded Platform','Management Origin','Actual Vehicle Motion','VIN Defect IDs','Executed Records'],rows:['Bench','Vehicle','Unknown'].map(environment=>{const selected=defects.filter(row=>row.actual_environment===environment);return [environment,environment==='Bench'?'Static-origin':environment==='Vehicle'?'Dynamic-origin':'Unknown-origin','Unknown',selected.length,runs.filter(row=>row.actual_environment===environment&&row.is_executed).length];})};
  if(view==='gates')return {headers:['Review Gate','Evidence Needed','Default Review Status'],rows:automation.review_gates.map(row=>[row.gate,row.evidence_needed,row.default_status])};
  if(view==='effort')return {headers:['Assumption Boundary','Value'],rows:[['Measured savings','Not available'],['Eligible volume','Requires version/configuration review'],['Fixed costs','Build + maintenance + additional validation, counted once'],['Model horizon','One consistent horizon; not automatically annual'],['Physical scenario','Vehicle source does not prove motion or non-automatability']]};
  if(view==='routes'){const groups=new Map();for(const row of records){if(!groups.has(row.review_route))groups.set(row.review_route,[]);groups.get(row.review_route).push(row);}return {headers:['Title-Based Review Route','Case IDs','Executed Records','Additional Records','Automation Feasibility'],rows:[...groups.entries()].map(([route,items])=>[route,items.length,items.reduce((sum,row)=>sum+row.executed,0),items.reduce((sum,row)=>sum+row.additional,0),'Unreviewed'])};}
  return {headers:['Review Rank','Test ID','Testcase','Executed','Additional Records','Latest Result','Title-Based Review Route','Candidate Approach','Feasibility'],rows:records.map((row,index)=>[index+1,row.test_id,row.test_name,row.executed,row.additional,row.latest,row.review_route,row.candidate_approach,'Unreviewed'])};
 }
 if(currentView==='Environment'){
  const view=element('#environment-view').value;
    if(view==='comparison')return {headers:['Platform / Source Convention','Executed','Distinct Testcases','Passed','Failed','Attention','Planned','Pass % P/(P+F)','HK Platform Defects','Linked Defect IDs','Actual Vehicle Motion'],rows:['Bench','Vehicle'].map(environment=>{const selected=runs.filter(row=>row.actual_environment===environment),issues=defects.filter(row=>row.actual_environment===environment),totals=summarize(selected,issues),denominator=totals.passed+totals.failed;return [environment==='Bench'?'Rack / Static-origin':'Vehicle / Dynamic-origin',totals.executed,totals.tests,totals.passed,totals.failed,totals.attention,totals.planned,denominator?Math.round(10000*totals.passed/denominator)/100:'N/A',totals.defects,totals.linkedDefects,'Unknown; vehicle may be stationary'];})};
    if(view==='unknown'){const groups=new Map();for(const row of runs.filter(item=>item.actual_environment==='Unknown')){const identifier=row.vehicle_value||'<EMPTY>';if(!groups.has(identifier))groups.set(identifier,[]);groups.get(identifier).push(row);}return {headers:['Unresolved Identifier','V-number Candidates','Reason','Records','Executed','Planned','Example MR IDs'],rows:[...groups.entries()].sort((first,second)=>second[1].length-first[1].length).map(([identifier,records])=>{const numbers=[...new Set(records.flatMap(row=>row.v_numbers??[]))],reason=identifier==='<EMPTY>'?'Native identifier empty / local capture unavailable':numbers.length?'V-number type needs confirmation':'VIN to V-number mapping unavailable';return [identifier,numbers.join('; '),reason,records.length,records.filter(row=>row.is_executed).length,records.filter(row=>row.status==='Planned').length,records.slice(0,3).map(row=>row.run_id).join('; ')];})};}
    if(view==='actual')return {headers:['Platform Type (V-number rule)','Records','Executed','Testcases','Passed','Failed','Attention','Planned'],rows:['Bench','Vehicle','Unknown'].map(environment=>{const selected=runs.filter(row=>(row.actual_environment??'Unknown')===environment),totals=summarize(selected,[]);return [environment,selected.length,totals.executed,totals.tests,totals.passed,totals.failed,totals.attention,totals.planned];})};
    if(view==='platform_defects')return {headers:['Platform Type (V-number rule)','VIN Defects','Phase 06','Phase 09','Other Phases'],rows:['Bench','Vehicle','Unknown'].map(environment=>{const selected=defects.filter(row=>(row.actual_environment??'Unknown')===environment);return [environment,selected.length,selected.filter(row=>row.phase_code==='06').length,selected.filter(row=>row.phase_code==='09').length,selected.filter(row=>!['06','09'].includes(row.phase_code)).length];})};
    if(view==='classified_aida'){const groups=group(runs,defects,'aida_key');return {headers:['AIDA Code','Function','Rack Executions','Vehicle Executions','Rack Testcases','Vehicle Testcases','Rack Defects','Vehicle Defects'],rows:groups.sort((first,second)=>second.executed-first.executed||second.defects-first.defects).map(item=>{const selected=runs.filter(row=>row.aida_key===item.key),issues=defects.filter(row=>row.aida_key===item.key),rack=summarize(selected.filter(row=>row.actual_environment==='Bench'),issues.filter(row=>row.actual_environment==='Bench')),vehicle=summarize(selected.filter(row=>row.actual_environment==='Vehicle'),issues.filter(row=>row.actual_environment==='Vehicle'));return [item.code,item.name,rack.executed,vehicle.executed,rack.tests,vehicle.tests,rack.defects,vehicle.defects];})};}
  if(view==='capabilities'){const guidance=caseAnalysis.environment_comparison?.capability_guidance??[];return {headers:['Dimension','Bench Contribution (conditional)','Vehicle Contribution','Required Comparison Evidence'],rows:guidance.map(row=>[row.dimension,row.bench,row.vehicle,row.evidence_needed])};}
    if(view==='evidence')return {headers:['Run ID','Test ID','Testcase','Date','Status','Platform Type','V-number','Classification Source','Mapping Sources','Observed Taxonomy','Identifier','Tool','Software'],rows:[...runs].reverse().map(row=>[row.run_id,row.test_id,row.test_name,row.date,row.status,row.actual_environment??'Unknown',(row.v_numbers??[]).join('; '),row.environment_classification_source??'',(row.v_number_mapping_sources??[]).join('; '),environmentLabels[row.environment_tag_signal??'no_platform_tag'],row.vehicle_value||'Missing',row.testing_tool_type||'Missing',row.software_version||'Missing'])};
  if(view==='aida'){const groups=new Map();for(const row of runs){if(!groups.has(row.aida_key))groups.set(row.aida_key,[]);groups.get(row.aida_key).push(row);}return {headers:['AIDA Code','Function','Executed','sWKP Only','dWKP Only','Both Tags','Other Taxonomy','No Platform Tag'],rows:[...groups.values()].sort((first,second)=>second.length-first.length).map(records=>[records[0].aida_code,records[0].aida_name,summarize(records,[]).executed,...environmentSignals.map(signal=>records.filter(row=>(row.environment_tag_signal??'no_platform_tag')===signal).length)])};}
  return {headers:['Observed Platform Taxonomy (not actual environment)','Records','Executed','Testcases','Passed','Failed','Attention','Planned'],rows:environmentSignals.map(signal=>{const selected=runs.filter(row=>(row.environment_tag_signal??'no_platform_tag')===signal),totals=summarize(selected,[]);return [environmentLabels[signal],selected.length,totals.executed,totals.tests,totals.passed,totals.failed,totals.attention,totals.planned];})};
 }
 if(currentView==='Annual'||currentView==='Monthly'){const key=currentView==='Annual'?'year':'month',groups=new Map();for(const row of runs){if(!groups.has(row[key]))groups.set(row[key],[]);groups.get(row[key]).push(row);}for(const row of defects)if(!groups.has(row[key]))groups.set(row[key],[]);return {headers:[currentView==='Annual'?'Year':'Month','Executed','Testcases','Passed','Failed','Attention','Planned','Pass % P/(P+F)','HK Platform Defects','Linked Defect IDs'],rows:[...groups.keys()].sort().map(value=>{const totals=summarize(groups.get(value),defects.filter(row=>row[key]===value)),denominator=totals.passed+totals.failed;return [value,totals.executed,totals.tests,totals.passed,totals.failed,totals.attention,totals.planned,denominator?Math.round(10000*totals.passed/denominator)/100:'N/A',totals.defects,totals.linkedDefects];})};}
 if(currentView==='Results'){const counts=new Map();const vinCatalog=[...new Set(runs.map(row=>row.test_id).filter(Boolean))].map(test_id=>({test_id}));const records=currentCohort==='hk_title'?cases:caseRows(runs,vinCatalog);for(const item of records)counts.set(item.latest,(counts.get(item.latest)??0)+1);return {headers:['Latest Executed Result','Distinct Testcases'],rows:[...counts.entries()]};}
 if(currentView==='AIDA'){const rows=group(runs,defects,'aida_key').sort((first,second)=>second.executed-first.executed||second.defects-first.defects);return {headers:['AIDA Code','Function','Executed','Testcases','Passed','Failed','Attention','Planned','HK Platform Defects','Linked Defect IDs'],rows:rows.map(row=>[row.code,row.name,row.executed,row.tests,row.passed,row.failed,row.attention,row.planned,row.defects,row.linkedDefects])};}
 if(currentView==='Platforms'){const evidence=element('#evidence').value,vin=element('#vin').value;return {headers:['VIN','V-number','Platform Type','Model','Inventory Evidence','Executed','Testcases','Defects'],rows:report.inventory.filter(item=>(evidence==='all'||item.evidence===evidence)&&(vin==='all'||item.vin===vin)).map(item=>{const totals=summarize(runs.filter(row=>row.matched_vins.includes(item.vin)),defects.filter(row=>row.matched_vins.includes(item.vin)));return [item.vin,item.v_number??'',item.actual_environment??'Unknown',item.model,item.evidence,totals.executed,totals.tests,totals.defects];})};}
 if(currentView==='Testcases'&&currentCohort==='hk_title')return {headers:['Test ID','HK Testcase','AIDA / Historical Case Metadata','Executed','Passed','Failed','Attention','Planned','Latest Executed','Latest Record','Last Record Date','Linked Defect Count'],rows:cases.map(item=>[item.test_id,item.test_name,item.aida_names.join('; '),item.executed,item.passed,item.failed,item.attention,item.planned,item.latest,item.latestRecord,item.lastDate,item.links])};
 if(currentView==='Testcases'){const groups=new Map();for(const row of runs){if(!row.test_id)continue;if(!groups.has(row.test_id))groups.set(row.test_id,[]);groups.get(row.test_id).push(row);}return {headers:['Test ID','Testcase','AIDA','Executed','Passed','Failed','Attention','Last Record'],rows:[...groups.entries()].map(([testId,records])=>{const totals=summarize(records,[]);return [testId,records.at(-1).test_name,[...new Set(records.map(row=>row.aida_name))].join('; '),totals.executed,totals.passed,totals.failed,totals.attention,records.at(-1).date];})};}
 if(currentView==='Defects')return {headers:['Defect ID','Title','Date','AIDA','VIN','V-number','Platform Type','VIN Evidence','Phase','Severity','Reporting Class','Detected By'],rows:[...defects].reverse().map(row=>[row.defect_id,row.name,row.date.slice(0,10),row.aida_name,row.matched_vins.map(value=>value.slice(-7)).join('; '),(row.v_numbers??[]).join('; '),row.actual_environment??'Unknown',row.vin_source,row.phase,row.severity,row.reporting_class,row.detected_by])};
 if(currentView==='Runs')return {headers:['MR ID','Test ID','Testcase','Date','Status','AIDA','Observed Platform Identifier','Current Platform Metadata','Frozen Local Capture','Native MR Check','Historical Link Verified','Identifier Source','Date Basis','Linked Defect IDs'],rows:[...runs].reverse().map(row=>[row.run_id,row.test_id,row.test_name,row.date,row.status,row.aida_name,row.vehicle_value||'Identifier unavailable',row.mr_platform_capture_status??'Not captured in local MR snapshot',row.frozen_mr_platform_capture_status??'Not captured in frozen local MR snapshot',row.native_mr_link_status??'Not checked against current Octane MR','Not established',row.vin_source,row.date_basis,row.linked_defect_ids.join('; ')])};
 return {headers:['Year','Local MR Snapshots','Identifier Captured Locally','Platform Identifier Not Captured Locally','Matched HK Records'],rows:report.quality.by_year.filter(row=>element('#year').value==='all'||String(row.year)===element('#year').value).map(row=>[row.year,row.source_runs,row.with_vehicle_identifier??0,row.without_vehicle_identifier??0,row.matched_hk_run_records??0])};
}
function nativePlatformNotice(supplement){if(!supplement)return 'No native MR platform supplement has been read for this report. '+format(caseAnalysis.runs.filter(row=>!row.vehicle_value).length)+' MR records have no captured platform identifier.';const coverage=format(supplement.native_records_read)+' / '+format(supplement.report_mr_ids)+' report MR identifiers';return supplement.read_mode==='saved_native_snapshot'?coverage+' were reused from local saved native metadata at '+supplement.fetched_at+'. No new Octane requests were made. Only matching cached MR/testcase records are included.':coverage+' were read from native metadata at '+supplement.fetched_at+'.';}
function render(){const {runs,defects,cases}=selectedData(),isTitle=currentCohort==='hk_title',totals=summarize(runs,defects),aidas=group(runs,defects,'aida_key');element('#metric-runs').textContent=format(totals.executed);element('#metric-tests').textContent=format(totals.tests);element('#metric-defects').textContent=format(totals.defects);element('#metric-aidas').textContent=format(new Set(runs.filter(row=>row.is_executed&&row.aida_code).map(row=>row.aida_code)).size);element('#run-status').textContent=`Passed ${totals.passed} / Failed ${totals.failed} / Attention ${totals.attention} / Planned ${totals.planned}`;element('#metric-runs-label').textContent='HK testcase executions';element('#metric-tests-label').textContent='Unique executed testcases';element('#metric-tests-note').textContent=`${caseAnalysis.cases.length} locally observed testcase IDs`;element('#metric-third-label').textContent='HK platform defects';element('#metric-third-note').textContent=`Separate platform evidence; ${totals.linkedDefects} case-linked IDs`;element('#metric-fourth-label').textContent='AIDA codes with executions';element('#metric-fourth-note').textContent='Not a coverage percentage';element('#table-scope').textContent=currentView==='Quality'?'Source-wide quality: year scope only; not limited to HK VINs.':'HK testcase executions and HK-platform defects are separate evidence sets. Latest-case filters do not filter the independent defect set.';
 element('#evidence').disabled=isTitle;element('#vin').disabled=isTitle;element('#case-status').disabled=!isTitle;element('#cohort-title').setAttribute('aria-pressed',String(isTitle));element('#cohort-vin').setAttribute('aria-pressed',String(!isTitle));const capture=report.mr_platform_capture_audit??{},nativeSupplement=report.native_mr_platform_enrichment;element('#quality-heading').textContent=nativeSupplement?'Native MR platform identifiers supplemented':isTitle?'HK title intent is not execution location':'MR platform data capture is incomplete';element('#quality-warning').textContent=nativeSupplement?`${nativeSupplement.native_records_read} / ${nativeSupplement.report_mr_ids} frozen-report MR IDs were read from native Octane at ${nativeSupplement.fetched_at}. Missing original local fields did not mean missing MR links. Current identifiers supplement platform classification, not frozen execution results or certified historical link state. Source-wide local snapshots were not rewritten.`:isTitle?`${caseAnalysis.overview.catalog_backed_testcases} catalog-title cases + ${caseAnalysis.overview.run_title_only_testcases} run-title-only cases; ${caseAnalysis.overlap_with_vin_scope.executed_runs} execution records overlap the VIN cohort. Native MR platform links are not live-verified. Native testcase steps and approved market applicability are not available in this local catalog.`:report.quality.by_year.map(row=>`${row.year}: platform identifiers were not captured in ${format(row.without_vehicle_identifier??0)} / ${format(row.source_runs)} local MR snapshots`).join('; ')+'. This is a local collection gap, not proof that native MRs lack platform links or that no testing occurred. Historical MR identifier/relationship backfill is still required.';
 element('#case-status-control').hidden=!isTitle;element('#platform-control').hidden=isTitle;element('#quality-heading').textContent=isTitle?'HK testcase and defect overview':'Rack vs Vehicle: static/dynamic source comparison';element('#quality-warning').textContent=isTitle?`${caseAnalysis.cases.length} locally observed HK testcase IDs and ${report.defects.length} HK-platform defect IDs are shown together, not as a claimed discovery conversion. ${nativePlatformNotice(nativeSupplement)} Historical link state is not certified.`:`Rack = Static-origin; Vehicle = Dynamic-origin. Vehicle tests may be stationary. ${runs.filter(row=>row.is_executed&&row.actual_environment==='Unknown').length} unclassified executed records are excluded from the Rack/Vehicle comparison; Unknown is not being investigated here. Platform pass rates have different case/software mixes and are not a controlled effectiveness test.`;
 element('#environment-control').hidden=currentView!=='Environment';
 element('#automation-control').hidden=currentView!=='Automation';element('#effort-panel').hidden=currentView!=='Automation'||element('#automation-view').value!=='effort';
 if(currentView==='Automation'){const view=element('#automation-view').value;element('#quality-heading').textContent='Automation opportunity review, not feasibility approval';element('#quality-warning').textContent='Static-origin = bench source; Dynamic-origin = vehicle source. Actual motion remains Unknown, including on vehicles. Recorded platform is not proof of first discovery. Candidates use title signals and observed repeats only; native steps, machine oracles, validated harnesses and measured effort are still required.';element('#table-scope').textContent=view==='sources'?'Selected VIN evidence by recorded platform; dynamic/static are management source labels, not motion states.':view==='gates'||view==='effort'?'Review framework and hypothetical assumptions, not year-filtered measured savings.':'Selected HK-title cases ranked by observed repeated execution volume; every feasibility decision remains Unreviewed.';renderEffort();}
 if(currentView==='Environment'){const view=element('#environment-view').value;element('#table-scope').textContent=view==='capabilities'?'Conditional engineering capability guidance, not measured local results or a year-filtered comparison.':'Full HK testcase execution evidence by platform plus separate HK-platform defect evidence. Static/dynamic are source labels; motion and same-case/software equivalence are not established.';}
 if(!isTitle){const rackRuns=runs.filter(row=>row.actual_environment==='Bench'),vehicleRuns=runs.filter(row=>row.actual_environment==='Vehicle'),rackDefects=defects.filter(row=>row.actual_environment==='Bench'),vehicleDefects=defects.filter(row=>row.actual_environment==='Vehicle');element('#metric-runs-label').textContent='Rack / Static-origin executions';element('#metric-runs').textContent=format(summarize(rackRuns,[]).executed);element('#metric-tests-label').textContent='Vehicle / Dynamic-origin executions';element('#metric-tests').textContent=format(summarize(vehicleRuns,[]).executed);element('#metric-tests-note').textContent='May include stationary vehicle tests';element('#metric-third-label').textContent='Rack-source HK defects';element('#metric-defects').textContent=format(rackDefects.length);element('#metric-third-note').textContent='Recorded platform, not proof of first discovery';element('#metric-fourth-label').textContent='Vehicle-source HK defects';element('#metric-aidas').textContent=format(vehicleDefects.length);element('#metric-fourth-note').textContent='Not a per-execution discovery rate';}
 renderStatusCharts(runs,defects,isTitle);
 const data=tableData(runs,defects,cases),search=element('#search').value.trim().toLowerCase(),rows=data.rows.filter(row=>!search||row.join(' ').toLowerCase().includes(search)),pageSize=Number(element('#page-size').value),pageCount=Math.max(1,Math.ceil(rows.length/pageSize));let page=Math.max(1,Math.min(pageCount,Number(element('#page').value)||1));element('#page').value=page;element('#page').max=pageCount;element('#page-count').textContent='of '+pageCount;element('#row-count').textContent=`${format(rows.length)} ${currentView==='Quality'?'source-quality rows':'matching rows'}`;
 const head=node('tr');for(const header of data.headers)head.append(node('th',header));element('thead').replaceChildren(head);const body=element('tbody');body.replaceChildren();for(const row of rows.slice((page-1)*pageSize,page*pageSize)){const line=node('tr');for(const value of row){const cell=node('td',format(value));if(typeof value==='number')cell.className='number';if(value==='Failed')cell.className='status-failed';if(value==='Passed')cell.className='status-passed';if(value==='Requires Attention')cell.className='status-attention';line.append(cell);}body.append(line);}if(!rows.length){const line=node('tr'),cell=node('td','No matching records');cell.colSpan=data.headers.length;cell.className='empty';line.append(cell);body.append(line);}
}
function setCohort(value){currentCohort=value;if(currentView==='Automation'){if(value==='vin')element('#automation-view').value='sources';else if(element('#automation-view').value==='sources')element('#automation-view').value='candidates';}element('#case-status').value='all';element('#evidence').value='all';element('#vin').value='all';element('#search').value='';element('#page').value=1;for(const tab of element('.tabs').children)tab.setAttribute('aria-selected',String(tab.dataset.view===currentView));render();}
for(const view of views){const button=node('button',view==='Environment'?'Comparison':view);button.dataset.view=view;button.type='button';button.setAttribute('role','tab');button.setAttribute('aria-selected',String(view===currentView));button.addEventListener('click',()=>{currentView=view;if(view==='Automation')setCohort('hk_title');for(const tab of element('.tabs').children)tab.setAttribute('aria-selected',String(tab===button));element('#page').value=1;render();});element('.tabs').append(button);}
element('#cohort-title').addEventListener('click',()=>{currentView='Testcases';setCohort('hk_title');});element('#cohort-vin').addEventListener('click',()=>{currentView='Environment';element('#environment-view').value='comparison';setCohort('vin');});
for(const item of report.v_number_mapping??report.inventory){const option=node('option',(item.v_number||item.vin.slice(-7))+' / '+(item.actual_environment??'Unknown'));option.value=item.vin;element('#vin').append(option);}
for(const selector of ['#year','#case-status','#evidence','#vin','#search','#page-size','#environment-view'])element(selector).addEventListener(selector==='#search'?'input':'change',()=>{element('#page').value=1;render();});element('#page').addEventListener('change',render);
element('#automation-view').addEventListener('change',()=>{const needsVin=element('#automation-view').value==='sources';if(needsVin&&currentCohort!=='vin')setCohort('vin');else if(!needsVin&&currentCohort!=='hk_title')setCohort('hk_title');else{element('#page').value=1;element('#search').value='';render();}});
for(const selector of ['#effort-volume','#effort-manual','#effort-share','#effort-supervision','#effort-build','#effort-maintenance','#effort-validation'])element(selector).addEventListener('input',renderEffort);
element('#source').textContent='Frozen data cutoff: '+report.source.latest_fetched_at.runs+' | V-number rule confirmed 2026-11-02 | '+(report.source.database_matches_frozen_snapshot_at_refresh===false?'Current source differs; frozen evidence preserved':'Classification update only; no source query');
for(const limit of [...report.limitations,...caseAnalysis.limitations,...(caseAnalysis.environment_comparison?.limitations??[]),...automation.limitations,...(capacityInsights?.limits??[])])element('#limits').append(node('li',limit));renderCapacityInsights();render();
</script></body></html>'''


def build_html_payload(payload):
    keys = ("schema_version", "scope", "source", "inventory", "v_number_mapping", "limitations",
            "hk_testcase_analysis", "defects", "automation_analysis", "capacity_insights", "quality",
            "mr_platform_capture_audit", "native_mr_platform_enrichment")
    result = {key: payload[key] for key in keys if key in payload}
    analysis = payload["hk_testcase_analysis"]
    run_fields = ("run_id", "test_id", "test_name", "status", "is_executed", "date", "date_basis", "year", "month",
                  "aida_code", "aida_name", "aida_key", "matched_vins", "vehicle_value", "vin_source", "linked_defect_ids",
                  "software_version", "actual_environment", "v_numbers", "environment_classification_source",
                  "v_number_mapping_sources", "environment_tag_signal", "testing_tool_type", "mr_platform_capture_status",
                  "frozen_mr_platform_capture_status", "native_mr_link_status", "platform_identifier_observed_at",
                  "native_mr_last_modified", "native_platform_lookup_error")
    case_fields = ("test_id", "test_name", "aida_names", "aida_codes", "testcase_scope_basis", "title_variants")
    comparison = analysis["environment_comparison"]
    result["hk_testcase_analysis"] = {
        "scope": analysis["scope"], "overview": analysis["overview"], "limitations": analysis["limitations"],
        "overlap_with_vin_scope": {key: value for key, value in analysis["overlap_with_vin_scope"].items() if key != "run_ids"},
        "cases": [{key: row[key] for key in case_fields if key in row} for row in analysis["cases"]],
        "runs": [{key: row[key] for key in run_fields if key in row} for row in analysis["runs"]],
        "environment_comparison": {key: comparison[key] for key in ("capability_guidance", "limitations")},
    }
    result["defects"] = [{key: value for key, value in row.items() if key not in {"environment_classification_rule", "aida_raw"}}
                         for row in payload["defects"]]
    if "automation_analysis" in payload:
        automation = payload["automation_analysis"]
        fields = ("test_id", "review_route", "candidate_approach", "review_priority_rank")
        result["automation_analysis"] = {**automation, "cases": [
            {key: row[key] for key in fields if key in row} for row in automation["cases"]]}
    return result


def compact_export_tables(payload, sheets):
    analysis = payload["hk_testcase_analysis"]
    region = payload["scope"].get("region", "HK")
    primary_ids = {row["run_id"] for row in analysis["runs"]}
    platform_ids = {row["run_id"] for row in payload["runs"]}
    merged = {}
    business_fields = ("test_id", "test_name", "status", "is_executed", "date", "date_basis", "software_version", "linked_defect_ids")
    for collection in (payload["runs"], analysis["runs"]):
        for row in collection:
            previous = merged.get(row["run_id"])
            if previous and any(previous.get(key) != row.get(key) for key in business_fields):
                raise ValueError("Export MR business facts differ between cohorts: " + row["run_id"])
            merged[row["run_id"]] = {**(previous or {}), **row,
                                      "in_testcase_cohort": row["run_id"] in primary_ids,
                                      "in_vin_scope": row["run_id"] in platform_ids}

    def scoped(collections):
        return [{"cohort": name, **row} for name, records in collections for row in records]

    overview = [{"cohort": name, "metric": key, "value": value}
                for name, metrics in (("testcase", analysis["overview"]), ("regional_platform", payload["overview"]),
                                      ("confirmed_platform", payload["confirmed_overview"]))
                for key, value in metrics.items() if name != "confirmed_platform" or metrics["run_records"] or metrics["defect_count"]]
    quality = [{"scope": name, "year": row.get("year"), "metric": key, "value": value}
               for name, records in (("source", sheets["Source Quality"]), ("report_native_capture", sheets["MR Platform Capture"]))
               for row in records for key, value in row.items() if key != "year"]
    readme = [row for row in sheets["Readme"] if row["field"].startswith(("source_", "scope_", "limitation_")) or row["field"] == "supplemental_read"]
    readme += [{"field": "primary_testcase_cohort", "value": analysis["scope"]["classification"]},
               {"field": "mr_detail_membership", "value": "One row per MR. in_testcase_cohort selects the primary testcase workload; in_vin_scope selects independently attributed regional-platform MRs. These scopes overlap and must not be added."},
               {"field": "shared_column_values", "value": "Data Dictionary kind=constant values apply to every row in the named table; repeated prose is stored once rather than in every record."},
               {"field": "complete_audit_source", "value": payload["scope"].get("region", "HK") + "_Test_Output_2025_2026.json"}]
    compact = {"Readme": readme, "Overview": overview, "HK Cases": analysis["cases"],
               "MR Details": list(merged.values()), "Defects": payload["defects"],
               "Annual": scoped((("testcase", analysis["year_summary"]), ("regional_platform", payload["year_summary"]))),
               "Monthly": scoped((("testcase", analysis["month_summary"]), ("regional_platform", payload["month_summary"]))),
               "AIDA": scoped((("testcase", analysis["aida_summary"]), ("regional_platform", payload["aida_summary"]))),
               "Platforms": payload["platform_summary"],
               "Rack vs Vehicle": scoped((("testcase", sheets["Rack vs Vehicle"]), ("regional_platform", payload["platform_type_summary"]))),
               "Rack Vehicle AIDA": scoped((("testcase", sheets["Rack Vehicle AIDA"]), ("regional_platform", payload.get("platform_type_aida_summary", [])))),
               "V-number Mapping": sheets["V-number Mapping"],
               "Unknown Platforms": sheets["Unknown Platforms"], "Automation Candidates": sheets["Automation Candidates"],
               "Automation Routes": sheets["Automation Routes"], "Automation Gates": sheets["Automation Gates"],
               "HK Capacity Insights": sheets["HK Capacity Insights"], "2027 Capacity Scenarios": sheets["2027 Capacity Scenarios"],
               "Capacity Pilot Queue": sheets["Capacity Pilot Queue"], "Capacity Assumptions": sheets["Capacity Assumptions"],
               "Platform Capabilities": sheets["Platform Capabilities"], "Data Quality": quality}
    definitions = [{"kind": "definition", "table": "MR Details", "column": column, "value": meaning, "applies_to_rows": len(merged)}
                   for column, meaning in (("in_testcase_cohort", "True selects primary testcase-to-MR workload"),
                                           ("in_vin_scope", "True selects the separate regional VIN/platform cohort"))]
    guidance = ("environment_classification_rule", "native_field_scope", "native_mr_platform_field", "historical_mr_link_verified",
                "actual_vehicle_motion", "screening_basis", "required_evidence", "review_status", "automation_feasible", "approved_for_full_automation")
    for title, records in list(compact.items()):
        if not records:
            continue
        fields = guidance + (("defect_count", "phase06_defects", "phase09_defects", "other_phase_defects", "first_defect_date", "last_defect_date") if title == "HK Cases" else ())
        shared = {}
        for field in fields:
            if all(field in row for row in records):
                value = records[0][field]
                if all(row[field] == value for row in records):
                    shared[field] = value
                    definitions.append({"kind": "constant", "table": title.replace("HK", region), "column": field, "value": value, "applies_to_rows": len(records)})
        compact[title] = [{key: value for key, value in row.items() if key not in shared} for row in records]
    compact["Data Dictionary"] = definitions
    csv_sheets = {"testcases": "HK Cases", "executions": "MR Details", "defects": "Defects", "annual_summary": "Annual",
                  "monthly_summary": "Monthly", "aida_summary": "AIDA", "platforms": "Platforms", "rack_vs_vehicle": "Rack vs Vehicle",
                  "rack_vehicle_aida": "Rack Vehicle AIDA", "v_number_mapping": "V-number Mapping", "unknown_platform_followup": "Unknown Platforms",
                  "automation_candidates": "Automation Candidates", "automation_routes": "Automation Routes",
                  "capacity_scenarios": "2027 Capacity Scenarios", "capacity_pilot_queue": "Capacity Pilot Queue", "data_dictionary": "Data Dictionary"}
    payload["export_layout"] = {"version": 1, "mr_detail_records": len(merged), "primary_mr_records": len(primary_ids),
                                 "regional_platform_mr_records": len(platform_ids), "csv_files": [name + ".csv" for name in csv_sheets],
                                 "workbook_sheets": [title.replace("HK", region) for title in compact], "shared_column_values": definitions}
    return compact, {name: compact[title] for name, title in csv_sheets.items()}


def write_report(payload, output_dir):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    region = payload["scope"].get("region", "HK")
    basename = region + "_Test_Output_2025_2026"
    paths = {"json": output / (basename + ".json"), "excel": output / (basename + ".xlsx"),
             "html": output / (basename + ".html"), "markdown": output / "ANALYSIS.md"}
    previous_markdown = paths["markdown"].read_text(encoding="utf-8") if paths["markdown"].exists() else ""
    capture = _mr_capture_audit(payload)
    payload["report_views"] = build_report_views(payload)
    followup = unknown_platform_followup(payload["hk_testcase_analysis"]["runs"])
    payload["unknown_platform_followup"] = followup
    payload["mr_platform_capture_audit"] = capture
    automation = build_automation_analysis(payload)
    payload["automation_analysis"] = automation
    payload["capacity_insights"] = build_capacity_insights(payload)
    payload["schema_version"] = "5.2" if region == "TW" else "5.3" if region == "CN" else "5.0"
    payload["testcases"] = _testcase_rows(payload)
    readme = [{"field": key, "value": value} for key, value in payload["overview"].items()]
    readme += [{"field": ("confirmed_six_" if region == "HK" else "user_confirmed_") + key, "value": value} for key, value in payload["confirmed_overview"].items()]
    readme += [{"field": "source_" + key, "value": value} for key, value in payload["source"].items()]
    readme += [{"field": "scope_" + key, "value": value} for key, value in payload["scope"].items()]
    if region == "TW":
        readme += [{"field": "primary_testcase_cohort", "value": payload["hk_testcase_analysis"]["scope"]["classification"]},
                   {"field": "tw_title_testcase_ids", "value": len(payload["hk_testcase_analysis"]["cases"])},
                   {"field": "tw_title_related_mrs", "value": len(payload["hk_testcase_analysis"]["runs"])},
                   {"field": "overview_is_strict_platform_subset_not_full_testcase_cohort", "value": True}]
    if region == "CN":
        readme += [{"field": "primary_testcase_cohort", "value": payload["hk_testcase_analysis"]["scope"]["classification"]},
                   {"field": "non_hk_tw_testcase_ids", "value": len(payload["hk_testcase_analysis"]["cases"])},
                   {"field": "non_hk_tw_related_mrs", "value": len(payload["hk_testcase_analysis"]["runs"])},
                   {"field": "hk_tw_testcase_ids_excluded", "value": len(payload["hk_testcase_analysis"].get("excluded_regional_testcase_ids", []))},
                   {"field": "overview_is_strict_platform_subset_not_full_testcase_cohort", "value": True}]
    readme += [{"field": "supplemental_read", "value": payload.get("supplemental_read", {"status": "local_only"})}]
    readme += [{"field": "limitation_" + str(index + 1), "value": value} for index, value in enumerate(payload["limitations"])]
    sheets = {
        "Readme": readme, "Annual": payload["year_summary"], "Confirmed Annual": payload["confirmed_year_summary"],
        "Monthly": payload["month_summary"], "AIDA": payload["aida_summary"], "Platforms": payload["platform_summary"],
        "Testcases": payload["testcases"], "Runs": payload["runs"], "Defects": payload["defects"],
        "Source Quality": payload["quality"]["by_year"], "Linked Only Excluded": payload["linked_only_runs_excluded"],
        "Market Only Excluded": payload["legacy_market_only_defects_excluded"],
    }
    report_views = payload["report_views"]
    sheets.update({"HK Overview": [{"metric": key, "value": value} for key, value in report_views["hk_overview"].items()],
                   "Rack vs Vehicle": report_views["platform_comparison"]["summary"],
                   "Rack Vehicle AIDA": report_views["platform_comparison"]["aida_summary"]})
    distribution_rows = [{"dimension": dimension, "value": value, "defect_count": count}
                         for dimension, counts in payload["defect_distributions"].items() for value, count in counts.items()]
    sheets["Defect Distributions"] = distribution_rows
    analysis = payload.get("hk_testcase_analysis")
    if analysis:
        case_results = [{"basis": basis, "status": status, "testcase_count": count}
                        for basis in ("latest_executed_status_counts", "latest_record_status_counts")
                        for status, count in analysis[basis].items()]
        sheets.update({
            "HK Case Overview": [{"metric": key, "value": value} for key, value in analysis["overview"].items()]
                + [{"metric": "overlap_with_vin_" + key, "value": value} for key, value in analysis["overlap_with_vin_scope"].items()]
                + [{"metric": "limitation", "value": value} for value in analysis["limitations"]],
            "HK Cases": analysis["cases"], "HK Case Runs": analysis["runs"],
            "HK Case Annual": analysis["year_summary"], "HK Case Monthly": analysis["month_summary"],
            "HK Case AIDA": analysis["aida_summary"], "HK Case Results": case_results,
        })
        comparison = analysis["environment_comparison"]
        environment_readiness = [{"metric": key, "value": comparison[key]} for key in (
            "actual_comparison_ready", "classification_available", "actual_environment_unknown_records", "actual_bench_executions",
            "actual_vehicle_executions", "matched_case_version_comparison")]
        environment_readiness += [{"metric": "required_evidence", "value": value} for value in comparison["required_evidence"]]
        environment_readiness += [{"metric": "limitation", "value": value} for value in comparison["limitations"]]
        sheets.update({"Environment Readiness": environment_readiness, "Environment Signals": comparison["signal_summary"],
                       "Environment AIDA": comparison["aida_summary"], "Environment Evidence": comparison["evidence"],
                       "Platform Capabilities": comparison["capability_guidance"],
                       "Environment Classified": comparison["actual_summary"],
                       "Platform Type Totals": payload["platform_type_summary"],
                       "Platform Type AIDA": payload.get("platform_type_aida_summary", []),
                       "V-number Mapping": payload.get("v_number_mapping", payload["inventory"])})
    effort_rows = [{"metric": key, "value": value} for key, value in automation["effort_scenario"].items()]
    effort_rows += [{"metric": "boundary", "value": value} for value in automation["limitations"]]
    sheets.update({"Automation Candidates": automation["cases"], "Automation Routes": automation["route_summary"],
                   "Automation Gates": automation["review_gates"], "Automation Sources": automation["source_summary"],
                   "Effort Scenario": effort_rows})
    sheets["MR Platform Capture"] = capture["summary"]
    sheets["Unknown Platforms"] = followup
    insights = payload["capacity_insights"]
    sheets.update({"HK Capacity Insights": insights["facts"],
                   "2027 Capacity Scenarios": insights["scenarios"],
                   "Capacity Pilot Queue": insights["pilot_queue"],
                   "Capacity Assumptions": [{"metric": key, "value": value} for key, value in insights["planning_baseline"].items()]
                       + [{"metric": "person_capacity_hours_assumption", "value": insights["person_capacity_hours_assumption"]}]
                       + [{"metric": "limit", "value": value} for value in insights["limits"]]})
    sheets, csv_tables = compact_export_tables(payload, sheets)
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, records in sheets.items():
        sheet = workbook.create_sheet(title.replace("HK", region))
        columns = list(dict.fromkeys(key for row in records for key in row)) or ["no_records"]
        sheet.append(columns)
        for record in records:
            sheet.append([_spreadsheet_safe(record.get(column)) for column in columns])
        for cell in sheet[1]:
            cell.font = Font(name="Aptos", color="FFFFFF", bold=True)
            cell.fill = PatternFill("solid", fgColor="176C63")
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for index, name in enumerate(columns, 1):
            longest = max([len(name)] + [len(str(_flat(row.get(name)))) for row in records[:200]])
            sheet.column_dimensions[get_column_letter(index)].width = min(72, max(14, longest + 2))
        sheet.sheet_view.zoomScale = 85
    workbook.save(paths["excel"])
    for name, records in csv_tables.items():
        columns = list(dict.fromkeys(key for row in records for key in row)) or ["no_records"]
        with (output / (name + ".csv")).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows({column: _spreadsheet_safe(row.get(column)) for column in columns} for row in records)
    paths["json"].write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    aliases = {"environment_evidence": "executions", "platform_type_totals": "rack_vs_vehicle", "platform_type_aida": "rack_vehicle_aida"}
    for prefix in ("hk_", "tw_", "cn_"):
        aliases.update({prefix + "case_annual": "annual_summary", prefix + "case_monthly": "monthly_summary",
                        prefix + "case_aida": "aida_summary", prefix + "case_results": "testcases",
                        prefix + "capacity_scenarios": "capacity_scenarios", prefix + "capacity_pilot_queue": "capacity_pilot_queue"})
    for prefix in ("hk_marked_", "tw_regional_", "cn_regional_"):
        aliases.update({prefix + "testcases": "testcases", prefix + "executions": "executions"})
    def compact_link(match):
        name = Path(match.group(1)).stem
        target = aliases.get(name, name)
        return "](" + (target + ".csv" if target in csv_tables else paths["json"].name) + ")"

    markdown = re.sub(r"\]\(([^()\s]+\.csv)\)", compact_link, _markdown(payload))
    layout_note = ("## Compact Export Layout\n\n"
                   "- `executions.csv` and **MR Details** contain each MR once. Filter `in_testcase_cohort` for the primary workload or `in_vin_scope` for the independent platform subset; do not add overlapping scopes.\n"
                   "- **Data Dictionary** stores repeated constant column values once; **Annual**, **Monthly**, **AIDA** and platform comparisons identify their cohort explicitly.\n"
                   "- The full frozen JSON retains all original audit collections. HTML carries only data used by its views; obsolete duplicate CSVs are no longer published.\n\n")
    markdown = markdown.replace("\n## ", "\n" + layout_note + "## ", 1)
    preserved = re.findall(r"(?ms)^## Native (?:Testcase Body Supplement|MR Platform Recovery)[^\n]*\n.*?(?=^## |\Z)", previous_markdown)
    if preserved:
        sections = "\n\n".join(section.strip() for section in preserved) + "\n\n"
        marker = "## Evidence and Dates"
        markdown = markdown.replace(marker, sections + marker, 1) if marker in markdown else markdown.rstrip() + "\n\n" + sections
    paths["markdown"].write_text(markdown, encoding="utf-8")
    embedded = json.dumps(build_html_payload(payload), ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = HTML_TEMPLATE
    if region != "HK":
        template = template.replace("HK_Test_Output_", region + "_Test_Output_")
        if region == "CN":
            template = template.replace("HK title intent is not execution location", "Platform region is not historical execution location")
            template = template.replace("HK-title", "__PRIMARY_CASE_SCOPE__").replace("HK title", "__PRIMARY_CASE_SCOPE__")
        template = template.replace("6 confirmed + 4 legacy candidates", "Regional inventory with source evidence")
        template = template.replace("Confirmed six vehicles", "User-confirmed platforms").replace("Legacy four candidates", "Legacy mapping candidates")
        template = template.replace("HK", REGION_LABELS[region])
        template = template.replace("__PRIMARY_CASE_SCOPE__", "DTSV China non-HK/TW")
    paths["html"].write_text(template.replace("__REPORT_DATA__", embedded), encoding="utf-8")
    retired = {"source_quality", "defect_distributions", "linked_only_runs_excluded", "legacy_market_only_defects_excluded",
               "environment_readiness", "environment_signals", "environment_aida", "environment_evidence", "platform_capabilities",
               "environment_classified", "platform_type_totals", "platform_type_aida", "automation_gates", "automation_sources",
               "effort_scenario", "mr_platform_capture"}
    retired.update({prefix + suffix for prefix in ("hk_marked_", "tw_regional_", "cn_regional_") for suffix in ("testcases", "executions")})
    retired.update({prefix + suffix for prefix in ("hk_", "tw_", "cn_") for suffix in ("case_annual", "case_monthly", "case_aida", "case_results", "capacity_scenarios", "capacity_pilot_queue")})
    for name in retired:
        path = output / (name + ".csv")
        if name not in csv_tables and path.is_file() and not path.is_symlink():
            path.unlink()
    return paths


def enrich_testcase_csv(csv_path, native_snapshot):
    path = Path(csv_path)
    fetched_at = native_snapshot.get("fetched_at")
    if _datetime(fetched_at) is None:
        raise ValueError("The native testcase snapshot needs a valid fetched_at timestamp")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        rows = list(reader)
    if "test_id" not in columns:
        raise ValueError("The testcase CSV has no test_id column")
    identifiers = []
    for row in rows:
        value = _text(row.get("test_id"))
        if re.fullmatch(r"[0-9]+", value):
            identifier = value
        else:
            parameters = parse_qs(urlsplit(value).fragment.partition("?")[2])
            candidates = parameters.get("id", [])
            identifier = candidates[0] if len(candidates) == 1 else ""
        if not re.fullmatch(r"[0-9]+", identifier):
            raise ValueError("The testcase CSV contains an invalid or ambiguous Octane ID")
        identifiers.append(identifier)
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("The testcase CSV contains duplicate Octane IDs")
    native_by_id = {}
    for record in native_snapshot.get("records", []):
        test = record["test"]
        identifier = _text(test.get("id"))
        if identifier in native_by_id:
            raise ValueError("The native testcase snapshot contains duplicate Octane IDs")
        script = record.get("script")
        if (test.get("subtype") != "test_manual" or not isinstance(script, dict)
                or not isinstance(script.get("script"), str)
                or not isinstance(test.get("description") or "", str)):
            raise ValueError("The native testcase snapshot has an incomplete manual-test body")
        native_by_id[identifier] = record
    errors_by_id = {}
    for error in native_snapshot.get("errors", []):
        identifier = _text(error.get("id"))
        if (identifier in errors_by_id or identifier in native_by_id
                or error.get("endpoint") not in {f"tests/{identifier}", f"tests/{identifier}/script"}
                or not _text(error.get("error_type"))):
            raise ValueError("The native testcase snapshot contains invalid or duplicate read errors")
        errors_by_id[identifier] = error
    if set(native_by_id) | set(errors_by_id) != set(identifiers):
        raise ValueError("The native testcase snapshot does not exactly match the CSV testcase IDs")
    body_columns = ("mt_description", "mt_steps", "mt_body_source", "mt_body_fetched_at",
                    "mt_test_last_modified", "mt_script_last_modified", "mt_script_test_version",
                    "mt_script_version_stamp", "mt_steps_status", "mt_body_http_status", "mt_body_error_type")
    columns.extend(column for column in body_columns if column not in columns)
    for row, identifier in zip(rows, identifiers):
        for column in body_columns:
            row.setdefault(column, "")
        values = {"mt_body_source": f"Octane /tests/{identifier}; /tests/{identifier}/script"}
        if identifier in errors_by_id:
            error = errors_by_id[identifier]
            values.update({"mt_steps_status": "native_unavailable", "mt_body_http_status": error.get("http_status"),
                           "mt_body_error_type": error["error_type"],
                           "mt_body_fetched_at": error.get("script_checked_at") or fetched_at})
        else:
            record = native_by_id[identifier]
            test, script = record["test"], record["script"]
            values.update({"mt_description": test.get("description") or "", "mt_steps": script["script"],
                           "mt_body_fetched_at": record.get("fetched_at") or fetched_at,
                           "mt_test_last_modified": test.get("last_modified", ""),
                           "mt_script_last_modified": script.get("last_modified", ""),
                           "mt_script_test_version": script.get("test_version", ""),
                           "mt_script_version_stamp": script.get("version_stamp", ""),
                           "mt_steps_status": "available" if script["script"].strip() else "native_empty",
                           "mt_body_http_status": 200, "mt_body_error_type": ""})
        row.update({column: _spreadsheet_safe(value) for column, value in values.items()})
    temporary_path = None
    try:
        with NamedTemporaryFile("w", dir=path.parent, suffix=".csv.tmp", encoding="utf-8-sig",
                                newline="", delete=False) as handle:
            temporary_path = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return {"row_count": len(rows), "scripts_populated": sum(row["mt_steps_status"] == "available" for row in rows),
            "descriptions_populated": sum(bool(row["mt_description"].strip()) for row in rows),
            "native_empty_scripts": sum(row["mt_steps_status"] == "native_empty" for row in rows),
            "unavailable_bodies": len(errors_by_id), "complete": not errors_by_id,
            "fetched_at": fetched_at, "historical_execution_versions_included": False,
            "source_sync": False, "octane_writeback": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="database/source/qgate_raw.db")
    parser.add_argument("--region", choices=("HK", "TW", "CN"), default="HK", help="CN means positive mainland mappings excluding HK/TW platform associations")
    parser.add_argument("--team-name", default="DTSV_China", help="Exact run team and defect finder team for a new local build")
    parser.add_argument("--output", help="New builds default to a dated region folder; frozen HK refresh defaults to the existing reviewed folder")
    parser.add_argument("--market-mapping", help="Repository-owned full-VIN market mapping JSON; explicit user confirmations take priority")
    parser.add_argument("--supplement-status", choices=("local_only", "authentication_required"), default="local_only")
    publication_mode = parser.add_mutually_exclusive_group()
    publication_mode.add_argument("--refresh-platform-types", action="store_true", help="Reclassify the existing frozen JSON without querying the source database")
    publication_mode.add_argument("--render-only", action="store_true", help="Rebuild compact artifacts from frozen JSON without source reads or platform reclassification")
    parser.add_argument("--fleet-snapshot", default="exports/fleem/dtsv-china-active.json")
    parser.add_argument("--supplemental-fleet-snapshot", action="append", default=[], help="Additional Fleem VIN/V-number snapshot; may be repeated and is retained for later frozen classification refreshes")
    mr_snapshot_inputs = parser.add_mutually_exclusive_group()
    mr_snapshot_inputs.add_argument("--mr-platform-snapshot", help="Supplement existing report MRs with separately fetched native identifier metadata")
    mr_snapshot_inputs.add_argument("--cached-mr-platform-snapshot", help="Reuse only matching MR/testcase records from an existing local native snapshot without network access")
    parser.add_argument("--testcase-csv", help="Supplement only this existing CSV from a saved native testcase-body snapshot")
    parser.add_argument("--testcase-body-snapshot", help="Native snapshot containing fetched_at and records with test and script responses")
    args = parser.parse_args()
    if args.testcase_csv or args.testcase_body_snapshot:
        if not (args.testcase_csv and args.testcase_body_snapshot):
            parser.error("--testcase-csv and --testcase-body-snapshot must be supplied together")
        native_snapshot = json.loads(Path(args.testcase_body_snapshot).read_text(encoding="utf-8"))
        summary = enrich_testcase_csv(args.testcase_csv, native_snapshot)
        print(json.dumps({"output": args.testcase_csv, **summary}, ensure_ascii=True, indent=2))
        return
    if args.output is None:
        if (args.refresh_platform_types or args.render_only) and args.region != "HK":
            parser.error("--output is required to refresh a frozen TW/CN report")
        args.output = ("exports/hk_platform_analysis/20261030" if args.refresh_platform_types or args.render_only else
                       "exports/" + args.region.lower() + "_platform_analysis/" + datetime.now(timezone.utc).strftime("%Y%m%d"))
    if args.refresh_platform_types or args.render_only:
        snapshot = Path(args.output) / (args.region + "_Test_Output_2025_2026.json")
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        if payload["scope"].get("region", "HK") != args.region:
            parser.error("Frozen report region does not match --region; a new local build is required")
        expected_case_cohorts = {"TW": "explicit_tw_title", "CN": "dtsv_china_non_hk_tw_testcase"}
        if args.region in expected_case_cohorts and payload["hk_testcase_analysis"]["scope"].get("classification") != expected_case_cohorts[args.region]:
            parser.error("Legacy " + args.region + " platform-only snapshot requires a new local build to restore testcase-to-MR relations; do not use --refresh-platform-types")
    else:
        payload = build_report(args.db, team_name=args.team_name, region=args.region,
                               fleet_snapshot=args.fleet_snapshot, market_mapping=args.market_mapping)
        payload["supplemental_read"] = {"status": args.supplement_status, "upstream_updates": False,
                                        "source_sync": False, "new_login_triggered": False}
        if args.supplement_status == "authentication_required":
            payload["supplemental_read"].update({"cookie_probe_http_status": 401, "keeper_existing_sessions": 0})
    if args.render_only:
        if args.mr_platform_snapshot or args.cached_mr_platform_snapshot or args.supplemental_fleet_snapshot:
            parser.error("--render-only cannot be combined with native MR or Fleem mapping supplements")
        payload["publication_refresh"] = {"input_mode": "frozen_render_only", "rendered_at": datetime.now(timezone.utc).isoformat(),
                                           "source_sync": False, "source_database_updated": False, "platform_reclassified": False}
        paths = write_report(payload, args.output)
        print(json.dumps({"outputs": {name: str(path) for name, path in paths.items()}, "region": args.region,
                          "source_queried_for_rendering": False, "source_modified_by_rendering": False,
                          "csv_files": len(payload["export_layout"]["csv_files"]), "workbook_sheets": len(payload["export_layout"]["workbook_sheets"]),
                          "html_bytes": paths["html"].stat().st_size}, ensure_ascii=True, indent=2))
        return
    fleet_path = Path(args.fleet_snapshot)
    previous_fleet_supplements = payload.get("classification_refresh", {}).get("supplemental_fleet_snapshots", [])
    fleet_supplements = list(dict.fromkeys([*previous_fleet_supplements, *args.supplemental_fleet_snapshot]))
    payload = refresh_platform_types(payload, load_v_number_mapping(fleet_path if fleet_path.exists() else None,
                                                                   supplemental_fleet_paths=fleet_supplements))
    if args.mr_platform_snapshot:
        native_snapshot = json.loads(Path(args.mr_platform_snapshot).read_text(encoding="utf-8"))
        payload = enrich_native_mr_platform_links(payload, native_snapshot)
    if args.cached_mr_platform_snapshot:
        cached_path = Path(args.cached_mr_platform_snapshot)
        cached_bytes = cached_path.read_bytes()
        cached_snapshot = json.loads(cached_bytes)
        cached_snapshot["snapshot_file"] = str(cached_path)
        cached_snapshot["source_snapshot_sha256"] = hashlib.sha256(cached_bytes).hexdigest()
        payload = enrich_native_mr_platform_links(payload, select_saved_mr_platform_snapshot(payload, cached_snapshot))
    payload["classification_refresh"]["supplemental_fleet_snapshots"] = fleet_supplements
    payload["classification_refresh"]["input_mode"] = "frozen_existing_report" if args.refresh_platform_types else "newly_built_report"
    paths = write_report(payload, args.output)
    print(json.dumps({"outputs": {name: str(path) for name, path in paths.items()},
                      "region": payload["scope"].get("region", "HK"), "team_name": payload["scope"].get("team_name"),
                      "overview": payload["overview"], "confirmed_overview": payload["confirmed_overview"],
                      "platform_type_summary": payload["platform_type_summary"],
                      "source_unchanged_at_original_data_build": payload["source"].get("unchanged_at_data_build"),
                      "database_matches_frozen_snapshot_at_refresh": payload["source"]["database_matches_frozen_snapshot_at_refresh"],
                      "source_queried_for_classification": False, "source_modified_by_classification": False}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()