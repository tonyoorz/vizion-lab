from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import requests

from backend.analytics.asset_loader import load_mapping_assets
from backend.analytics.processor import _vin_market_map


FLEEM_GRAPHQL_URL = "https://integrate.bmwgroup.net/fleem/graphql"
FLEEM_PLATFORM_EXPORT_VIEW = "platform-EEK_Export"
FLEEM_PLATFORM_EXPORT_QUERY = """
query Fleem_queryPlatforms(
    $query: String!
    $platformStatus: [String]!
    $timezoneOffset: String
    $pagination: Fleem_PageRequest
    $view: String
) {
    Fleem_queryPlatforms(
        query: $query
        platformStatus: $platformStatus
        timezoneOffset: $timezoneOffset
        pagination: $pagination
        view: $view
    ) {
        page {
            currentPage
            pageSize
            total
        }
        platforms
        debug
    }
}
"""
_BROWSER_PLATFORM_SNAPSHOT_SCRIPT = """
async ({ query, platformStatus, timezoneOffset, pageSize, view }) => {
    const bearerToken = localStorage.getItem('token');
    if (!bearerToken) {
        throw new Error('FLEEM browser session does not contain a token');
    }

    const graphqlQuery = `query Fleem_queryPlatforms(
        $query: String!
        $platformStatus: [String]!
        $timezoneOffset: String
        $pagination: Fleem_PageRequest
        $view: String
    ) {
        Fleem_queryPlatforms(
            query: $query
            platformStatus: $platformStatus
            timezoneOffset: $timezoneOffset
            pagination: $pagination
            view: $view
        ) {
            page { currentPage pageSize total }
            platforms
        }
    }`;
    const platforms = [];
    let currentPage = 1;
    let expectedTotal = null;

    for (;;) {
        const response = await fetch('/fleem/graphql', {
            method: 'POST',
            headers: {
                Authorization: `Bearer ${bearerToken}`,
                'Apollo-Require-Preflight': 'true',
                'Content-Type': 'application/json',
                integrate_path_context: 'fleem',
            },
            body: JSON.stringify({
                operationName: 'Fleem_queryPlatforms',
                query: graphqlQuery,
                variables: {
                    query,
                    platformStatus,
                    timezoneOffset,
                    pagination: { currentPage, pageSize },
                    view,
                },
            }),
        });
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(`FLEEM GraphQL request failed with HTTP ${response.status}`);
        }
        if (Array.isArray(payload.errors) && payload.errors.length) {
            throw new Error(`FLEEM GraphQL query failed: ${payload.errors.map((error) => error.message || 'unknown GraphQL error').join('; ')}`);
        }

        const result = payload.data?.Fleem_queryPlatforms;
        const page = result?.page;
        const total = Number(page?.total);
        if (!Number.isInteger(total) || total < 0 || !Array.isArray(result?.platforms)) {
            throw new Error('FLEEM GraphQL response has an invalid platforms payload');
        }
        if (expectedTotal === null) {
            expectedTotal = total;
        } else if (expectedTotal !== total) {
            throw new Error('FLEEM GraphQL page total changed during retrieval');
        }

        platforms.push(...result.platforms);
        if (platforms.length >= total) {
            return {
                page: { currentPage, pageSize, total },
                platforms: platforms.slice(0, total),
            };
        }
        if (!result.platforms.length) {
            throw new Error('FLEEM returned an empty page before the reported total was reached');
        }
        currentPage += 1;
    }
}
"""


class FleemApiError(RuntimeError):
    pass


def _parse_view_url(view_url: str) -> tuple[str, list[str]]:
    parsed_url = urlsplit(str(view_url or "").strip())
    if parsed_url.scheme != "https" or parsed_url.hostname != "integrate.bmwgroup.net":
        raise ValueError("view_url must be an https://integrate.bmwgroup.net FLEEM URL")
    if not parsed_url.path.startswith("/fleem/"):
        raise ValueError("view_url must point to a FLEEM page")

    parameters = parse_qs(parsed_url.query, keep_blank_values=True)
    query = str((parameters.get("iql") or [""])[0]).strip()
    if not query:
        raise ValueError("view_url must include a non-empty iql parameter")

    platform_status = [
        status.strip()
        for raw_value in parameters.get("platformStatus") or []
        for status in str(raw_value).split(",")
        if status.strip()
    ]
    if not platform_status:
        raise ValueError("view_url must include at least one platformStatus parameter")
    return query, platform_status


def _build_headers(bearer_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {bearer_token}",
        "Apollo-Require-Preflight": "true",
        "Content-Type": "application/json",
        "integrate_path_context": "fleem",
    }


def _project_market_classification(platforms: list[dict[str, Any]]) -> dict[str, Any]:
    market_mapping = _vin_market_map(load_mapping_assets().vin_project_rows)
    market_counts: Counter[str] = Counter()
    classified_platforms: list[dict[str, str]] = []

    for platform in platforms:
        ids = platform.get("ids")
        vin = str(ids.get("vin") or "").strip() if isinstance(ids, Mapping) else ""
        matched_vin_prefix = ""
        market = ""
        for vin_prefix, candidate_market in market_mapping.items():
            if vin.startswith(vin_prefix):
                matched_vin_prefix = vin_prefix
                market = candidate_market
                break
        market_counts[market or "UNMAPPED"] += 1
        classified_platforms.append(
            {
                "platform_id": str(platform.get("_id") or ""),
                "vin": vin,
                "market": market,
                "matched_vin_prefix": matched_vin_prefix,
            }
        )

    return {
        "source": "backend/analytics/assets/data/vin_project_mapping.json",
        "rule": "First VIN prefix match in the project mapping asset.",
        "market_counts": dict(sorted(market_counts.items())),
        "platforms": classified_platforms,
    }


def _build_snapshot(
    *,
    view_url: str,
    query: str,
    platform_status: list[str],
    view: str,
    total: int,
    platforms: list[dict[str, Any]],
) -> dict[str, Any]:
    if len(platforms) != total:
        raise FleemApiError(f"FLEEM returned {len(platforms)} platforms but reported a total of {total}")
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_url": str(view_url).strip(),
        "query": query,
        "platform_status": platform_status,
        "view": view,
        "total": total,
        "platforms": platforms,
        "project_market_classification": _project_market_classification(platforms),
    }


def _graphql_result(payload: object) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise FleemApiError("FLEEM returned a non-object GraphQL payload")

    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        messages = [
            str(error.get("message") or "unknown GraphQL error")
            if isinstance(error, Mapping)
            else str(error)
            for error in errors
        ]
        raise FleemApiError("FLEEM GraphQL query failed: " + "; ".join(messages))

    data = payload.get("data")
    result = data.get("Fleem_queryPlatforms") if isinstance(data, Mapping) else None
    if not isinstance(result, Mapping):
        raise FleemApiError("FLEEM GraphQL response did not contain Fleem_queryPlatforms")
    return result


def fetch_platform_snapshot_from_browser_page(
    *,
    page: Any,
    view_url: str,
    page_size: int = 100,
    timezone_offset: str = "+08:00",
    view: str = FLEEM_PLATFORM_EXPORT_VIEW,
) -> dict[str, Any]:
    if page_size < 1:
        raise ValueError("page_size must be positive")
    if not str(view or "").strip():
        raise ValueError("view is required")

    query, platform_status = _parse_view_url(view_url)
    result = page.evaluate(
        _BROWSER_PLATFORM_SNAPSHOT_SCRIPT,
        {
            "query": query,
            "platformStatus": platform_status,
            "timezoneOffset": timezone_offset,
            "pageSize": page_size,
            "view": str(view).strip(),
        },
    )
    if not isinstance(result, Mapping):
        raise FleemApiError("FLEEM browser query returned a non-object result")
    page_metadata = result.get("page")
    platforms = result.get("platforms")
    if not isinstance(page_metadata, Mapping) or not isinstance(platforms, list):
        raise FleemApiError("FLEEM browser query returned an invalid platforms payload")
    try:
        total = int(page_metadata.get("total"))
    except (TypeError, ValueError) as exc:
        raise FleemApiError("FLEEM browser query returned an invalid page total") from exc
    if total < 0 or any(not isinstance(platform, dict) for platform in platforms):
        raise FleemApiError("FLEEM browser query returned invalid platform data")
    return _build_snapshot(
        view_url=view_url,
        query=query,
        platform_status=platform_status,
        view=str(view).strip(),
        total=total,
        platforms=platforms,
    )


def fetch_platform_snapshot_from_browser_profile(
    *,
    profile_dir: Path | str,
    view_url: str,
    headless: bool = False,
    login_timeout: int = 180,
    page_size: int = 100,
    timeout: int = 60,
    timezone_offset: str = "+08:00",
    view: str = FLEEM_PLATFORM_EXPORT_VIEW,
) -> dict[str, Any]:
    if login_timeout < 1:
        raise ValueError("login_timeout must be positive")
    if timeout < 1:
        raise ValueError("timeout must be positive")

    from playwright.sync_api import sync_playwright

    resolved_profile_dir = Path(profile_dir).expanduser().resolve()
    resolved_profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(resolved_profile_dir),
            headless=headless,
            ignore_https_errors=False,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(view_url, wait_until="domcontentloaded", timeout=timeout * 1000)
            page.wait_for_function(
                "() => document.title === 'Fleem | Platforms' && Boolean(localStorage.getItem('token'))",
                timeout=login_timeout * 1000,
            )
            return fetch_platform_snapshot_from_browser_page(
                page=page,
                view_url=view_url,
                page_size=page_size,
                timezone_offset=timezone_offset,
                view=view,
            )
        finally:
            context.close()


def fetch_platform_snapshot(
    *,
    bearer_token: str,
    view_url: str,
    session: requests.Session | Any | None = None,
    page_size: int = 100,
    timeout: int = 60,
    timezone_offset: str = "+08:00",
    view: str = FLEEM_PLATFORM_EXPORT_VIEW,
) -> dict[str, Any]:
    token = str(bearer_token or "").strip()
    if not token:
        raise ValueError("bearer_token is required")
    if page_size < 1:
        raise ValueError("page_size must be positive")
    if timeout < 1:
        raise ValueError("timeout must be positive")
    if not str(view or "").strip():
        raise ValueError("view is required")

    query, platform_status = _parse_view_url(view_url)
    client = session or requests.Session()
    headers = _build_headers(token)
    platforms: list[dict[str, Any]] = []
    expected_total: int | None = None
    current_page = 1

    while True:
        variables = {
            "query": query,
            "platformStatus": platform_status,
            "timezoneOffset": timezone_offset,
            "pagination": {"currentPage": current_page, "pageSize": page_size},
            "view": str(view).strip(),
        }
        response = client.post(
            FLEEM_GRAPHQL_URL,
            json={
                "operationName": "Fleem_queryPlatforms",
                "query": FLEEM_PLATFORM_EXPORT_QUERY,
                "variables": variables,
            },
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        result = _graphql_result(response.json())
        page = result.get("page")
        if not isinstance(page, Mapping):
            raise FleemApiError("FLEEM GraphQL response did not include page metadata")
        try:
            total = int(page.get("total"))
        except (TypeError, ValueError) as exc:
            raise FleemApiError("FLEEM GraphQL response included an invalid page total") from exc
        if total < 0:
            raise FleemApiError("FLEEM GraphQL response included a negative page total")
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise FleemApiError("FLEEM GraphQL page total changed during retrieval")

        batch = result.get("platforms")
        if not isinstance(batch, list) or any(not isinstance(platform, dict) for platform in batch):
            raise FleemApiError("FLEEM GraphQL response included an invalid platforms collection")
        platforms.extend(batch)
        if len(platforms) >= total:
            break
        if not batch:
            raise FleemApiError("FLEEM returned an empty page before the reported total was reached")
        current_page += 1

    return _build_snapshot(
        view_url=view_url,
        query=query,
        platform_status=platform_status,
        view=str(view).strip(),
        total=expected_total or 0,
        platforms=platforms,
    )


def write_platform_snapshot(snapshot: Mapping[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch a FLEEM Platforms view into a JSON snapshot.")
    parser.add_argument("--view-url", required=True, help="Authenticated FLEEM Platforms URL containing iql and platformStatus.")
    parser.add_argument("--output", required=True, type=Path, help="JSON snapshot output path.")
    parser.add_argument("--page-size", type=int, default=100, help="Number of records requested per GraphQL page.")
    parser.add_argument("--timeout", type=int, default=60, help="HTTP request timeout in seconds.")
    parser.add_argument("--timezone-offset", default="+08:00", help="Timezone offset sent to FLEEM, for example +08:00.")
    parser.add_argument("--view", default=FLEEM_PLATFORM_EXPORT_VIEW, help="FLEEM export view name.")
    parser.add_argument(
        "--browser-profile",
        type=Path,
        help="Persistent local Playwright profile for browser-session authentication; token stays in the browser.",
    )
    parser.add_argument("--headless", action="store_true", help="Use the browser profile without showing a browser window.")
    parser.add_argument(
        "--login-timeout",
        type=int,
        default=180,
        help="Seconds to wait for interactive browser login when using --browser-profile.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _build_argument_parser().parse_args(argv)
    if arguments.browser_profile is not None:
        snapshot = fetch_platform_snapshot_from_browser_profile(
            profile_dir=arguments.browser_profile,
            view_url=arguments.view_url,
            headless=arguments.headless,
            login_timeout=arguments.login_timeout,
            page_size=arguments.page_size,
            timeout=arguments.timeout,
            timezone_offset=arguments.timezone_offset,
            view=arguments.view,
        )
    else:
        bearer_token = os.environ.get("FLEEM_BEARER_TOKEN", "").strip()
        if not bearer_token:
            raise SystemExit("Set FLEEM_BEARER_TOKEN or pass --browser-profile")
        snapshot = fetch_platform_snapshot(
            bearer_token=bearer_token,
            view_url=arguments.view_url,
            page_size=arguments.page_size,
            timeout=arguments.timeout,
            timezone_offset=arguments.timezone_offset,
            view=arguments.view,
        )
    write_platform_snapshot(snapshot, arguments.output)
    print(
        json.dumps(
            {
                "output": str(arguments.output),
                "retrieved": len(snapshot["platforms"]),
                "total": snapshot["total"],
                "view": snapshot["view"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())