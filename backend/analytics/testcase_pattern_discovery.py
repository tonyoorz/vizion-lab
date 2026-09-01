"""Read-only, context-safe discovery of reusable defect testcase patterns."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from html import unescape
import hashlib
import re
from typing import Any

import numpy as np


DEFAULT_SIMILARITY_THRESHOLD = 0.84
PATTERN_DISCOVERY_POLICY_VERSION = "2026-09-01.2"
_MIN_REPRODUCTION_EVIDENCE_CHARS = 40
_UNKNOWN_CONTEXT_THRESHOLD_INCREMENT = 0.05
_EMBEDDING_DESCRIPTION_MAX_CHARS = 2400
_EMBEDDING_COMMENTS_MAX_CHARS = 1200
_MAX_AUTOMATIC_PATTERN_MEMBERS = 20
_FUNCTIONAL_ANCHOR_STOPWORDS = frozenset(
    {
        "a", "an", "and", "app", "application", "are", "as", "at", "be", "been", "before",
        "but", "by", "can", "cannot", "case", "content", "could", "defect", "did", "display",
        "do", "does", "during", "empty", "error", "fail", "failed", "failure", "feature", "for",
        "from", "function", "has", "have", "in", "interface", "into", "is", "issue", "it", "its",
        "lost", "missing", "no", "not", "of", "on", "or", "page", "problem", "rse", "rsu", "screen",
        "setting", "settings", "show", "showing", "shown", "shows", "system", "test", "that", "the",
        "there", "this", "timeout", "to", "under", "unable", "unexpected", "vehicle", "was", "when",
        "where", "while", "will", "with", "without", "would", "wrong",
    }
)
_FUNCTIONAL_ANCHOR_PATTERN = re.compile(
    r"\b(?:0x[0-9a-f]+|[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*)\b",
    re.IGNORECASE,
)
_CJK_SEQUENCE_PATTERN = re.compile(r"[\u3400-\u9fff]+")


def canonical_defect_id(value: Any) -> str:
    """Normalize bare, D-prefixed, and D-dash-prefixed Octane defect IDs."""
    text = str(value or "").strip()
    match = re.fullmatch(r"(?:d\s*-?\s*)?(\d+)", text, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _clean_text(value: Any) -> str:
    text = unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()


def _flatten_comments(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, Mapping):
        preferred = value.get("text") or value.get("content") or value.get("body")
        if preferred is not None:
            return _flatten_comments(preferred)
        return " ".join(_flatten_comments(item) for item in value.values())
    if isinstance(value, Sequence):
        return " ".join(_flatten_comments(item) for item in value)
    return _clean_text(value)


def _normalized_context_value(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _display_value(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _context_key(record: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        _normalized_context_value(record.get("project")) or "unknown-project",
        _normalized_context_value(record.get("assigned_ecu")) or "unknown-ecu",
        _normalized_context_value(record.get("lead_model")) or "unknown-model",
    )


def _requires_manual_review(context_key: tuple[str, str, str]) -> bool:
    return any(value.startswith("unknown-") for value in context_key)


def _raw_functional_anchor_tokens(value: Any) -> set[str]:
    text = _clean_text(value).casefold()
    tokens: set[str] = set()
    for match in _FUNCTIONAL_ANCHOR_PATTERN.finditer(text):
        token = match.group(0)
        if token in _FUNCTIONAL_ANCHOR_STOPWORDS:
            continue
        tokens.add(token)
        if "-" in token or "_" in token:
            tokens.update(
                part
                for part in re.split(r"[-_]", token)
                if len(part) >= 3 and part not in _FUNCTIONAL_ANCHOR_STOPWORDS
            )
    for match in _CJK_SEQUENCE_PATTERN.finditer(text):
        sequence = match.group(0)
        tokens.update(sequence[index : index + 2] for index in range(max(len(sequence) - 1, 0)))
    return tokens


def _functional_anchor_tokens(record: Mapping[str, Any]) -> set[str]:
    context_tokens: set[str] = set()
    for field_name in ("project", "assigned_ecu", "lead_model"):
        context_tokens.update(_raw_functional_anchor_tokens(record.get(field_name)))
    return _raw_functional_anchor_tokens(record.get("name")) - context_tokens


def _anchors_are_compatible(left: set[str], right: set[str]) -> bool:
    shared = left & right
    if len(shared) >= 2:
        return True
    return any(
        token.startswith("0x")
        or "-" in token
        or "_" in token
        or (len(token) >= 5 and any(char.isalpha() for char in token) and any(char.isdigit() for char in token))
        for token in shared
    )


def _evidence_text(record: Mapping[str, Any]) -> tuple[str, str, str]:
    title = _clean_text(record.get("name"))
    description = _clean_text(record.get("description"))
    comments = _flatten_comments(record.get("comments"))
    reproduction_evidence = " ".join(part for part in (description, comments) if part)
    text = " ".join(
        part
        for part in (
            title,
            title,
            description[:_EMBEDDING_DESCRIPTION_MAX_CHARS],
            comments[:_EMBEDDING_COMMENTS_MAX_CHARS],
        )
        if part
    )
    return text, reproduction_evidence, description[:500]


def _member_payload(record: Mapping[str, Any], similarity: float) -> dict[str, Any]:
    return {
        "defectId": record["defect_id"],
        "name": record["name"],
        "project": _display_value(record.get("project")),
        "assignedEcu": _display_value(record.get("assigned_ecu")),
        "leadModel": _display_value(record.get("lead_model")),
        "softwareVersion": _display_value(record.get("software_version")),
        "detectedInRelease": _display_value(record.get("detected_in_release")),
        "similarityToRepresentative": round(float(similarity), 3),
    }


def _pattern_id(context_key: tuple[str, str, str], member_ids: list[str]) -> str:
    value = "|".join((*context_key, *member_ids)).encode("utf-8")
    return f"pattern-{hashlib.sha256(value).hexdigest()[:12]}"


def _unique_values(records: Sequence[Mapping[str, Any]], field_name: str) -> list[str]:
    values = {
        value
        for record in records
        if (value := _display_value(record.get(field_name)))
    }
    return sorted(values, key=str.casefold)


def _normalize_embeddings(embeddings: Any, *, expected_rows: int) -> np.ndarray:
    matrix = np.asarray(embeddings, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[0] != expected_rows or matrix.shape[1] == 0:
        raise ValueError("embedding encoder returned an invalid matrix")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.maximum(norms, np.finfo(np.float32).eps)


def _cluster_bucket(
    records: Sequence[dict[str, Any]],
    embeddings: np.ndarray,
    indices: list[int],
    similarity_threshold: float,
) -> list[list[int]]:
    remaining = set(indices)
    clusters: list[list[int]] = []

    def matching_members(representative_index: int, candidates: list[int]) -> list[int]:
        similarities = embeddings[representative_index] @ embeddings[candidates].T
        return [
            candidate
            for candidate, score in zip(candidates, similarities)
            if score >= similarity_threshold
            and (
                candidate == representative_index
                or _anchors_are_compatible(
                    records[representative_index]["functional_anchors"],
                    records[candidate]["functional_anchors"],
                )
            )
        ]

    while remaining:
        candidates = sorted(remaining)
        candidate_scores: list[tuple[int, float, int]] = []
        for index in candidates:
            similarities = embeddings[index] @ embeddings[candidates].T
            members = matching_members(index, candidates)
            member_set = set(members)
            average_similarity = float(
                np.mean([score for candidate, score in zip(candidates, similarities) if candidate in member_set])
            )
            candidate_scores.append((len(members), average_similarity, -records[index]["source_index"]))

        _, _, _, representative_index = max(
            (member_count, average_similarity, source_order, index)
            for (member_count, average_similarity, source_order), index in zip(candidate_scores, candidates)
        )
        similarities = embeddings[representative_index] @ embeddings[candidates].T
        matching_indices = set(matching_members(representative_index, candidates))
        remaining_member_indices = sorted(
            [candidate for candidate in matching_indices if candidate != representative_index],
            key=lambda index: records[index]["source_index"],
        )
        member_indices = [representative_index, *remaining_member_indices]
        clusters.append(member_indices)
        remaining.difference_update(member_indices)

    return clusters


def discover_defect_patterns(
    defects: Sequence[Mapping[str, Any]],
    *,
    encode: Callable[[list[str]], Any],
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> dict[str, Any]:
    """Group evidence-rich defects into context-compatible reusable-test patterns.

    The caller supplies the embedding function so this deterministic policy can be
    tested without a model and reuse the project's existing embedding runtime.
    """
    if not 0 < similarity_threshold <= 1:
        raise ValueError("similarity_threshold must be between 0 and 1")

    eligible: list[dict[str, Any]] = []
    needs_evidence: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    for source_index, source in enumerate(defects):
        defect_id = canonical_defect_id(source.get("defect_id") or source.get("defectId"))
        name = _clean_text(source.get("name"))
        if not defect_id:
            needs_evidence.append({"defectId": "", "name": name, "reason": "invalid_defect_id"})
            continue
        if defect_id in seen_ids:
            continue
        seen_ids.add(defect_id)

        text, reproduction_evidence, description_preview = _evidence_text(source)
        if len(reproduction_evidence) < _MIN_REPRODUCTION_EVIDENCE_CHARS:
            needs_evidence.append(
                {
                    "defectId": defect_id,
                    "name": name,
                    "reason": "missing_reproduction_evidence",
                }
            )
            continue

        eligible.append(
            {
                "defect_id": defect_id,
                "name": name,
                "project": source.get("project"),
                "assigned_ecu": source.get("assigned_ecu"),
                "lead_model": source.get("lead_model"),
                "software_version": source.get("software_version"),
                "detected_in_release": source.get("detected_in_release"),
                "evidence_text": text,
                "description_preview": description_preview,
                "functional_anchors": _functional_anchor_tokens(source),
                "source_index": source_index,
            }
        )

    patterns: list[dict[str, Any]] = []
    if eligible:
        embeddings = _normalize_embeddings(
            encode([record["evidence_text"] for record in eligible]),
            expected_rows=len(eligible),
        )
        buckets: dict[tuple[str, str, str], list[int]] = defaultdict(list)
        for index, record in enumerate(eligible):
            buckets[_context_key(record)].append(index)

        for context_key, indices in sorted(buckets.items()):
            requires_manual_review = _requires_manual_review(context_key)
            bucket_threshold = min(
                1.0,
                similarity_threshold + (_UNKNOWN_CONTEXT_THRESHOLD_INCREMENT if requires_manual_review else 0.0),
            )
            for member_indices in _cluster_bucket(eligible, embeddings, indices, bucket_threshold):
                representative_index = member_indices[0]
                representative = eligible[representative_index]
                members = [eligible[index] for index in member_indices]
                similarities = embeddings[representative_index] @ embeddings[member_indices].T
                member_ids = [member["defect_id"] for member in members]
                common_anchor_terms = sorted(
                    set.intersection(*(member["functional_anchors"] for member in members))
                ) if members else []
                patterns.append(
                    {
                        "patternId": _pattern_id(context_key, member_ids),
                        "confidence": round(float(np.mean(similarities)), 3),
                        "requiresManualReview": (
                            requires_manual_review
                            or len(members) > _MAX_AUTOMATIC_PATTERN_MEMBERS
                            or not all(member["functional_anchors"] for member in members)
                        ),
                        "context": {
                            "project": _display_value(representative.get("project")),
                            "assignedEcu": _display_value(representative.get("assigned_ecu")),
                            "leadModel": _display_value(representative.get("lead_model")),
                        },
                        "representative": _member_payload(representative, 1.0),
                        "members": [
                            _member_payload(member, score)
                            for member, score in zip(members, similarities)
                        ],
                        "parameterMatrix": {
                            "softwareVersions": _unique_values(members, "software_version"),
                            "detectedInReleases": _unique_values(members, "detected_in_release"),
                        },
                        "commonAnchorTerms": common_anchor_terms,
                        "evidencePreview": representative["description_preview"],
                    }
                )

    patterns.sort(
        key=lambda pattern: (
            -len(pattern["members"]),
            pattern["representative"]["defectId"],
        )
    )
    singleton_count = sum(1 for pattern in patterns if len(pattern["members"]) == 1)
    eligible_count = len(eligible)
    return {
        "similarityThreshold": similarity_threshold,
        "patterns": patterns,
        "needs_evidence": needs_evidence,
        "summary": {
            "source_count": len(defects),
            "eligible_count": eligible_count,
            "needs_evidence_count": len(needs_evidence),
            "pattern_count": len(patterns),
            "clustered_member_count": sum(len(pattern["members"]) for pattern in patterns),
            "singleton_pattern_count": singleton_count,
            "estimated_llm_generations_avoided": max(eligible_count - len(patterns), 0),
        },
    }


def _coverage_candidate_payload(candidate: Any, *, source: str) -> dict[str, Any]:
    def value(*field_names: str) -> Any:
        if isinstance(candidate, Mapping):
            for field_name in field_names:
                if candidate.get(field_name) not in {None, ""}:
                    return candidate[field_name]
            return None
        for field_name in field_names:
            candidate_value = getattr(candidate, field_name, None)
            if candidate_value not in {None, ""}:
                return candidate_value
        return None

    payload = {
        "testId": str(value("testId", "test_id", "id") or ""),
        "name": str(value("name", "test_name") or ""),
        "source": source,
    }
    if source == "semantic":
        score = value("score")
        if score is not None:
            payload["score"] = round(float(score), 3)
        for output_name, field_names in (
            ("team", ("team", "scope_team")),
            ("release", ("release", "scope_release")),
            ("subtype", ("subtype", "test_subtype")),
        ):
            candidate_value = value(*field_names)
            if candidate_value is not None:
                payload[output_name] = str(candidate_value)
    return payload


def audit_pattern_coverage(
    patterns: Sequence[Mapping[str, Any]],
    *,
    direct_testcases_by_defect: Mapping[str, Sequence[Mapping[str, Any]]],
    retrieve_similar: Callable[[Mapping[str, Any]], Sequence[Any]],
) -> dict[str, Any]:
    """Return review evidence for existing testcase coverage without deciding coverage.

    Exact defect-to-testcase links and semantic candidates are both advisory. A
    human must still verify that an existing script covers the failure mode and
    compatible execution context before selecting it instead of a new pattern.
    """
    direct_links: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for defect_id, testcases in direct_testcases_by_defect.items():
        normalized_id = canonical_defect_id(defect_id)
        if not normalized_id:
            continue
        direct_links[normalized_id].extend(
            _coverage_candidate_payload(testcase, source="direct")
            for testcase in testcases
            if isinstance(testcase, Mapping)
        )

    audits: list[dict[str, Any]] = []
    direct_linked_member_ids: set[str] = set()
    semantic_candidate_pattern_count = 0
    review_existing_testcase_count = 0
    review_generate_reusable_testcase_count = 0
    requires_manual_review_count = 0

    for pattern in patterns:
        representative = dict(pattern.get("representative") or {})
        direct_member_links: list[dict[str, Any]] = []
        for member in pattern.get("members") or []:
            normalized_id = canonical_defect_id(member.get("defectId") if isinstance(member, Mapping) else None)
            testcases = direct_links.get(normalized_id, [])
            if not testcases:
                continue
            direct_linked_member_ids.add(normalized_id)
            direct_member_links.append({"defectId": normalized_id, "testcases": testcases})

        semantic_candidates = [
            _coverage_candidate_payload(candidate, source="semantic")
            for candidate in retrieve_similar(representative)
        ]
        if semantic_candidates:
            semantic_candidate_pattern_count += 1

        if bool(pattern.get("requiresManualReview")):
            disposition = "requires_manual_review"
            requires_manual_review_count += 1
        elif direct_member_links or semantic_candidates:
            disposition = "review_existing_testcase"
            review_existing_testcase_count += 1
        else:
            disposition = "review_generate_reusable_testcase"
            review_generate_reusable_testcase_count += 1

        audits.append(
            {
                "patternId": str(pattern.get("patternId") or ""),
                "directLinks": direct_member_links,
                "semanticCandidates": semantic_candidates,
                "recommendedDisposition": disposition,
            }
        )

    return {
        "audits": audits,
        "summary": {
            "audited_pattern_count": len(audits),
            "direct_linked_member_count": len(direct_linked_member_ids),
            "semantic_candidate_pattern_count": semantic_candidate_pattern_count,
            "review_existing_testcase_count": review_existing_testcase_count,
            "review_generate_reusable_testcase_count": review_generate_reusable_testcase_count,
            "requires_manual_review_count": requires_manual_review_count,
        },
    }


def encode_defect_evidence(texts: list[str]) -> np.ndarray:
    """Encode pattern evidence with the repository's local sentence-transformer."""
    from backend.duplicate_issue_finder import _get_st_model

    model = _get_st_model()
    if model is None:
        raise RuntimeError("local defect embedding model is unavailable")
    return np.asarray(model.encode(texts, normalize_embeddings=True, show_progress_bar=False), dtype=np.float32)
