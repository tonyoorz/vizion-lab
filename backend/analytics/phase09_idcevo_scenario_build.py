#!/usr/bin/env python3
"""
V2 Field Escape Scenario Library Builder.

Reads the LLM scenario-extraction outputs (v2_scenario_18000_outputs.jsonl)
plus ticket metadata from the FullLLM_Complete Excel, and builds the final
multi-sheet Field Escape Scenario Library workbook.

Pipeline (all in this script):
    2. Scenario Normalization (rule-based canonicalization)
    3. Scenario Merge (cluster by Customer Symptom + Trigger + Affected Function)
    4. Engineering Family (stable R&D responsibility grouping)
    5. Field Escape Risk Scoring (rubric-based, per scenario)
    6. Service Knowledge Cards (per scenario)
    7. After-sales reference-tree knowledge base and Excel output
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

REPO_ROOT = Path(__file__).resolve().parents[2]
LLM_DIR = REPO_ROOT / "exports" / "idcevo_phase09" / "llm"
EXCEL_PATH = (
    REPO_ROOT
    / "exports"
    / "idcevo_phase09"
    / "IDCEVO_Phase09_Closure_Risk_20260908_FullLLM_Complete.xlsx"
)
EXTRACTION_JSONL = LLM_DIR / "v2_scenario_18000_outputs.jsonl"
OUTPUT_XLSX = (
    REPO_ROOT
    / "exports"
    / "idcevo_phase09"
    / "IDCEVO_Phase09_Field_Escape_Scenario_Library_V12_Symptom_to_Solution_Trees.xlsx"
)

# ═══════════════════════════════════════════════════════════════════════════════
# Function domain taxonomy — maps raw 功能 field to canonical domains
# ═══════════════════════════════════════════════════════════════════════════════

FUNCTION_DOMAIN_MAP: dict[str, str] = {
    # Audio
    "audio infrastructure": "Audio Infrastructure",
    "audio mixing": "Audio Infrastructure",
    "audio, audiomanagement": "Audio Infrastructure",
    "ent_and_con cn": "Audio / Entertainment",
    "entertainment": "Audio / Entertainment",
    "ent/connected_music_china": "Audio / Entertainment",
    "cn vod": "Audio / Entertainment",
    "p ent functions & ui": "Audio / Entertainment",
    "bon-apps": "Audio / Entertainment",
    # Speech
    "speech cn": "Speech Recognition",
    "cn speech": "Speech Recognition",
    "ipa cn": "Speech Recognition",
    "ipa intelligent functions": "Speech Recognition",
    "use speech operation": "Speech Recognition",
    # Navigation
    "navigation cn": "Navigation",
    "application navigation china hk": "Navigation",
    "provide navigation": "Navigation",
    # Display / HUD
    "displaygraphicsinfra": "Display / HUD",
    "instrument cluster": "Display / HUD",
    "mmi": "Display / HUD",
    "core ui": "Display / HUD",
    "ui toolkit": "Display / HUD",
    "experiences and modes": "Display / HUD",
    # Connectivity
    "wireless services": "Connectivity / ConnectedDrive",
    "telematics": "Connectivity / ConnectedDrive",
    "network": "Connectivity / ConnectedDrive",
    "communication": "Connectivity / ConnectedDrive",
    "vehicle communication": "Connectivity / ConnectedDrive",
    "sdi": "Connectivity / ConnectedDrive",
    "connectivity": "Connectivity / ConnectedDrive",
    # Phone projection
    "cn_huawei_hicar": "HiCar",
    "hicar": "HiCar",
    "carplay": "CarPlay",
    # Personalization
    "personalization": "Personalization / Digital Key",
    "china product": "Personalization / Digital Key",
    "carfunctions": "Personalization / Digital Key",
    # ADAS
    "iks cv": "ADAS / Camera",
    "ad_cnapps": "ADAS / Camera",
    # Platform
    "android platform": "Platform / Base",
    "stability": "Platform / Base",
    "performance": "Platform / Base",
    # HV / PTH / DCDC
    "coordinate high-voltage conditions": "HV / PTH / DCDC",
    "vehicle programming implementation": "HV / PTH / DCDC",
    # Charging
    "charging settings & value add": "Charging",
    # Light
    "interior light": "Lighting",
    "sichtabsicht": "Lighting",
}


_CARPLAY_IDENTIFIERS = ("carplay", "car play", "apple carplay", "苹果 carplay")
_HICAR_IDENTIFIERS = ("hicar", "hi car", "huawei hicar", "cn_huawei_hicar")


def _projection_identity(text: str) -> str:
    normalized = str(text or "").casefold()
    has_carplay = any(identifier in normalized for identifier in _CARPLAY_IDENTIFIERS)
    has_hicar = any(identifier in normalized for identifier in _HICAR_IDENTIFIERS)
    if has_carplay and not has_hicar:
        return "CarPlay"
    if has_hicar and not has_carplay:
        return "HiCar"
    if has_carplay and has_hicar:
        return "Phone Projection / Unspecified"
    return ""


def _specific_projection_identity(scenario: dict[str, Any]) -> str:
    high_confidence_fields = (
        "rep_function",
        "function",
        "rep_title",
        "customer_symptom",
        "trigger_condition",
        "observed_behaviour",
        "expected_behaviour",
    )
    identities = {
        _projection_identity(str(scenario.get(field, "") or ""))
        for field in high_confidence_fields
    }
    identities.discard("")
    if identities == {"CarPlay"}:
        return "CarPlay"
    if identities == {"HiCar"}:
        return "HiCar"
    if identities:
        return "Phone Projection / Unspecified"

    affected_function_identity = _projection_identity(
        str(scenario.get("affected_function_raw", "") or "")
    )
    if affected_function_identity:
        return affected_function_identity
    return _projection_identity(str(scenario.get("affected_function", "") or ""))


def canonical_function_domain(raw_function: str, affected_function: str = "") -> str:
    """Map raw function field to canonical domain, falling back to LLM extraction."""
    raw_projection_identity = _projection_identity(raw_function)
    if raw_projection_identity in {"CarPlay", "HiCar"}:
        return raw_projection_identity
    affected_projection_identity = _projection_identity(affected_function)
    if affected_projection_identity:
        return affected_projection_identity

    # First try the LLM-extracted affected_function
    if affected_function:
        af_lower = affected_function.lower().strip()
        for key, domain in FUNCTION_DOMAIN_MAP.items():
            if key in af_lower or af_lower in key:
                return domain
        # If LLM gave a specific function, use it directly
        if affected_function.strip():
            return affected_function.strip()

    # Fall back to raw function field
    func_lower = raw_function.lower().strip()
    for key, domain in FUNCTION_DOMAIN_MAP.items():
        if key in func_lower:
            return domain

    # Use the raw function name itself, truncated
    return raw_function[:60] if raw_function else "Unclassified"


# ═══════════════════════════════════════════════════════════════════════════════
# Normalization
# ═══════════════════════════════════════════════════════════════════════════════

WHITESPACE_RE = re.compile(r"\s+")
PUNCT_RE = re.compile(r"[，。！？、；：“”‘’（）()【】\[\]{},.!?;:\"'\\/]")
DTC_RE = re.compile(r"\b0x[0-9a-f]{3,}\b", re.IGNORECASE)
LONG_ID_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_]{15,}\b")

# Symptom type keywords — maps diverse symptom descriptions to canonical
# failure-type tokens. The key insight: we cluster by failure-type keyword +
# affected object, not by the full sentence (which varies per LLM extraction).
SYMPTOM_KEYWORD_MAP: dict[str, str] = {
    # Display issues
    "黑屏": "display-blank", "无显示": "display-blank", "不显示": "display-blank",
    "白屏": "display-abnormal", "花屏": "display-abnormal", "闪屏": "display-abnormal",
    "显示异常": "display-abnormal", "显示不": "display-abnormal",
    "缺色": "display-abnormal", "颜色偏差": "display-abnormal",
    # Audio issues
    "无声音": "audio-none", "无声": "audio-none", "没有声音": "audio-none",
    "无音": "audio-none", "无声音输出": "audio-none",
    "声音异常": "audio-abnormal", "杂音": "audio-abnormal", "破音": "audio-abnormal",
    "音量异常": "audio-abnormal", "声音断续": "audio-abnormal",
    # System unresponsive
    "死机": "system-unresponsive", "卡死": "system-unresponsive",
    "无响应": "system-unresponsive", "无反应": "system-unresponsive",
    "卡顿": "system-lag", "卡": "system-lag", "延迟": "system-lag",
    "慢": "system-lag", "loading": "system-lag",
    # Crashes
    "崩溃": "app-crash", "闪退": "app-crash", "退出": "app-crash",
    # Reboots
    "重启": "system-reboot", "复位": "system-reboot", "重置": "system-reboot",
    # Connection
    "断连": "connection-lost", "断开": "connection-lost", "中断": "connection-lost",
    "无法连接": "connection-fail", "连不上": "connection-fail", "连接失败": "connection-fail",
    "连接超时": "connection-timeout", "超时": "connection-timeout",
    # Recognition
    "误识别": "recognition-error", "识别错误": "recognition-error",
    "识别失败": "recognition-fail", "无法识别": "recognition-fail",
    # Navigation
    "定位漂移": "nav-drift", "定位偏差": "nav-drift", "偏移": "nav-drift",
    "定位失败": "nav-fail", "无法定位": "nav-fail",
    "导航崩溃": "nav-crash",
    # Login/Account
    "无法登录": "login-fail", "登录失败": "login-fail",
    "登录异常": "login-abnormal", "自动退出登录": "login-abnormal",
    # Charging
    "无法充电": "charging-fail", "充电失败": "charging-fail",
    # Startup
    "无法启动": "startup-fail", "启动失败": "startup-fail",
    # Warning lights
    "故障灯": "warning-light", "报警": "warning-light",
    # Update/Programming
    "升级失败": "update-fail", "编程失败": "update-fail",
    # Other
    "功能不可用": "feature-unavailable", "功能无法使用": "feature-unavailable",
    "无法使用": "feature-unavailable",
}

# Trigger keywords — extract the core action/state
TRIGGER_KEYWORD_MAP: dict[str, str] = {
    "休眠唤醒": "wake-up", "唤醒": "wake-up", "sleep": "wake-up",
    "重启后": "after-reboot", "复位后": "after-reboot",
    "连接": "connecting", "连接时": "connecting", "连接后": "after-connect",
    "断开": "disconnecting",
    "切换": "switching", "切换后": "after-switch",
    "播放": "playing", "播放后": "after-play",
    "启动": "starting", "启动后": "after-start",
    "行驶": "driving", "行驶中": "driving",
    "停车": "parking", "驻车": "parking",
    "开机": "boot", "开机后": "after-boot",
    "充电": "charging",
    "导航": "navigating", "导航中": "navigating",
    "通话": "calling", "通话中": "calling",
    "更新后": "after-update", "升级后": "after-update",
    "编程后": "after-programming",
}

# Chinese stopwords to remove before keyword extraction
STOPWORDS = {
    "的", "了", "是", "在", "和", "与", "或", "也", "但", "而", "则", "这",
    "那", "一个", "一种", "发生", "出现", "导致", "使得", "用户", "车辆",
    "该", "此", "其", "当", "后", "中", "时", "间", "上", "下", "到",
    "会", "能", "可", "被", "把", "让", "给", "对", "由", "从", "向",
    "等", "之", "以", "及", "或", "如", "即", "于", "为", "有", "无",
    "正常", "突然", "偶发", "偶尔", "频繁", "持续", "仍然", "一直",
    "进行", "尝试", "发现", "观察", "看到", "听到", "感觉",
    "问题", "现象", "故障", "异常", "情况", "状态",
    "车辆", "系统", "功能", "应用", "界面", "页面", "屏幕", "显示",
    "已经", "之前", "之后", "过程中", "情况下",
}


def clean_text(text_value: str) -> str:
    """Basic text cleaning: lowercase, strip punctuation, collapse whitespace."""
    if not text_value:
        return ""
    t = text_value.strip().lower()
    t = DTC_RE.sub("[diag]", t)
    t = LONG_ID_RE.sub("[tech]", t)
    t = PUNCT_RE.sub(" ", t)
    t = WHITESPACE_RE.sub(" ", t).strip()
    return t


def extract_symptom_keyword(text_value: str) -> str:
    """
    Extract the canonical failure-type keyword from symptom text.
    This is the clustering key — not the full sentence, just the failure type.
    e.g. '车辆行驶中DCDC转换器突然停止工作' -> 'system-unresponsive' (from '停止工作')
         '从APP页面切换至主页时长时间黑屏' -> 'display-blank' (from '黑屏')
    """
    if not text_value:
        return "unknown"
    t = text_value.lower()
    # Check keyword map — return first match
    for keyword, canonical in SYMPTOM_KEYWORD_MAP.items():
        if keyword.lower() in t:
            return canonical
    # Fallback: try to detect failure pattern from common verbs
    failure_patterns = {
        "无法": "cannot-function",
        "不能": "cannot-function",
        "失败": "fail",
        "异常": "abnormal",
        "错误": "error",
        "丢失": "lost",
        "消失": "lost",
        "停止": "stopped",
        "不可用": "unavailable",
    }
    for pattern, canonical in failure_patterns.items():
        if pattern in t:
            return canonical
    return "other"


def extract_trigger_keyword(text_value: str) -> str:
    """
    Extract the canonical trigger keyword from trigger text.
    e.g. 'CDE播放视频后切换至QQ音乐' -> 'after-switch|playing'
         '休眠唤醒后' -> 'wake-up'
    """
    if not text_value:
        return "unknown"
    t = text_value.lower()
    triggers = []
    for keyword, canonical in TRIGGER_KEYWORD_MAP.items():
        if keyword.lower() in t:
            if canonical not in triggers:
                triggers.append(canonical)
    if not triggers:
        # Check if it's a static/always condition
        if any(w in t for w in ["始终", "持续", "一直", "每次"]):
            return "persistent"
        if any(w in t for w in ["偶发", "偶尔", "有时"]):
            return "intermittent"
        return "other"
    return "|".join(sorted(triggers))


def normalize_symptom(text_value: str) -> str:
    """Full normalization for display: clean + canonical terms."""
    t = clean_text(text_value)
    return t


def normalize_trigger(text_value: str) -> str:
    """Full normalization for display."""
    return clean_text(text_value)


def scenario_key(symptom: str, trigger: str, function: str) -> str:
    """
    Build a deterministic hash key from keyword-extracted scenario fields.
    Clusters by: failure-type keyword + trigger keyword + canonical function domain.
    This ensures tickets with the same symptom type and trigger pattern merge,
    even if the LLM worded them slightly differently.
    """
    s = extract_symptom_keyword(symptom)
    t = extract_trigger_keyword(trigger)
    f = function.lower().strip()
    raw = f"{s}|{t}|{f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════════════════
# Service decision trees
# ═══════════════════════════════════════════════════════════════════════════════

# Family is R&D responsibility context. Service Function is the after-sales
# customer entry. Reference Trees capture supplied after-sales flow assets.
_DECISION_TREE_SOURCE_DEFINITIONS: tuple[
    tuple[str, str, str, str, str, str], ...
] = (
    ("DTREF-BATTERY_MONITORING", "[BAT] Battery Monitoring.png", "BAT", "1.0", "Battery condition and monitoring eligibility -> vehicle evidence -> resolve or escalation", "Charging / Connectivity"),
    ("DTREF-IPA", "[IPA] Intelligent Personal Assistant.png", "IPA", "1.0", "Feature availability -> voice/vehicle preconditions -> application evidence -> L3", "Speech / Connectivity"),
    ("DTREF-PIA", "[PIA] - Personalization.png", "PIA", "1.0", "Profile and entitlement -> vehicle synchronization -> personalization recovery", "Account / Digital Key / Connectivity"),
    ("DTREF-R360", "[R360] - Remote 3D View.png", "REMOTE_SERVICES", "1.0", "Remote entitlement -> vehicle online -> camera/data freshness -> remote-service escalation", "Connectivity / Camera"),
    ("DTREF-RSU", "[RSU] Remote Software Upgrade.png", "SOFTWARE_UPDATE", "1.0", "Eligibility -> campaign and package state -> vehicle download/install evidence -> RSU L3", "Connectivity / Account"),
    ("DTREF-RTTI", "[RTTI] - Real Time Traffic Information CN.png", "NAVIGATION", "1.0", "Service booked -> vehicle online -> traffic data provision -> navigation escalation", "Connectivity / Account"),
    ("DTREF-S2C", "[S2C] - Send2Car CN.png", "NAVIGATION", "1.0", "App-to-vehicle synchronization -> vehicle online -> destination delivery evidence", "My BMW App / Connectivity"),
    ("DTREF-SLI", "[SLI] - Speed Limit Information.png", "DRIVER_ASSISTANCE", "1.0", "Vehicle capability -> camera/map input -> assistance function validation -> L3", "Navigation / ADAS"),
    ("DTREF-DIGITAL_KEY", "[SMACC] - Digital Key.png", "PIA", "1.0", "Device and vehicle eligibility -> account/profile state -> pairing evidence -> Digital Key L3", "Account / Connectivity"),
    ("DTREF-CD_STORE", "[Store] - ConnectedDrive Store CN.png", "ENTERTAINMENT", "CN", "Contract/entitlement -> store availability -> download/provisioning -> store escalation", "Connectivity / Account"),
    ("DTREF-ACALL", "acall.png", "ACALL", "1.0", "Vehicle online -> SA code -> service booking -> call error handling -> ACall L3", "Connectivity / eCall"),
    ("DTREF-ACCOUNT", "account.png", "ACCOUNT_PROBLEM", "CN", "Login/account state -> profile/privacy/vehicle mapping -> owning platform route", "PIA / GCDM / CASA / My BMW App"),
    ("DTREF-AM_SR", "am sr.png", "AM_SR", "1.0", "Subscription -> market/account eligibility -> media service recovery", "Connectivity / Account"),
    ("DTREF-BCALL", "bcall.png", "BCALL", "1.0", "Vehicle online -> SA code -> booking -> signing/call error -> BCall L3", "Connectivity / eCall"),
    ("DTREF-BON_WEATHER", "bon weather.png", "BON_WEATHER", "CN", "Booking -> online state -> provisioning/download -> application owner route", "Connectivity / In-Car Store"),
    ("DTREF-CARPLAY", "carplay.png", "CARPLAY", "1.0", "Compatibility -> activation -> phone connection -> symptom branch -> recovery or L3", "Audio / HMI / Navigation"),
    ("DTREF-CBS", "CBS.png", "CBS", "1.0", "App/vehicle synchronization -> connectivity -> vehicle data transmission -> business handling", "Connectivity / Vehicle Data"),
    ("DTREF-CCM", "CCM.png", "CCM", "1.0", "App/vehicle synchronization -> connectivity -> charging data and service-channel checks", "Connectivity / LSC"),
    ("DTREF-CD_UNSPECIFIED", "CDUnspecified.png", "CDUNSPECIFIED", "1.0", "Retrofit/vehicle presence -> activation and SIM state -> CASA or specialist routing", "AM/SR / CASA / Connectivity"),
    ("DTREF-ADAS", "ConnectedDrive - ADAS.png", "DRIVER_ASSISTANCE", "1.0", "Vehicle precheck -> SA code -> sensor/camera modality -> ADAS functional route", "Camera / Navigation / Connectivity"),
    ("DTREF-DIGITAL_TIRE", "Digital Tire Diagnosis CN.png", "DRIVER_ASSISTANCE", "CN", "Vehicle tire data -> app/vehicle display consistency -> diagnostics route", "Connectivity / Vehicle Data"),
    ("DTREF-DRIVE_RECORDER", "DriveRecorder.png", "DRIVE_RECORDER", "1.0", "Capability and configuration -> provisioning/version -> recording symptom -> Drive Recorder L3", "Connectivity / Camera"),
    ("DTREF-ECALL", "ecall.png", "ECALL", "1.0", "Vehicle online -> SA code -> enablement/contract -> call diagnostics -> eCall L3", "Connectivity / BCall / ACall"),
    ("DTREF-EMOBILITY_CHARGING", "eMobility&Charing.png", "CHARGING", "1.0", "Charging capability -> contract/service state -> vehicle/app data -> charging specialist route", "CCM / Connectivity"),
    ("DTREF-ENTERTAINMENT", "Entertainment.png", "ENTERTAINMENT", "CN", "Headunit/app precheck -> content provider or app type -> store/media/game owner route", "In-Car Store / 3rd Party Apps"),
    ("DTREF-FOD", "FOD - Decision Tree V1.png", "ENTERTAINMENT", "V1", "Feature entitlement -> package state -> activation/install evidence -> FOD handling", "ConnectedDrive Store / Connectivity"),
    ("DTREF-FOD", "FOD - Decision Tree V2.png", "ENTERTAINMENT", "V2", "Feature entitlement -> package state -> activation/install evidence -> FOD handling", "ConnectedDrive Store / Connectivity"),
    ("DTREF-CONNECTIVITY_LEGACY", "historical data connectivity.png", "CONNECTIVITY_GEN2", "Legacy", "SIM/network state -> backend connection -> vehicle data flow -> connectivity routing", "LSC / Provisioning"),
    ("DTREF-INDOOR_PARKING", "IndoorParking.png", "DRIVER_ASSISTANCE", "1.0", "Vehicle capability -> service booking -> online state -> indoor parking route", "Connectivity / Vehicle Finder"),
    ("DTREF-LSC", "LSC.png", "CONNECTIVITY_GEN2", "1.0", "Service status freshness -> LSC/FSU logs -> vehicle attribute diagnosis -> specialist route", "Vehicle Finder / Connectivity"),
    ("DTREF-CD_ACTIVATION", "Mapping&CD Activation.png", "CONNECTIVITY_GEN2", "1.0", "Mapping/de-mapping -> activation state -> service platform route", "Account / CASA"),
    ("DTREF-MAPUP", "MapUp.png", "MAPUP", "1.0", "Map package eligibility -> download/install state -> map update escalation", "Connectivity / Navigation"),
    ("DTREF-MYBMW_APP", "MYBMW APP.png", "REMOTE_SERVICES", "1.0", "App state -> account/vehicle binding -> service-specific route -> mobile owner", "Account / Remote Services"),
    ("DTREF-MYTRIPS", "MyTrips.png", "MYTRIPS", "1.0", "Journey data eligibility -> synchronization -> privacy/data route", "Connectivity / My BMW App"),
    ("DTREF-NAVIGATION", "Navigation.png", "NAVIGATION", "1.0", "Navigation precheck -> map/location/content branch -> functional diagnosis -> navigation L3", "Connectivity / ADAS"),
    ("DTREF-CONNECTIVITY_GEN2", "new data connectivity.png", "CONNECTIVITY_GEN2", "GEN2", "Precheck -> SIM/network -> MQTT/HTTP state -> provisioning/app/backend route", "Legacy Connectivity / OAP"),
    ("DTREF-PERS_ESIM", "PERS.ESIM.png", "PERS_ESIM", "1.0", "eSIM eligibility -> profile/account state -> activation evidence -> eSIM route", "PIA / Account"),
    ("DTREF-QQ_MUSIC_HIMALAYA", "QQ Music Himalaya.png", "QQ_MUSIC_HIMALAYA", "CN", "Subscription/app state -> playback/content branch -> provider or platform route", "Entertainment / Connectivity"),
    ("DTREF-REMOTE_SERVICE", "Remote Service.png", "REMOTE_SERVICES", "1.0", "App/API -> backend -> connectivity -> vehicle wake-up/data path -> service owner", "My BMW App / Connectivity"),
)

DECISION_TREE_SOURCE_CATALOG: tuple[dict[str, Any], ...] = tuple(
    {
        "reference_tree_id": reference_tree_id,
        "source_file": source_file,
        "source_asset": (
            f"decision_tree_sources/lossless_webp/{Path(source_file).stem}.webp"
        ),
        "service_function_id": service_function_id,
        "tree_version": tree_version,
        "triage_pattern": triage_pattern,
        "cross_tree_routes": cross_tree_routes,
        "catalog_status": "after_sales_reference",
    }
    for (
        reference_tree_id,
        source_file,
        service_function_id,
        tree_version,
        triage_pattern,
        cross_tree_routes,
    ) in _DECISION_TREE_SOURCE_DEFINITIONS
)

_REFERENCE_TREE_IDS_BY_SERVICE: dict[str, list[str]] = defaultdict(list)
for _reference_tree in DECISION_TREE_SOURCE_CATALOG:
    _reference_ids = _REFERENCE_TREE_IDS_BY_SERVICE[_reference_tree["service_function_id"]]
    if _reference_tree["reference_tree_id"] not in _reference_ids:
        _reference_ids.append(_reference_tree["reference_tree_id"])
REFERENCE_TREE_IDS_BY_SERVICE = {
    service_function_id: tuple(reference_tree_ids)
    for service_function_id, reference_tree_ids in _REFERENCE_TREE_IDS_BY_SERVICE.items()
}

_ENGINEERING_FAMILY_DEFINITIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("PHONE_PROJECTION", "Phone Projection / CarPlay / HiCar", ("carplay", "hicar", "cn_huawei_hicar", "phone projection")),
    ("ENTERTAINMENT_APPS", "Entertainment / 3rd-Party Apps", ("gaming", "3rd party", "third party", "cn_gaming", "entertainment", "app store", "apps_store", "connected music", "fod", "in-car store")),
    ("AUDIO_MEDIA_PLATFORM", "Audio / Media Platform", ("audio", "klangeinstellungen", "audiomanagement", "audiofocus", "audio mixing", "base audio", "media playback")),
    ("DRIVER_ASSISTANCE", "Driver Assistance / ADAS", ("ad_apps", "ad_cnapps", "ad_functions", "iks cv", "driver assistance", "lane change", "parking assist", "sl i", "speed limit")),
    ("ASSISTANCE_EMERGENCY", "Assistance / Emergency Calls", ("accident call", "xcall", "acall", "bcall", "ecall", "e call", "emergency call", "breakdown call", "onl/xcall", "onl/e call")),
    ("REMOTE_MOBILE", "Remote / Mobile Services", ("remote_in_car", "remote in car", "remote service", "remote-hmi", "r360", "my bmw", "bmw_connected_client", "bmw connected client", "remote configuration")),
    ("CONNECTED_SERVICES", "ConnectedDrive / Connectivity", ("telematics", "wireless services", "connectivity", "modem", "nad", "onl/online", "network", "vehicle communication", "sdi", "lsc")),
    ("NAVIGATION_LOCATION", "Navigation / Location", ("navigation", "mapup", "map update", "route guidance", "rtti", "send2car", "send to car", "location")),
    ("PERSONALIZATION_IDENTITY", "Personalization / Identity / Digital Key", ("personalization", "digital key", "smart access", "carfunctions", "account", "gcdm", "pers.esim")),
    ("CHARGING_ENERGY", "Charging / Energy", ("charging", "battery", "emobility", "e-mobility", "high-voltage", "high voltage", "dcdc")),
    ("HMI_DISPLAY", "HMI / Display", ("display", "hud", "phud", "cid", "ui toolkit", "core ui", "instrument cluster", "mmi", "launcher")),
    ("SPEECH_IPA", "Speech / IPA", ("speech", "ipa", "voice interface", "voice operation", "dictation")),
    ("PLATFORM_SOFTWARE", "Platform / Software", ("android platform", "softwareplatform", "stability", "performance", "logtrace", "cdc platform")),
    ("CAMERA_RECORDING", "Camera / Recording", ("camera", "drive recorder", "apprecorder", "snapshot", "pvis", "rear view")),
    ("VEHICLE_FEATURES", "Vehicle Functions / Controls", ("climatic comfort", "climate control", "interior sensing", "lighting", "interior light", "seat comfort", "vehicle function")),
    ("OTHER_UNCLASSIFIED", "Other / Unclassified", ()),
)

ENGINEERING_FAMILY_CATALOG: tuple[dict[str, Any], ...] = tuple(
    {
        "engineering_family_id": engineering_family_id,
        "engineering_family": engineering_family,
        "aliases": aliases,
    }
    for engineering_family_id, engineering_family, aliases in _ENGINEERING_FAMILY_DEFINITIONS
)
ENGINEERING_FAMILY_BY_ID = {
    item["engineering_family_id"]: item for item in ENGINEERING_FAMILY_CATALOG
}

_SERVICE_FUNCTION_DEFINITIONS: tuple[
    tuple[str, str, str, str, str, tuple[str, ...]], ...
] = (
    ("BAT", "Battery", "[BAT] Battery (电池)", "Charging / Energy", "after_sales_reference", ("battery monitoring", "battery", "电池")),
    ("CBS", "Connected Booking Services", "[CBS] Connected Booking Services", "ConnectedDrive / Entitlement", "after_sales_reference", ("connected booking", "booking service", "预约服务")),
    ("CCM", "Connected Charging Management", "[CCM] Connected Charging Management", "Charging / Energy", "after_sales_reference", ("connected charging management", "charging management", "ccm")),
    ("CDUNSPECIFIED", "Default Decision Tree", "[CDUnspecified] Default Decision Tree (默认兜底)", "ConnectedDrive / Entitlement", "after_sales_reference", ()),
    ("PIA", "Personalization", "[PIA] Personalization (个性化)", "Identity / Personalization", "after_sales_reference", ("personalization", "personalisation", "digital key", "smart access", "个性化")),
    ("QQ_MUSIC_HIMALAYA", "QQ Music / Himalaya", "[QQ Music/Himalaya] QQ音乐/喜马拉雅", "In-Car Entertainment / Apps", "after_sales_reference", ("qq music", "qq音乐", "himalaya", "喜马拉雅")),
    ("ACALL", "Accident Assistance Call", "ACall - Accident Assistance Call (事故救援呼叫)", "Assistance / Emergency Calls", "after_sales_reference", ("accident call", "accident assistance", "acall", "xcall", "事故救援")),
    ("ACCOUNT_PROBLEM", "Account Problem", "AccountProblem - Account Problem, GCDM CN (账号问题)", "Identity / Personalization", "after_sales_reference", ("account problem", "account", "账号", "账户", "gcdm")),
    ("AM_SR", "Amazon Music / SiriusXM", "AM/SR - Amazon Music & SiriusXM", "In-Car Entertainment / Apps", "after_sales_reference", ("amazon music", "siriusxm", "sirius xm", "am/sr")),
    ("BCALL", "Breakdown Call", "BCall - Breakdown Call (道路救援呼叫)", "Assistance / Emergency Calls", "after_sales_reference", ("breakdown call", "bcall", "roadside", "道路救援")),
    ("BMW_CHARGING_CONTRACTS", "BMW Charging Contracts", "BMWChargingContracts - 宝马充电合约", "Charging / Energy", "after_sales_reference", ("charging contract", "charging contracts", "充电合约")),
    ("BON_WEATHER", "BON Weather", "BON Weather - 天气", "ConnectedDrive / Entitlement", "after_sales_reference", ("bon weather", "weather app", "天气")),
    ("CARPLAY", "CarPlay", "CarPlay - 苹果 CarPlay", "Phone Projection", "after_sales_reference", ("carplay", "car play", "apple carplay", "苹果 carplay")),
    ("HICAR", "HiCar", "HiCar - 华为 HiCar", "Phone Projection", "rd_derived_candidate", ("hicar", "hi car", "huawei hicar", "cn_huawei_hicar")),
    ("NAVIGATION_AMAP", "Navigation Amap", "Navigation Amap - 功能地图功能", "Navigation / Location", "idcevo_function_map_feature", ("navigation amap", "navigation anmap", " amap", " anmap")),
    ("AI_TRAVEL_COMPANION", "AI Empowered Travel Companion", "AI Empowered Travel Companion - 功能地图功能", "Navigation / Location", "idcevo_function_map_feature", ("ai empowered travel companion", "travel companion")),
    ("POTHOLE_WARNING", "Pothole Warning", "Pothole Warning - 功能地图功能", "Navigation / Location", "idcevo_function_map_feature", ("pothole warning", "pothole")),
    ("HAZARD_PREVIEW", "Hazard Preview", "Hazard Preview - 功能地图功能", "Navigation / Location", "idcevo_function_map_feature", ("hazard preview",)),
    ("MAP_SEAMLESS", "Map Seamless", "Map Seamless - 功能地图功能", "Navigation / Location", "idcevo_function_map_feature", ("map seamless", "amap seamless", "seamless amap")),
    ("DESTINATION_SEARCH", "Destination Search", "Destination Search - 功能地图功能", "Navigation / Location", "idcevo_function_map_feature", ("destination search",)),
    ("E_ROUTE", "Navigation E-Route", "Navigation E-Route - 功能地图功能", "Navigation / Location", "idcevo_function_map_feature", ("e-route", "e route", "eroute")),
    ("PREMIUM_PARKING", "Premium Parking Experience", "Premium Parking Experience - 功能地图功能", "Navigation / Location", "idcevo_function_map_feature", ("premium parking experience", "on-street parking", "on street parking")),
    ("CHARGING_POI", "Charging POIs", "Charging POIs - 功能地图功能", "Charging / Energy", "idcevo_function_map_feature", ("charging poi", "charging pois")),
    ("WIRELESS_CHARGING", "Wireless Charging", "Wireless Charging - 功能地图功能", "Charging / Energy", "idcevo_function_map_feature", ("wireless charging",)),
    ("AI_EMPOWERED_IPA", "AI Empowered IPA", "AI Empowered IPA - 功能地图功能", "Voice / Assistant", "idcevo_function_map_feature", ("ai empowered ipa",)),
    ("IPA_DIRECT_COMMANDS", "IPA Direct Commands", "Front Seat Just Talk / Direct Commands - 功能地图功能", "Voice / Assistant", "idcevo_function_map_feature", ("front seat just talk", "direct commands", "direct command")),
    ("IPA_PASSENGER_ENTERTAINMENT", "IPA Passenger Entertainment", "Speech for Passenger Entertainment - 功能地图功能", "Voice / Assistant", "idcevo_function_map_feature", ("speech for passenger entertainment", "passenger entertainment")),
    ("IPA_CAR_CONTROL", "Extended IPA Car Control", "Extended IPA Car Control - 功能地图功能", "Voice / Assistant", "idcevo_function_map_feature", ("extended ipa car control",)),
    ("FORGOTTEN_PHONE_REMINDER", "Forgotten Phone Reminder", "Camera Recognition for Forgotten Phone Reminder - 功能地图功能", "Voice / Assistant", "idcevo_function_map_feature", ("forgotten phone reminder", "forgotten phone")),
    ("QQ_MUSIC", "QQ Music", "QQ Music - 功能地图功能", "In-Car Entertainment / Apps", "idcevo_function_map_feature", ("qq music", "qq音乐")),
    ("XIMALAYA", "Ximalaya", "Ximalaya - 功能地图功能", "In-Car Entertainment / Apps", "idcevo_function_map_feature", ("ximalaya", "喜马拉雅")),
    ("NETEASE_CLOUD_MUSIC", "NetEase Cloud Music", "NetEase Cloud Music - 功能地图功能", "In-Car Entertainment / Apps", "idcevo_function_map_feature", ("netease cloud music", "netease", "网易云音乐")),
    ("SODA_MUSIC", "Soda Music", "Soda Music - 功能地图功能", "In-Car Entertainment / Apps", "idcevo_function_map_feature", ("soda music", "soda")),
    ("KUWO_MUSIC", "Kuwo Music", "Kuwo Music (3rd Party) - 功能地图功能", "In-Car Entertainment / Apps", "idcevo_function_map_feature", ("kuwo music", "kuwo", "酷我音乐")),
    ("KARAOKE", "Karaoke", "Karaoke - 功能地图功能", "In-Car Entertainment / Apps", "idcevo_function_map_feature", ("karaoke",)),
    ("SING_ALONG_LYRICS", "Sing-along Lyrics", "Sing-along Lyrics - 功能地图功能", "In-Car Entertainment / Apps", "idcevo_function_map_feature", ("sing-along lyrics", "sing along lyrics", "singalong lyrics")),
    ("WESING", "WeSing", "WeSing - 功能地图功能", "In-Car Entertainment / Apps", "idcevo_function_map_feature", ("wesing", "we sing", "全民k歌")),
    ("IMMERSIVE_MUSIC", "Immersive Music", "Immersive Music - 功能地图功能", "In-Car Entertainment / Apps", "idcevo_function_map_feature", ("immersive music",)),
    ("DOLBY_ATMOS", "Dolby Atmos", "Dolby Atmos - 功能地图功能", "In-Car Entertainment / Apps", "idcevo_function_map_feature", ("dolby atmos",)),
    ("IQIYI", "iQIYI", "iQIYI - 功能地图功能", "In-Car Entertainment / Apps", "idcevo_function_map_feature", ("iqiyi", "爱奇艺")),
    ("TENCENT_VIDEO", "Tencent Video", "Tencent Video - 功能地图功能", "In-Car Entertainment / Apps", "idcevo_function_map_feature", ("tencent video", "腾讯视频")),
    ("BILIBILI", "Bilibili", "Bilibili (3rd Party) - 功能地图功能", "In-Car Entertainment / Apps", "idcevo_function_map_feature", ("bilibili", "哔哩哔哩")),
    ("MIGU_GAMING", "Migu Gaming", "Migu Gaming - 功能地图功能", "In-Car Entertainment / Apps", "idcevo_function_map_feature", ("migu gaming", "migu game", "咪咕游戏")),
    ("DLNA_CASTING", "China DLNA Casting", "China DLNA Casting (CID) - 功能地图功能", "In-Car Entertainment / Apps", "idcevo_function_map_feature", ("china dlna casting", "dlna")),
    ("MYBMW_HOTSPOT_SCAN", "My BMW App Hotspot Scan", "Connect Hotspot via My BMW App Scan - 功能地图功能", "Remote / Mobile Services", "idcevo_function_map_feature", ("hotspot via my bmw app scan", "my bmw app scan")),
    ("PHUD", "PHUD Experience", "PHUD Experience - 功能地图功能", "Vehicle HMI / Display", "idcevo_function_map_feature", ("phud experience", "phud")),
    ("ALL_APPS_GESTURE", "All Apps Gesture", "Five-finger Pinch/Spread to All Apps Menu - 功能地图功能", "Vehicle HMI / Display", "idcevo_function_map_feature", ("five-finger pinch", "five finger pinch", "pinch/spread to all apps", "all apps menu")),
    ("SHOWROOM_EXPERIENCE", "Show Room Experience", "Show Room Experience - 功能地图功能", "Vehicle HMI / Display", "idcevo_function_map_feature", ("show room experience", "showroom experience")),
    ("MOMENTA_ADAS", "ADAS Experience with Momenta", "ADAS Experience with Momenta Integration - 功能地图功能", "Driver Assistance / ADAS", "idcevo_function_map_feature", ("momenta integration", "momenta")),
    ("MY_MOMENTS", "My Moments", "My Moments - 功能地图功能", "Vehicle HMI / Display", "idcevo_function_map_feature", ("my moments", "mymoments")),
    ("MY_MODES", "My Modes", "My Modes - 功能地图功能", "Vehicle HMI / Display", "idcevo_function_map_feature", ("my modes", "mymodes")),
    ("WELCOME_ANIMATION", "Welcome Animation", "Welcome Animation - 功能地图功能", "Vehicle HMI / Display", "idcevo_function_map_feature", ("welcome animation",)),
    ("SMART_ACCESS", "Smart Access / Digital Key", "Smart Access / Digital Key - 功能地图功能", "Identity / Personalization", "idcevo_function_map_feature", ("smart access", "digital key")),
    ("REMOTE_3D_VIEW", "Remote 3D View", "Remote 3D View - 功能地图功能", "Remote / Mobile Services", "idcevo_function_map_feature", ("remote 3d", "remote view")),
    ("MYBMW_APP", "MY BMW App", "MY BMW App - 功能地图功能", "Remote / Mobile Services", "idcevo_function_map_feature", ("my bmw app", "mybmw app")),
    ("ETC_CHINA", "ETC China", "ETC China - 功能地图功能", "Vehicle Connectivity / Cloud", "idcevo_function_map_feature", ("etc china",)),
    ("REAL_TIME_MONITORING", "Real Time Monitoring", "Real Time Monitoring - 功能地图功能", "Vehicle Connectivity / Cloud", "idcevo_function_map_feature", ("real time monitoring", "realtime monitoring")),
    ("MNO_CONNECTIVITY_CHINA", "MNO Connectivity China", "MNO Connectivity China - 功能地图功能", "Vehicle Connectivity / Cloud", "idcevo_function_map_feature", ("mno connectivity china", "mno connectivity")),
    ("UNICOM_CONNECTIVITY", "Unicom Connectivity", "Unicom - 功能地图功能", "Vehicle Connectivity / Cloud", "idcevo_function_map_feature", ("unicom connectivity", "unicom")),
    ("CHARGING", "Charging", "Charging - 充电功能", "Charging / Energy", "after_sales_reference", ("charging", "charge", "充电")),
    ("CONNECTIVITY_GEN2", "Connectivity (GEN2)", "Connectivity (GEN2) - 车联网连接", "ConnectedDrive / Connectivity", "after_sales_reference", ("connectivity", "connecteddrive", "connected drive", "telematics", "vehicle online", "车联网")),
    ("DRIVE_RECORDER", "Drive Recorder", "DriveRecorder - 行车记录仪", "Camera / Recording", "after_sales_reference", ("drive recorder", "apprecorder", "dashcam", "行车记录仪")),
    ("ECALL", "Emergency Call", "eCall - Emergency Call (紧急呼叫)", "Assistance / Emergency Calls", "after_sales_reference", ("emergency call", "ecall", "e-call", "紧急呼叫")),
    ("ESIM", "eSIM", "eSIM - eSIM 服务", "ConnectedDrive / Connectivity", "after_sales_reference", ("esim", "e sim")),
    ("ENTERTAINMENT", "Entertainment", "Entertainment - 娱乐系统 / 3rd-Party Apps", "In-Car Entertainment / Apps", "after_sales_reference", ("gaming", "3rd party", "third party", "cn_gaming", "entertainment", "app store", "in-car store", "fod")),
    ("HMI", "HMI", "HMI - 人机交互界面", "Vehicle HMI / Display", "rd_derived_candidate", ("hmi", "human machine interface", "ui toolkit", "core ui", "display", "hud", "phud", "cid", "人机交互")),
    ("IPA", "Intelligent Personal Assistant", "IPA - Intelligent Personal Assistant (智能语音助手)", "Voice / Assistant", "after_sales_reference", ("intelligent personal assistant", "ipa intelligent", "智能语音助手")),
    ("MAPUP", "Map Update", "MapUp - Map Update (地图更新)", "Navigation / Location", "after_sales_reference", ("map update", "mapup", "地图更新")),
    ("MAP", "Map", "Map - 地图", "Navigation / Location", "rd_derived_candidate", ("map display", "地图")),
    ("MOB2", "My BMW Mobile 2.0", "Mob2 - My BMW Mobile 2.0", "Remote / Mobile Services", "after_sales_reference", ("mob2", "my bmw mobile 2")),
    ("MYTRIPS", "My Trips", "MyTrips - 我的行程", "Remote / Mobile Services", "after_sales_reference", ("my trips", "mytrips", "我的行程")),
    ("NAVIGATION", "Navigation", "Navigation - 导航", "Navigation / Location", "after_sales_reference", ("navigation", "route guidance", "route", "ccp", "rtti", "导航")),
    ("NEWS", "News", "News - 新闻", "In-Car Entertainment / Apps", "rd_derived_candidate", ("news", "新闻")),
    ("PAYMENT", "Payment", "Payment - 支付", "ConnectedDrive / Entitlement", "rd_derived_candidate", ("payment", "支付")),
    ("PERS_ESIM", "Personalization eSIM", "PERS.ESIM - Personalization eSIM", "Identity / Personalization", "after_sales_reference", ("pers.esim", "pers esim", "personalization esim", "个性化 esim")),
    ("REMOTE_SERVICES", "Remote Services", "Remote Services - 远程服务", "Remote / Mobile Services", "after_sales_reference", ("remote_in_car", "remote in car", "remote service", "remote-hmi", "r360", "my bmw", "bmw connected client", "远程服务")),
    ("SOFTWARE_UPDATE", "Software Update", "Software Update - 软件更新", "Platform / Software", "after_sales_reference", ("remote software upgrade", "rsu", "software update", "softwareupdate", "ota", "软件更新", "升级", "编程")),
    ("TTS", "Text-to-Speech", "TTS - Text-to-Speech (语音合成)", "Voice / Assistant", "rd_derived_candidate", ("text-to-speech", "text to speech", "tts", "语音合成")),
    ("SPEECH", "Speech", "Speech - 语音", "Voice / Assistant", "rd_derived_candidate", ("speech", "voice interface", "voice operation", "dictation", "语音")),
    ("USB", "USB", "USB - USB 媒体/连接", "In-Car Entertainment / Apps", "rd_derived_candidate", ("usb", "usb media")),
    ("VIDEO", "Video", "Video - 视频", "In-Car Entertainment / Apps", "rd_derived_candidate", ("video", "vod", "视频")),
    ("DRIVER_ASSISTANCE", "Driver Assistance / ADAS", "Driver Assistance / ADAS - 驾驶辅助", "Driver Assistance / ADAS", "after_sales_reference", ("ad_apps", "ad_cnapps", "ad_functions", "driver assistance", "parking assist", "indoor parking", "speed limit", "adas")),
    ("AUDIO_MEDIA", "Audio / Media Platform", "Audio / Media Platform - 音频媒体平台", "In-Car Entertainment / Apps", "rd_derived_candidate", ("audio infrastructure", "audio routing", "audiofocus", "audio mixing", "audiomanagement", "base audio", "klangeinstellungen")),
    ("PLATFORM_SOFTWARE", "Platform / Software", "Platform / Software - 车机平台", "Platform / Software", "rd_derived_candidate", ("android platform", "softwareplatform", "stability", "performance", "cdc platform")),
    ("CAMERA_RECORDING", "Camera / Recording", "Camera / Recording - 摄像头与录像", "Camera / Recording", "rd_derived_candidate", ("camera infrastructure", "snapshot", "rear view camera", "pvis", "interior sensing")),
    ("VEHICLE_FEATURES", "Vehicle Functions / Controls", "Vehicle Functions / Controls - 车辆功能", "Vehicle Functions / Controls", "rd_derived_candidate", ("climate control", "climatic comfort", "interior light", "lighting", "seat comfort")),
)

SERVICE_FUNCTION_CATALOG: tuple[dict[str, Any], ...] = tuple(
    {
        "service_function_id": service_function_id,
        "service_function": service_function,
        "display_name": display_name,
        "service_domain": service_domain,
        "catalog_status": catalog_status,
        "aliases": aliases,
        "reference_tree_ids": REFERENCE_TREE_IDS_BY_SERVICE.get(service_function_id, ()),
    }
    for (
        service_function_id,
        service_function,
        display_name,
        service_domain,
        catalog_status,
        aliases,
    ) in _SERVICE_FUNCTION_DEFINITIONS
)
SERVICE_FUNCTION_BY_ID = {
    item["service_function_id"]: item for item in SERVICE_FUNCTION_CATALOG
}

PROBLEM_TYPE_LABELS = {
    "activation_availability": "Activation / Availability (激活/不可用)",
    "connection_pairing": "Connection / Pairing (连接/配对)",
    "authentication_account": "Authentication / Account (认证/账号)",
    "audio_media": "Audio / Media (声音/媒体)",
    "call_communication": "Call / Communication (通话/呼叫)",
    "display_projection": "Display / Projection (显示/投屏)",
    "control_interaction": "Control / Interaction (控制/交互)",
    "navigation_location": "Navigation / Location (导航/定位)",
    "charging_energy": "Charging / Energy (充电/能源)",
    "update_programming": "Update / Programming (更新/编程)",
    "stability_performance": "Stability / Performance (稳定性/性能)",
    "data_sync": "Data / Sync (数据/同步)",
    "content_service": "Content / Service (内容/服务)",
    "safety_emergency": "Safety / Emergency (安全/紧急)",
    "other": "Other / Needs Classification (其他/待分类)",
}

PROBLEM_TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("safety_emergency", ("safety", "emergency", "安全", "紧急", "事故", "救援")),
    ("charging_energy", ("charging", "charge", "battery", "充电", "电池", "高压")),
    ("update_programming", ("software update", "ota", "programming", "升级", "更新", "编程")),
    ("authentication_account", ("account", "login", "authentication", "账号", "账户", "登录", "认证")),
    ("call_communication", ("incoming call", "outgoing call", "phone call", "calling", "通话", "来电", "呼叫", "电话")),
    ("navigation_location", ("navigation", "route", "location", "地图", "导航", "路线", "定位")),
    ("connection_pairing", ("connection", "connect", "pairing", "disconnect", "bluetooth", "连接", "配对", "断连", "蓝牙")),
    ("audio_media", ("audio", "sound", "volume", "music", "声音", "音量", "无声", "音频", "播放")),
    ("display_projection", ("display", "screen", "projection", "black screen", "显示", "屏", "投屏", "黑屏", "hud")),
    ("control_interaction", ("button", "touch", "gesture", "control", "按键", "触摸", "手势", "控制", "交互")),
    ("stability_performance", ("crash", "reboot", "restart", "freeze", "lag", "崩溃", "闪退", "重启", "死机", "卡顿", "无响应")),
    ("data_sync", ("sync", "data", "download", "upload", "同步", "数据", "下载", "上传")),
    ("content_service", ("content", "news", "weather", "video", "内容", "新闻", "天气", "视频")),
    ("activation_availability", ("activation", "activate", "unavailable", "cannot use", "激活", "不可用", "无法使用", "功能不可用")),
)

SYMPTOM_TOKEN_TO_PROBLEM_TYPE = {
    "display-blank": "display_projection",
    "display-abnormal": "display_projection",
    "audio-none": "audio_media",
    "audio-abnormal": "audio_media",
    "connection-lost": "connection_pairing",
    "connection-fail": "connection_pairing",
    "connection-timeout": "connection_pairing",
    "login-fail": "authentication_account",
    "login-abnormal": "authentication_account",
    "nav-drift": "navigation_location",
    "nav-fail": "navigation_location",
    "nav-crash": "navigation_location",
    "charging-fail": "charging_energy",
    "update-fail": "update_programming",
    "app-crash": "stability_performance",
    "system-reboot": "stability_performance",
    "system-unresponsive": "stability_performance",
    "system-lag": "stability_performance",
    "feature-unavailable": "activation_availability",
}
PROBLEM_TYPE_ORDER = {
    problem_type_id: index
    for index, problem_type_id in enumerate(PROBLEM_TYPE_LABELS)
}
DECISION_TREE_SOURCE_STATUS = (
    "Decision-tree skeleton; recovery and escalation text is blank unless Ticket evidence exists"
)


def _scenario_text(scenario: dict[str, Any], fields: tuple[str, ...]) -> str:
    return " ".join(
        str(scenario.get(field, "") or "") for field in fields
    ).casefold()


def _contains_alias(text: str, alias: str) -> bool:
    normalized_alias = alias.casefold()
    if not normalized_alias:
        return False
    if normalized_alias.isascii():
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(normalized_alias)}(?![a-z0-9])",
                text,
            )
        )
    return normalized_alias in text


def enrich_service_function_source_evidence(
    trees: list[dict[str, Any]], metadata: dict[str, dict[str, Any]]
) -> None:
    """Add source-ticket mention counts without changing scenario classification."""
    metadata_texts = [
        _scenario_text(
            metadata_row,
            ("function", "title", "description_excerpt"),
        )
        for metadata_row in metadata.values()
    ]
    for tree in trees:
        service_function = SERVICE_FUNCTION_BY_ID[tree["service_function_id"]]
        aliases = service_function["aliases"]
        source_ticket_mention_count = sum(
            any(_contains_alias(source_text, alias) for alias in aliases)
            for source_text in metadata_texts
        )
        tree["source_ticket_mention_count"] = source_ticket_mention_count
        tree["phase09_evidence_status"] = (
            "field_scenario_data_available"
            if tree["scenario_count"]
            else "source_ticket_evidence_without_field_scenario"
            if source_ticket_mention_count
            else "no_source_ticket_evidence"
        )


def classify_engineering_family(scenario: dict[str, Any]) -> dict[str, Any]:
    """Classify the R&D responsibility domain from the original R&D function."""
    raw_function_text = _scenario_text(
        scenario, ("rep_function", "function", "function_responsible")
    )
    context_text = _scenario_text(
        scenario,
        (
            "affected_function",
            "affected_function_raw",
            "rep_title",
            "customer_symptom",
            "trigger_condition",
        ),
    )
    matches: list[tuple[int, int, int, dict[str, Any], str, str]] = []
    for catalog_index, item in enumerate(ENGINEERING_FAMILY_CATALOG):
        if item["engineering_family_id"] == "OTHER_UNCLASSIFIED":
            continue
        for alias in item["aliases"]:
            if _contains_alias(raw_function_text, alias):
                matches.append((2, len(alias), -catalog_index, item, alias, "R&D Function"))
            elif _contains_alias(context_text, alias):
                matches.append((1, len(alias), -catalog_index, item, alias, "Scenario Context"))
    if not matches:
        return {
            **ENGINEERING_FAMILY_BY_ID["OTHER_UNCLASSIFIED"],
            "match_source": "Fallback",
            "match_alias": "",
        }
    _, _, _, match, alias, match_source = max(matches)
    return {**match, "match_source": match_source, "match_alias": alias}


_SERVICE_FUNCTION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("CARPLAY", _CARPLAY_IDENTIFIERS),
    ("HICAR", _HICAR_IDENTIFIERS),
    ("MAP_SEAMLESS", ("map seamless", "amap seamless", "seamless amap")),
    ("NAVIGATION_AMAP", ("navigation amap", "navigation anmap", " amap", " anmap")),
    ("AI_TRAVEL_COMPANION", ("ai empowered travel companion", "travel companion")),
    ("POTHOLE_WARNING", ("pothole warning", "pothole")),
    ("HAZARD_PREVIEW", ("hazard preview",)),
    ("E_ROUTE", ("e-route", "e route", "eroute")),
    ("DESTINATION_SEARCH", ("destination search",)),
    ("PREMIUM_PARKING", ("premium parking experience", "on-street parking", "on street parking")),
    ("CHARGING_POI", ("charging poi", "charging pois")),
    ("AI_EMPOWERED_IPA", ("ai empowered ipa",)),
    ("IPA_DIRECT_COMMANDS", ("front seat just talk", "direct commands", "direct command")),
    ("IPA_PASSENGER_ENTERTAINMENT", ("speech for passenger entertainment", "passenger entertainment")),
    ("IPA_CAR_CONTROL", ("extended ipa car control",)),
    ("FORGOTTEN_PHONE_REMINDER", ("forgotten phone reminder", "forgotten phone")),
    ("QQ_MUSIC", ("qq music", "qq音乐")),
    ("XIMALAYA", ("ximalaya", "喜马拉雅")),
    ("NETEASE_CLOUD_MUSIC", ("netease cloud music", "netease", "网易云音乐")),
    ("SODA_MUSIC", ("soda music", "soda")),
    ("KUWO_MUSIC", ("kuwo music", "kuwo", "酷我音乐")),
    ("SING_ALONG_LYRICS", ("sing-along lyrics", "sing along lyrics", "singalong lyrics")),
    ("WESING", ("wesing", "we sing", "全民k歌")),
    ("IMMERSIVE_MUSIC", ("immersive music",)),
    ("DOLBY_ATMOS", ("dolby atmos",)),
    ("IQIYI", ("iqiyi", "爱奇艺")),
    ("TENCENT_VIDEO", ("tencent video", "腾讯视频")),
    ("BILIBILI", ("bilibili", "哔哩哔哩")),
    ("MIGU_GAMING", ("migu gaming", "migu game", "咪咕游戏")),
    ("DLNA_CASTING", ("china dlna casting", "dlna")),
    ("MYBMW_HOTSPOT_SCAN", ("hotspot via my bmw app scan", "my bmw app scan")),
    ("MY_MOMENTS", ("my moments", "mymoments")),
    ("MY_MODES", ("my modes", "mymodes")),
    ("WELCOME_ANIMATION", ("welcome animation",)),
    ("PHUD", ("phud experience", "phud")),
    ("ALL_APPS_GESTURE", ("five-finger pinch", "five finger pinch", "pinch/spread to all apps", "all apps menu")),
    ("SHOWROOM_EXPERIENCE", ("show room experience", "showroom experience")),
    ("MOMENTA_ADAS", ("momenta integration", "momenta")),
    ("WIRELESS_CHARGING", ("wireless charging",)),
    ("SMART_ACCESS", ("smart access", "digital key")),
    ("REMOTE_3D_VIEW", ("remote 3d", "remote view")),
    ("MYBMW_APP", ("my bmw app", "mybmw app")),
    ("ETC_CHINA", ("etc china",)),
    ("REAL_TIME_MONITORING", ("real time monitoring", "realtime monitoring")),
    ("MNO_CONNECTIVITY_CHINA", ("mno connectivity china", "mno connectivity")),
    ("UNICOM_CONNECTIVITY", ("unicom connectivity", "unicom")),
    ("ACALL", ("accident call", "accident assistance", "acall", "xcall", "事故救援")),
    ("BCALL", ("breakdown call", "bcall", "roadside", "道路救援")),
    ("ECALL", ("emergency call", "ecall", "e-call", "紧急呼叫")),
    ("MAPUP", ("map update", "mapup", "地图更新")),
    ("SOFTWARE_UPDATE", ("remote software upgrade", "rsu", "software update", "softwareupdate", "ota", "软件更新", "升级", "编程")),
    ("REMOTE_SERVICES", ("remote_in_car", "remote in car", "remote service", "remote-hmi", "r360", "my bmw", "bmw connected client", "远程服务")),
    ("MYTRIPS", ("my trips", "mytrips", "我的行程")),
    ("DRIVE_RECORDER", ("drive recorder", "apprecorder", "dashcam", "行车记录仪")),
    ("PERS_ESIM", ("pers.esim", "pers esim", "personalization esim", "个性化 esim")),
    ("ESIM", ("esim", "e sim")),
    ("PIA", ("personalization", "personalisation", "digital key", "smart access", "个性化")),
    ("ACCOUNT_PROBLEM", ("account problem", "gcdm", "账号", "账户", "login")),
    ("CCM", ("connected charging management", "charging management", "ccm")),
    ("BMW_CHARGING_CONTRACTS", ("charging contract", "charging contracts", "充电合约")),
    ("BAT", ("battery monitoring", "battery", "电池")),
    ("CHARGING", ("charging", "charge", "充电")),
    ("CBS", ("connected booking", "booking service", "预约服务")),
    ("BON_WEATHER", ("bon weather", "weather app", "天气")),
    ("QQ_MUSIC_HIMALAYA", ("qq music", "qq音乐", "himalaya", "喜马拉雅")),
    ("AM_SR", ("amazon music", "siriusxm", "sirius xm", "am/sr")),
    ("ENTERTAINMENT", ("gaming", "3rd party", "third party", "cn_gaming", "app store", "in-car store", "fod", "entertainment")),
    ("VIDEO", ("video", "vod", "视频")),
    ("USB", ("usb", "usb media")),
    ("NAVIGATION", ("navigation", "route guidance", "route", "ccp", "rtti", "导航")),
    ("DRIVER_ASSISTANCE", ("ad_apps", "ad_cnapps", "ad_functions", "driver assistance", "parking assist", "indoor parking", "speed limit", "adas")),
    ("CONNECTIVITY_GEN2", ("connectivity", "connecteddrive", "connected drive", "telematics", "vehicle online", "wireless services", "modem", "车联网")),
    ("AUDIO_MEDIA", ("audio infrastructure", "audio routing", "audiofocus", "audio mixing", "audiomanagement", "base audio", "klangeinstellungen")),
    ("HMI", ("hmi", "human machine interface", "ui toolkit", "core ui", "display", "hud", "phud", "cid", "人机交互")),
    ("IPA", ("intelligent personal assistant", "ipa intelligent", "智能语音助手")),
    ("TTS", ("text-to-speech", "text to speech", "tts", "语音合成")),
    ("SPEECH", ("speech", "voice interface", "voice operation", "dictation", "语音")),
    ("CAMERA_RECORDING", ("camera infrastructure", "snapshot", "rear view camera", "pvis", "interior sensing")),
    ("PLATFORM_SOFTWARE", ("android platform", "softwareplatform", "stability", "performance", "cdc platform")),
    ("VEHICLE_FEATURES", ("climate control", "climatic comfort", "interior light", "lighting", "seat comfort")),
    ("NEWS", ("news", "新闻")),
    ("PAYMENT", ("payment", "支付")),
)

_ENGINEERING_FAMILY_SERVICE_FALLBACKS = {
    "PHONE_PROJECTION": "CDUNSPECIFIED",
    "ENTERTAINMENT_APPS": "ENTERTAINMENT",
    "AUDIO_MEDIA_PLATFORM": "AUDIO_MEDIA",
    "DRIVER_ASSISTANCE": "DRIVER_ASSISTANCE",
    "ASSISTANCE_EMERGENCY": "ACALL",
    "REMOTE_MOBILE": "REMOTE_SERVICES",
    "CONNECTED_SERVICES": "CONNECTIVITY_GEN2",
    "NAVIGATION_LOCATION": "NAVIGATION",
    "PERSONALIZATION_IDENTITY": "PIA",
    "CHARGING_ENERGY": "CHARGING",
    "HMI_DISPLAY": "HMI",
    "SPEECH_IPA": "SPEECH",
    "PLATFORM_SOFTWARE": "PLATFORM_SOFTWARE",
    "CAMERA_RECORDING": "CAMERA_RECORDING",
    "VEHICLE_FEATURES": "VEHICLE_FEATURES",
    "OTHER_UNCLASSIFIED": "CDUNSPECIFIED",
}


def classify_service_function(scenario: dict[str, Any]) -> dict[str, Any]:
    """Choose the customer-facing service entry, independent of R&D ownership."""
    projection_identity = _specific_projection_identity(scenario)
    if projection_identity == "CarPlay":
        return {
            **SERVICE_FUNCTION_BY_ID["CARPLAY"],
            "match_source": "Specific phone-projection evidence",
        }
    if projection_identity == "HiCar":
        return {
            **SERVICE_FUNCTION_BY_ID["HICAR"],
            "match_source": "Specific phone-projection evidence",
        }
    if projection_identity:
        return {
            **SERVICE_FUNCTION_BY_ID["CDUNSPECIFIED"],
            "match_source": "Phone projection is not specifically identified",
        }
    customer_context = _scenario_text(
        scenario,
        (
            "affected_function",
            "affected_function_raw",
            "rep_title",
            "customer_symptom",
            "trigger_condition",
            "observed_behaviour",
            "expected_behaviour",
            "rep_function",
        ),
    )
    for service_function_id, aliases in _SERVICE_FUNCTION_RULES:
        if any(_contains_alias(customer_context, alias) for alias in aliases):
            return {
                **SERVICE_FUNCTION_BY_ID[service_function_id],
                "match_source": "Customer Scenario Context",
            }
    engineering_family = classify_engineering_family(scenario)
    fallback_id = _ENGINEERING_FAMILY_SERVICE_FALLBACKS[
        engineering_family["engineering_family_id"]
    ]
    return {
        **SERVICE_FUNCTION_BY_ID[fallback_id],
        "match_source": f"Engineering Family fallback: {engineering_family['engineering_family_id']}",
    }


def classify_problem_type(scenario: dict[str, Any]) -> str:
    """Classify a customer-facing issue type without changing Family ownership."""
    text = " ".join(
        str(scenario.get(field, "") or "")
        for field in (
            "customer_symptom",
            "trigger_condition",
            "observed_behaviour",
            "expected_behaviour",
            "rep_title",
        )
    ).casefold()
    for problem_type_id, keywords in PROBLEM_TYPE_RULES:
        if any(keyword.casefold() in text for keyword in keywords):
            return problem_type_id
    symptom_token = extract_symptom_keyword(
        str(scenario.get("customer_symptom", "") or "")
    )
    return SYMPTOM_TOKEN_TO_PROBLEM_TYPE.get(symptom_token, "other")


def _recorded_evidence_text(value: Any) -> str:
    """Return usable ticket evidence; do not turn an absent value into guidance."""
    text = str(value or "").strip()
    normalized = text.casefold()
    if normalized in {"", "none", "none recorded", "n/a", "na", "无"}:
        return ""
    no_recovery_markers = (
        "无恢复",
        "没有恢复",
        "暂无恢复",
        "未记录恢复",
        "未提供恢复",
        "无已知恢复",
        "无法恢复",
        "未能恢复",
        "无法解决",
        "无操作",
        "问题持续存在",
        "no recover",
        "no recovery",
        "no workaround",
        "no action",
        "not recoverable",
        "unresolved",
    )
    if any(marker in normalized for marker in no_recovery_markers):
        return ""
    if re.fullmatch(r"(?:无|没有|暂无|不适用)(?:[（(].*[）)])?", text):
        return ""
    return text


_DIAGNOSTIC_CONTEXT_SIGNALS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("device_apple", "Device: Apple / iPhone", ("iphone", "ios", "apple")),
    ("device_huawei", "Device: Huawei", ("huawei", "华为", "hicar", "hi car")),
    ("device_android", "Device: Android", ("android",)),
    ("connection_usb", "Connection: USB", ("usb", "type-c", "type c")),
    ("connection_bluetooth", "Connection: Bluetooth", ("bluetooth", "蓝牙")),
    ("connection_wireless", "Connection: Wireless", ("wireless", "wifi", "wi-fi", "wlan")),
    ("lifecycle_wake_up", "Lifecycle: Wake-up", ("wake", "sleep", "唤醒", "休眠")),
    ("lifecycle_reboot", "Lifecycle: Reboot", ("reboot", "restart", "重启", "复位")),
    ("lifecycle_driving", "Lifecycle: Driving", ("driving", "行驶", "行车")),
    ("lifecycle_parking", "Lifecycle: Parking", ("parking", "park", "泊车", "驻车")),
)

_DIAGNOSTIC_CONTEXT_FIELDS = (
    "customer_symptom",
    "trigger_condition",
    "observed_behaviour",
    "expected_behaviour",
    "rep_title",
)
_REPRODUCTION_CONTEXT_FIELDS = (
    "trigger_condition",
    "observed_behaviour",
    "expected_behaviour",
)


def _diagnostic_context(
    scenario: dict[str, Any],
    fields: tuple[str, ...] = _DIAGNOSTIC_CONTEXT_FIELDS,
) -> tuple[str, str]:
    """Return device, connection, and lifecycle facts explicitly present in Scenario text."""
    text = _scenario_text(scenario, fields)
    matches = [
        (context_key, label)
        for context_key, label, indicators in _DIAGNOSTIC_CONTEXT_SIGNALS
        if any(_contains_alias(text, indicator) for indicator in indicators)
    ]
    if not matches:
        return "no_context_evidence", "No additional context recorded"
    return "|".join(key for key, _ in matches), "; ".join(
        label for _, label in matches
    )


def _recorded_technical_text(value: Any) -> str:
    """Return a technical value only when the source recorded a non-placeholder value."""
    text = str(value or "").strip()
    if text.casefold() in {
        "",
        "none",
        "none recorded",
        "n/a",
        "na",
        "not recorded",
        "unknown",
        "无",
    }:
        return ""
    return text


def _technical_values(values: Any) -> list[str]:
    """Read the values portion of `Counter.most_common()` pairs without inference."""
    if isinstance(values, tuple) and len(values) == 2 and not isinstance(
        values[0], (list, tuple)
    ):
        values = [values]
    elif not isinstance(values, (list, tuple)):
        values = [values]

    result = []
    for item in values:
        value = item[0] if isinstance(item, (list, tuple)) and item else item
        text = _recorded_technical_text(value)
        if text and text not in result:
            result.append(text)
        if len(result) == 2:
            break
    return result


def _technical_evidence(scenario: dict[str, Any]) -> tuple[str, str]:
    """Build a label solely from recorded ECU, software version, and RCA values."""
    ecus = _technical_values(scenario.get("top_ecus") or scenario.get("rep_ecu"))
    software_versions = _technical_values(scenario.get("top_sw_versions"))
    root_cause = _recorded_technical_text(scenario.get("rca_root_cause"))
    parts = []
    if ecus:
        parts.append(f"ECU: {', '.join(ecus)}")
    if software_versions:
        parts.append(f"Software Version: {', '.join(software_versions)}")
    if root_cause:
        parts.append(f"Recorded RCA Root Cause: {root_cause[:160]}")
    if not parts:
        return "no_technical_evidence", "No recorded ECU, software version, or RCA root cause"
    return "|".join(clean_text(part) for part in parts), "; ".join(parts)


def _source_scenario_ids(scenarios: list[dict[str, Any]], limit: int = 20) -> str:
    scenario_ids = sorted(scenario["scenario_id"] for scenario in scenarios)
    visible_ids = scenario_ids[:limit]
    suffix = f"; +{len(scenario_ids) - limit} more" if len(scenario_ids) > limit else ""
    return "; ".join(visible_ids) + suffix


def _representative_evidence_text(
    scenarios: list[dict[str, Any]], field_name: str
) -> str:
    values = Counter(
        _recorded_evidence_text(scenario.get(field_name)) for scenario in scenarios
    )
    values.pop("", None)
    return values.most_common(1)[0][0] if values else ""


def _data_tree_node(
    *,
    tree_id: str,
    node_id: str,
    node_type: str,
    parent_node_id: str,
    data_key: str,
    question_or_action: str,
    evidence_required: str,
    scenarios: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "tree_id": tree_id,
        "node_id": node_id,
        "node_type": node_type,
        "parent_node_id": parent_node_id,
        "data_key": data_key,
        "question_or_action": question_or_action,
        "true_next_node": "",
        "false_next_node": "",
        "next_node": "",
        "evidence_required": evidence_required,
        "source_scenario_count": len(scenarios),
        "source_ticket_count": sum(
            int(scenario.get("ticket_count", 0) or 0) for scenario in scenarios
        ),
        "source_scenario_ids": _source_scenario_ids(scenarios),
        "node_source": "field_scenario_data",
    }


def _build_decision_tree_nodes(
    tree: dict[str, Any], tree_scenarios: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build a tree from recorded Scenario values, never from a static flow template."""
    root = _data_tree_node(
        tree_id=tree["tree_id"],
        node_id="ROOT",
        node_type="Entry",
        parent_node_id="",
        data_key=tree["service_function_id"],
        question_or_action=tree["service_function"],
        evidence_required="",
        scenarios=tree_scenarios,
    )
    nodes = [root]
    symptom_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for scenario in tree_scenarios:
        symptom_groups[(
            scenario["problem_type_id"],
            extract_symptom_keyword(scenario.get("customer_symptom", "")),
        )].append(scenario)

    for symptom_index, ((problem_type_id, symptom_key), symptom_scenarios) in enumerate(
        sorted(symptom_groups.items()), start=1
    ):
        symptom_node_id = f"SYM-{symptom_index:03d}"
        nodes.append(_data_tree_node(
            tree_id=tree["tree_id"],
            node_id=symptom_node_id,
            node_type="Customer Symptom",
            parent_node_id="ROOT",
            data_key=f"{problem_type_id}|{symptom_key}",
            question_or_action=_representative_evidence_text(
                symptom_scenarios, "customer_symptom"
            ),
            evidence_required="Customer Symptom",
            scenarios=symptom_scenarios,
        ))
        trigger_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for scenario in symptom_scenarios:
            trigger_groups[extract_trigger_keyword(
                scenario.get("trigger_condition", "")
            )].append(scenario)

        for trigger_index, (trigger_key, trigger_scenarios) in enumerate(
            sorted(trigger_groups.items()), start=1
        ):
            trigger_node_id = f"TRG-{symptom_index:03d}-{trigger_index:03d}"
            trigger_text = _representative_evidence_text(
                trigger_scenarios, "trigger_condition"
            )
            nodes.append(_data_tree_node(
                tree_id=tree["tree_id"],
                node_id=trigger_node_id,
                node_type="Trigger Condition",
                parent_node_id=symptom_node_id,
                data_key=trigger_key,
                question_or_action=trigger_text,
                evidence_required="Trigger Condition" if trigger_text else "",
                scenarios=trigger_scenarios,
            ))
            context_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            for scenario in trigger_scenarios:
                context_groups[_diagnostic_context(scenario)].append(scenario)
            for context_index, ((context_key, context_text), context_scenarios) in enumerate(
                sorted(context_groups.items()), start=1
            ):
                context_node_id = (
                    f"CTX-{symptom_index:03d}-{trigger_index:03d}-{context_index:03d}"
                )
                nodes.append(_data_tree_node(
                    tree_id=tree["tree_id"],
                    node_id=context_node_id,
                    node_type="Diagnostic Context",
                    parent_node_id=trigger_node_id,
                    data_key=context_key,
                    question_or_action=context_text,
                    evidence_required="Customer Symptom; Trigger Condition; Observed Behaviour",
                    scenarios=context_scenarios,
                ))
                technical_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
                for scenario in context_scenarios:
                    technical_groups[_technical_evidence(scenario)].append(scenario)
                for technical_index, ((technical_key, technical_text), technical_scenarios) in enumerate(
                    sorted(technical_groups.items()), start=1
                ):
                    technical_node_id = (
                        f"TEC-{symptom_index:03d}-{trigger_index:03d}-"
                        f"{context_index:03d}-{technical_index:03d}"
                    )
                    nodes.append(_data_tree_node(
                        tree_id=tree["tree_id"],
                        node_id=technical_node_id,
                        node_type="Technical Evidence",
                        parent_node_id=context_node_id,
                        data_key=technical_key,
                        question_or_action=technical_text,
                        evidence_required="Assigned ECU; Software Version; RCA Root Cause",
                        scenarios=technical_scenarios,
                    ))
                    recovery_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
                    for scenario in technical_scenarios:
                        recovery_groups[_recorded_evidence_text(
                            scenario.get("recovery_action")
                        )].append(scenario)
                    for recovery_index, (recovery_action, recovery_scenarios) in enumerate(
                        sorted(recovery_groups.items()), start=1
                    ):
                        nodes.append(_data_tree_node(
                            tree_id=tree["tree_id"],
                            node_id=(
                                f"RES-{symptom_index:03d}-{trigger_index:03d}-"
                                f"{context_index:03d}-{technical_index:03d}-"
                                f"{recovery_index:03d}"
                            ),
                            node_type="Resolution",
                            parent_node_id=technical_node_id,
                            data_key=(
                                "recorded_recovery"
                                if recovery_action else "no_recovery_evidence"
                            ),
                            question_or_action=recovery_action,
                            evidence_required=(
                                "Ticket / Scenario Recovery Action"
                                if recovery_action else ""
                            ),
                            scenarios=recovery_scenarios,
                        ))
    return nodes


def build_service_decision_trees(
    scenarios: list[dict[str, Any]], families: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Build all catalogue trees and map every field Scenario to one entry."""
    family_lookup = {
        scenario["scenario_id"]: family["family_id"]
        for family in families
        for scenario in family.get("scenarios", [])
    }
    scenarios_by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
    mappings: list[dict[str, Any]] = []
    for scenario in scenarios:
        service_function = classify_service_function(scenario)
        service_function_id = service_function["service_function_id"]
        problem_type_id = classify_problem_type(scenario)
        reference_tree_ids = service_function["reference_tree_ids"]
        scenario["service_function_id"] = service_function_id
        scenario["service_function"] = service_function["service_function"]
        scenario["service_domain"] = service_function["service_domain"]
        scenario["service_function_catalog_status"] = service_function["catalog_status"]
        scenario["reference_tree_ids"] = reference_tree_ids
        scenario["service_function_match_source"] = service_function["match_source"]
        scenario["problem_type_id"] = problem_type_id
        scenario["problem_type"] = PROBLEM_TYPE_LABELS[problem_type_id]
        scenarios_by_service[service_function_id].append(scenario)
        mappings.append({
            "scenario_id": scenario["scenario_id"],
            "tree_id": f"DT-{service_function_id}",
            "family_id": family_lookup.get(scenario["scenario_id"], ""),
            "service_function_id": service_function_id,
            "service_function": service_function["service_function"],
            "problem_type_id": problem_type_id,
            "problem_type": PROBLEM_TYPE_LABELS[problem_type_id],
            "mapping_role": "Primary triage entry",
        })

    trees: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    for service_function in SERVICE_FUNCTION_CATALOG:
        service_function_id = service_function["service_function_id"]
        tree_scenarios = scenarios_by_service[service_function_id]
        problem_type_ids = sorted(
            {scenario["problem_type_id"] for scenario in tree_scenarios},
            key=lambda problem_type_id: PROBLEM_TYPE_ORDER[problem_type_id],
        )
        tree = {
            "tree_id": f"DT-{service_function_id}",
            "tree_name": f"{service_function['service_function']} Decision Tree",
            "service_function_id": service_function_id,
            "service_function": service_function["service_function"],
            "service_function_label": service_function["display_name"],
            "service_domain": service_function["service_domain"],
            "catalog_status": service_function["catalog_status"],
            "reference_tree_ids": service_function["reference_tree_ids"],
            "scenario_count": len(tree_scenarios),
            "ticket_count": sum(int(scenario.get("ticket_count", 0) or 0) for scenario in tree_scenarios),
            "problem_type_ids": problem_type_ids,
            "problem_types": [PROBLEM_TYPE_LABELS[item] for item in problem_type_ids],
            "tree_generation": "field_scenario_data",
            "source_status": (
                "Data-derived from Phase 09 Customer Symptom, Trigger Condition, and "
                "recorded Recovery Action; after-sales images are reference only"
            ),
        }
        tree_nodes = _build_decision_tree_nodes(tree, tree_scenarios)
        tree["node_count"] = len(tree_nodes)
        tree["edge_count"] = max(0, len(tree_nodes) - 1)
        trees.append(tree)
        nodes.extend(tree_nodes)
    return trees, nodes, mappings


GUIDED_MAX_ISSUE_GROUPS = 8
GUIDED_SUMMARY_ITEM_LIMIT = 3

SYMPTOM_PATTERN_LABELS = {
    "connection-fail": "Cannot connect",
    "connection-lost": "Connection lost",
    "connection-timeout": "Connection timeout",
    "cannot-function": "Cannot use / unavailable",
    "audio-none": "No audio",
    "audio-abnormal": "Abnormal audio",
    "display-blank": "Blank display",
    "display-abnormal": "Abnormal display",
    "system-unresponsive": "System unresponsive",
    "system-lag": "System lag",
    "app-crash": "Application crash",
    "system-reboot": "System reboot",
    "nav-drift": "Location drift",
    "nav-fail": "Navigation unavailable",
    "nav-crash": "Navigation crash",
    "login-fail": "Login failure",
    "login-abnormal": "Login abnormal",
    "charging-fail": "Charging unavailable",
    "update-fail": "Update failure",
    "feature-unavailable": "Feature unavailable",
    "other": "Other observed symptom pattern",
    "unknown": "Unspecified symptom pattern",
}

TRIGGER_PATTERN_LABELS = {
    "connecting": "During connection",
    "after-connect": "After connection",
    "disconnecting": "During disconnection",
    "playing": "During media playback",
    "switching": "During switching",
    "after-switch": "After switching",
    "calling": "During a call",
    "navigating": "During navigation",
    "starting": "During start-up",
    "after-start": "After start-up",
    "wake-up": "After wake-up",
    "after-reboot": "After reboot",
    "boot": "During boot",
    "charging": "During charging",
    "driving": "While driving",
    "parking": "While parking",
    "after-update": "After update",
    "after-programming": "After programming",
    "persistent": "Persistent / repeated condition",
    "intermittent": "Intermittent condition",
    "other": "Other reported trigger",
    "unknown": "No trigger recorded",
}


def _guided_symptom_pattern(scenario: dict[str, Any]) -> str:
    text = _scenario_text(
        scenario,
        ("customer_symptom", "observed_behaviour", "rep_title"),
    )
    if any(marker in text for marker in ("cannot connect", "connection fail", "connection fails")):
        return "connection-fail"
    if any(marker in text for marker in ("disconnect", "connection lost")):
        return "connection-lost"
    if any(marker in text for marker in ("no sound", "no audio")):
        return "audio-none"
    if "black screen" in text:
        return "display-blank"
    return extract_symptom_keyword(str(scenario.get("customer_symptom", "") or ""))


def _guided_trigger_pattern(scenario: dict[str, Any]) -> str:
    trigger_text = str(scenario.get("trigger_condition", "") or "").casefold()
    english_patterns = (
        ("connecting", "connecting"),
        ("disconnect", "disconnecting"),
        ("after reboot", "after-reboot"),
        ("restart", "after-reboot"),
        ("wake up", "wake-up"),
        ("wakeup", "wake-up"),
        ("sleep", "wake-up"),
        ("driving", "driving"),
        ("parking", "parking"),
        ("calling", "calling"),
        ("phone call", "calling"),
        ("navigation", "navigating"),
        ("playing", "playing"),
        ("playback", "playing"),
        ("switching", "switching"),
        ("after update", "after-update"),
    )
    for pattern, trigger_key in english_patterns:
        if pattern in trigger_text:
            return trigger_key
    return extract_trigger_keyword(trigger_text)


def _ticket_total(scenarios: list[dict[str, Any]]) -> int:
    return sum(int(scenario.get("ticket_count", 0) or 0) for scenario in scenarios)


def _sorted_weighted_values(values: Counter[str]) -> list[tuple[str, int]]:
    return sorted(values.items(), key=lambda item: (-item[1], item[0]))


def _top_observed_summary(
    *,
    prefix: str,
    values: Counter[str],
    labels: dict[str, str] | None = None,
    limit: int = GUIDED_SUMMARY_ITEM_LIMIT,
    fallback: str = "No recorded value",
) -> tuple[str, str]:
    ranked_values = _sorted_weighted_values(values)
    if not ranked_values:
        return "no_recorded_value", fallback
    selected_values = ranked_values[:limit]
    summary = "; ".join(
        f"{(labels or {}).get(value, value)} ({count} tickets)"
        for value, count in selected_values
    )
    hidden_count = len(ranked_values) - len(selected_values)
    if hidden_count:
        summary += f"; Other observed variants ({hidden_count} groups)"
    return "|".join(value for value, _ in selected_values), f"{prefix}: {summary}"


def _guided_context_summary(scenarios: list[dict[str, Any]]) -> tuple[str, str]:
    context_counts: Counter[str] = Counter()
    for scenario in scenarios:
        context_key, context_text = _diagnostic_context(scenario)
        if context_key == "no_context_evidence":
            continue
        for context_item in context_text.split("; "):
            context_counts[context_item] += int(scenario.get("ticket_count", 0) or 0)
    return _top_observed_summary(
        prefix="Observed diagnostic context",
        values=context_counts,
        fallback="No additional context recorded",
    )


def _guided_technical_summary(scenarios: list[dict[str, Any]]) -> tuple[str, str]:
    total_tickets = _ticket_total(scenarios)
    ecu_tickets = 0
    software_tickets = 0
    rca_tickets = 0
    ecu_values: Counter[str] = Counter()
    software_values: Counter[str] = Counter()
    for scenario in scenarios:
        ticket_count = int(scenario.get("ticket_count", 0) or 0)
        ecus = _technical_values(scenario.get("top_ecus") or scenario.get("rep_ecu"))
        software_versions = _technical_values(scenario.get("top_sw_versions"))
        root_cause = _recorded_technical_text(scenario.get("rca_root_cause"))
        if ecus:
            ecu_tickets += ticket_count
            for ecu in ecus:
                ecu_values[ecu] += ticket_count
        if software_versions:
            software_tickets += ticket_count
            for software_version in software_versions:
                software_values[software_version] += ticket_count
        if root_cause:
            rca_tickets += ticket_count
    parts = [
        f"ECU evidence: {ecu_tickets}/{total_tickets} tickets",
        f"Software version evidence: {software_tickets}/{total_tickets} tickets",
        f"Recorded RCA text: {rca_tickets}/{total_tickets} tickets",
    ]
    top_ecus = _sorted_weighted_values(ecu_values)[:2]
    if top_ecus:
        parts.append(
            "Top ECU: " + ", ".join(
                f"{ecu} ({count})" for ecu, count in top_ecus
            )
        )
    top_software_versions = _sorted_weighted_values(software_values)[:2]
    if top_software_versions:
        parts.append(
            "Top software version: " + ", ".join(
                f"{software_version} ({count})"
                for software_version, count in top_software_versions
            )
        )
    evidence_key = (
        f"ecu:{int(ecu_tickets > 0)}|software:{int(software_tickets > 0)}|"
        f"rca:{int(rca_tickets > 0)}"
    )
    return evidence_key, "; ".join(parts)


def _guided_recovery_summary(
    scenarios: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    recovery_scenarios: list[dict[str, Any]] = []
    no_recovery_scenarios: list[dict[str, Any]] = []
    recovery_counts: Counter[str] = Counter()
    for scenario in scenarios:
        recovery_action = _recorded_evidence_text(scenario.get("recovery_action"))
        if recovery_action:
            recovery_scenarios.append(scenario)
            recovery_counts[recovery_action] += int(scenario.get("ticket_count", 0) or 0)
        else:
            no_recovery_scenarios.append(scenario)

    nodes = []
    if recovery_scenarios:
        ranked_actions = _sorted_weighted_values(recovery_counts)[:GUIDED_SUMMARY_ITEM_LIMIT]
        if len(ranked_actions) == 1:
            recovery_text = f"{ranked_actions[0][0]} ({ranked_actions[0][1]} tickets)"
        else:
            recovery_text = "Recorded recovery actions: " + "; ".join(
                f"{action} ({count} tickets)" for action, count in ranked_actions
            )
            if len(recovery_counts) > len(ranked_actions):
                recovery_text += (
                    f"; Other recorded actions ({len(recovery_counts) - len(ranked_actions)} groups)"
                )
        nodes.append({
            "data_key": "recorded_recovery_actions",
            "question_or_action": recovery_text,
            "evidence_required": "Ticket / Scenario Recovery Action",
            "scenarios": recovery_scenarios,
        })
    if no_recovery_scenarios:
        nodes.append({
            "data_key": "no_recorded_recovery",
            "question_or_action": "",
            "evidence_required": "",
            "scenarios": no_recovery_scenarios,
        })
    return nodes, no_recovery_scenarios


def _guided_tree_node(
    *,
    tree_id: str,
    node_id: str,
    node_type: str,
    parent_node_id: str,
    data_key: str,
    question_or_action: str,
    evidence_required: str,
    scenarios: list[dict[str, Any]],
) -> dict[str, Any]:
    node = _data_tree_node(
        tree_id=tree_id,
        node_id=node_id,
        node_type=node_type,
        parent_node_id=parent_node_id,
        data_key=data_key,
        question_or_action=question_or_action,
        evidence_required=evidence_required,
        scenarios=scenarios,
    )
    node["node_source"] = "field_scenario_guided_triage"
    return node


def _guided_issue_groups(
    scenarios: list[dict[str, Any]],
) -> list[tuple[str, str, list[dict[str, Any]]]]:
    by_problem_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for scenario in scenarios:
        by_problem_type[scenario["problem_type_id"]].append(scenario)
    ordered_groups = sorted(
        by_problem_type.items(),
        key=lambda item: (-_ticket_total(item[1]), item[0]),
    )
    kept_groups = ordered_groups[:GUIDED_MAX_ISSUE_GROUPS]
    result = [
        (problem_type_id, PROBLEM_TYPE_LABELS[problem_type_id], issue_scenarios)
        for problem_type_id, issue_scenarios in kept_groups
    ]
    low_frequency_scenarios = [
        scenario
        for _, issue_scenarios in ordered_groups[GUIDED_MAX_ISSUE_GROUPS:]
        for scenario in issue_scenarios
    ]
    if low_frequency_scenarios:
        result.append((
            "other_observed_issue_groups",
            "Other observed issue groups",
            low_frequency_scenarios,
        ))
    return result


def build_guided_diagnostic_trees(
    scenarios: list[dict[str, Any]], families: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Build one compact, evidence-grounded diagnostic tree per Service Function."""
    _, _, mappings = build_service_decision_trees(scenarios, families)
    scenarios_by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for scenario in scenarios:
        scenarios_by_service[scenario["service_function_id"]].append(scenario)

    trees = []
    nodes = []
    for service_function in SERVICE_FUNCTION_CATALOG:
        service_function_id = service_function["service_function_id"]
        tree_id = f"DT-{service_function_id}"
        tree_scenarios = scenarios_by_service[service_function_id]
        problem_type_ids = sorted(
            {scenario["problem_type_id"] for scenario in tree_scenarios},
            key=lambda problem_type_id: PROBLEM_TYPE_ORDER[problem_type_id],
        )
        issue_groups = _guided_issue_groups(tree_scenarios)
        tree = {
            "tree_id": tree_id,
            "tree_name": f"{service_function['service_function']} Guided Diagnostic Tree",
            "service_function_id": service_function_id,
            "service_function": service_function["service_function"],
            "service_function_label": service_function["display_name"],
            "service_domain": service_function["service_domain"],
            "catalog_status": service_function["catalog_status"],
            "reference_tree_ids": service_function["reference_tree_ids"],
            "scenario_count": len(tree_scenarios),
            "ticket_count": _ticket_total(tree_scenarios),
            "problem_type_ids": problem_type_ids,
            "problem_types": [PROBLEM_TYPE_LABELS[item] for item in problem_type_ids],
            "tree_generation": "field_scenario_guided_triage_v1",
            "guided_branch_count": len(issue_groups),
            "source_status": (
                "Guided tree aggregated from Phase 09 observations; reference images are "
                "not copied; recovery text is recorded evidence only"
            ),
        }
        tree_nodes = [_guided_tree_node(
            tree_id=tree_id,
            node_id="START",
            node_type="Entry",
            parent_node_id="",
            data_key=service_function_id,
            question_or_action=service_function["service_function"],
            evidence_required="",
            scenarios=tree_scenarios,
        )]
        for issue_index, (
            problem_type_id,
            issue_label,
            issue_scenarios,
        ) in enumerate(issue_groups, start=1):
            issue_node_id = f"ISS-{issue_index:02d}"
            tree_nodes.append(_guided_tree_node(
                tree_id=tree_id,
                node_id=issue_node_id,
                node_type="Issue Group",
                parent_node_id="START",
                data_key=problem_type_id,
                question_or_action=issue_label,
                evidence_required="Customer Symptom",
                scenarios=issue_scenarios,
            ))
            symptom_values = Counter()
            trigger_values = Counter()
            for scenario in issue_scenarios:
                ticket_count = int(scenario.get("ticket_count", 0) or 0)
                symptom_values[_guided_symptom_pattern(scenario)] += ticket_count
                trigger_values[_guided_trigger_pattern(scenario)] += ticket_count
            symptom_key, symptom_text = _top_observed_summary(
                prefix="Observed symptom patterns",
                values=symptom_values,
                labels=SYMPTOM_PATTERN_LABELS,
            )
            symptom_node_id = f"SYM-{issue_index:02d}"
            tree_nodes.append(_guided_tree_node(
                tree_id=tree_id,
                node_id=symptom_node_id,
                node_type="Symptom Pattern",
                parent_node_id=issue_node_id,
                data_key=symptom_key,
                question_or_action=symptom_text,
                evidence_required="Customer Symptom",
                scenarios=issue_scenarios,
            ))
            trigger_key, trigger_text = _top_observed_summary(
                prefix="Observed trigger patterns",
                values=trigger_values,
                labels=TRIGGER_PATTERN_LABELS,
            )
            trigger_node_id = f"TRG-{issue_index:02d}"
            tree_nodes.append(_guided_tree_node(
                tree_id=tree_id,
                node_id=trigger_node_id,
                node_type="Trigger Pattern",
                parent_node_id=symptom_node_id,
                data_key=trigger_key,
                question_or_action=trigger_text,
                evidence_required="Trigger Condition",
                scenarios=issue_scenarios,
            ))
            context_key, context_text = _guided_context_summary(issue_scenarios)
            context_node_id = f"CTX-{issue_index:02d}"
            tree_nodes.append(_guided_tree_node(
                tree_id=tree_id,
                node_id=context_node_id,
                node_type="Diagnostic Context",
                parent_node_id=trigger_node_id,
                data_key=context_key,
                question_or_action=context_text,
                evidence_required="Customer Symptom; Trigger Condition; Observed Behaviour",
                scenarios=issue_scenarios,
            ))
            technical_key, technical_text = _guided_technical_summary(issue_scenarios)
            technical_node_id = f"TEC-{issue_index:02d}"
            tree_nodes.append(_guided_tree_node(
                tree_id=tree_id,
                node_id=technical_node_id,
                node_type="Technical Evidence",
                parent_node_id=context_node_id,
                data_key=technical_key,
                question_or_action=technical_text,
                evidence_required="Assigned ECU; Software Version; RCA Root Cause",
                scenarios=issue_scenarios,
            ))
            recovery_nodes, _ = _guided_recovery_summary(issue_scenarios)
            for recovery_index, recovery_node in enumerate(recovery_nodes, start=1):
                tree_nodes.append(_guided_tree_node(
                    tree_id=tree_id,
                    node_id=f"RES-{issue_index:02d}-{recovery_index:02d}",
                    node_type="Resolution",
                    parent_node_id=technical_node_id,
                    data_key=recovery_node["data_key"],
                    question_or_action=recovery_node["question_or_action"],
                    evidence_required=recovery_node["evidence_required"],
                    scenarios=recovery_node["scenarios"],
                ))
        tree["node_count"] = len(tree_nodes)
        tree["edge_count"] = max(0, len(tree_nodes) - 1)
        trees.append(tree)
        nodes.extend(tree_nodes)
    return trees, nodes, mappings


def _symptom_solution_tree_node(
    *,
    tree_id: str,
    node_id: str,
    node_type: str,
    parent_node_id: str,
    data_key: str,
    question_or_action: str,
    evidence_required: str,
    scenarios: list[dict[str, Any]],
) -> dict[str, Any]:
    node = _data_tree_node(
        tree_id=tree_id,
        node_id=node_id,
        node_type=node_type,
        parent_node_id=parent_node_id,
        data_key=data_key,
        question_or_action=question_or_action,
        evidence_required=evidence_required,
        scenarios=scenarios,
    )
    node["node_source"] = "field_scenario_symptom_solution"
    node["solution_status"] = (
        "ticket_recorded_solution"
        if node_type == "Ticket Solution" and question_or_action
        else "no_ticket_solution_recorded"
        if node_type == "Ticket Solution"
        else ""
    )
    return node


def build_symptom_solution_trees(
    scenarios: list[dict[str, Any]], families: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Build symptom-driven navigation trees with ticket-recorded solution leaves."""
    _, _, mappings = build_service_decision_trees(scenarios, families)
    scenarios_by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for scenario in scenarios:
        scenarios_by_service[scenario["service_function_id"]].append(scenario)

    trees = []
    nodes = []
    for service_function in SERVICE_FUNCTION_CATALOG:
        service_function_id = service_function["service_function_id"]
        tree_id = f"DT-{service_function_id}"
        tree_scenarios = scenarios_by_service[service_function_id]
        problem_type_ids = sorted(
            {scenario["problem_type_id"] for scenario in tree_scenarios},
            key=lambda problem_type_id: PROBLEM_TYPE_ORDER[problem_type_id],
        )
        issue_groups = _guided_issue_groups(tree_scenarios)
        tree = {
            "tree_id": tree_id,
            "tree_name": f"{service_function['service_function']} Symptom-to-Solution Tree",
            "service_function_id": service_function_id,
            "service_function": service_function["service_function"],
            "service_function_label": service_function["display_name"],
            "service_domain": service_function["service_domain"],
            "catalog_status": service_function["catalog_status"],
            "reference_tree_ids": service_function["reference_tree_ids"],
            "scenario_count": len(tree_scenarios),
            "ticket_count": _ticket_total(tree_scenarios),
            "problem_type_ids": problem_type_ids,
            "problem_types": [PROBLEM_TYPE_LABELS[item] for item in problem_type_ids],
            "tree_generation": "field_scenario_symptom_solution_v1",
            "guided_branch_count": len(issue_groups),
            "source_ticket_mention_count": 0,
            "phase09_evidence_status": "not_evaluated",
            "diagnostic_layers": (
                "Issue Group",
                "Symptom Pattern",
                "Trigger Pattern",
                "Ticket Solution",
            ),
            "solution_source_policy": (
                "Solution text is copied only from recorded Ticket Recovery Action; "
                "it is not a root-cause finding or an approved repair instruction"
            ),
            "source_status": (
                "Symptom-driven navigation from Phase 09 customer observations; "
                "Ticket-recorded solution text is shown only when available and is blank otherwise"
            ),
        }
        tree_nodes = [_symptom_solution_tree_node(
            tree_id=tree_id,
            node_id="START",
            node_type="Entry",
            parent_node_id="",
            data_key=service_function_id,
            question_or_action=service_function["service_function"],
            evidence_required="",
            scenarios=tree_scenarios,
        )]
        for issue_index, (
            problem_type_id,
            issue_label,
            issue_scenarios,
        ) in enumerate(issue_groups, start=1):
            issue_node_id = f"ISS-{issue_index:02d}"
            tree_nodes.append(_symptom_solution_tree_node(
                tree_id=tree_id,
                node_id=issue_node_id,
                node_type="Issue Group",
                parent_node_id="START",
                data_key=problem_type_id,
                question_or_action=issue_label,
                evidence_required="Customer Symptom",
                scenarios=issue_scenarios,
            ))
            symptom_values = Counter()
            trigger_values = Counter()
            for scenario in issue_scenarios:
                ticket_count = int(scenario.get("ticket_count", 0) or 0)
                symptom_values[_guided_symptom_pattern(scenario)] += ticket_count
                trigger_values[_guided_trigger_pattern(scenario)] += ticket_count
            symptom_key, symptom_text = _top_observed_summary(
                prefix="Observed symptom patterns",
                values=symptom_values,
                labels=SYMPTOM_PATTERN_LABELS,
            )
            symptom_node_id = f"SYM-{issue_index:02d}"
            tree_nodes.append(_symptom_solution_tree_node(
                tree_id=tree_id,
                node_id=symptom_node_id,
                node_type="Symptom Pattern",
                parent_node_id=issue_node_id,
                data_key=symptom_key,
                question_or_action=symptom_text,
                evidence_required="Customer Symptom",
                scenarios=issue_scenarios,
            ))
            trigger_key, trigger_text = _top_observed_summary(
                prefix="Observed trigger patterns",
                values=trigger_values,
                labels=TRIGGER_PATTERN_LABELS,
            )
            trigger_node_id = f"TRG-{issue_index:02d}"
            tree_nodes.append(_symptom_solution_tree_node(
                tree_id=tree_id,
                node_id=trigger_node_id,
                node_type="Trigger Pattern",
                parent_node_id=symptom_node_id,
                data_key=trigger_key,
                question_or_action=trigger_text,
                evidence_required="Trigger Condition",
                scenarios=issue_scenarios,
            ))
            solution_nodes, _ = _guided_recovery_summary(issue_scenarios)
            for solution_index, solution_node in enumerate(solution_nodes, start=1):
                tree_nodes.append(_symptom_solution_tree_node(
                    tree_id=tree_id,
                    node_id=f"SOL-{issue_index:02d}-{solution_index:02d}",
                    node_type="Ticket Solution",
                    parent_node_id=trigger_node_id,
                    data_key=(
                        "ticket_recorded_solution"
                        if solution_node["question_or_action"]
                        else "no_ticket_solution_recorded"
                    ),
                    question_or_action=solution_node["question_or_action"],
                    evidence_required=solution_node["evidence_required"],
                    scenarios=solution_node["scenarios"],
                ))
        tree["node_count"] = len(tree_nodes)
        tree["edge_count"] = max(0, len(tree_nodes) - 1)
        trees.append(tree)
        nodes.extend(tree_nodes)
    return trees, nodes, mappings


AFTER_SALES_EVIDENCE_WORKFLOW_ID = "after_sales_evidence_workflow_v1"
AFTER_SALES_EVIDENCE_WORKFLOW_STAGES = (
    "Customer Complaint",
    "Precondition Evidence",
    "Reproduction Context",
    "Technical Evidence",
    "Recorded Outcome",
    "Escalation Package",
)


def after_sales_evidence_workflow_metadata() -> dict[str, Any]:
    """Return the generic workflow contract independently of any service tree."""
    return {
        "template_id": AFTER_SALES_EVIDENCE_WORKFLOW_ID,
        "stage_sequence": list(AFTER_SALES_EVIDENCE_WORKFLOW_STAGES),
        "ticket_mapping_policy": (
            "Service Function derives from defect evidence, not from "
            "reference-tree assignment."
        ),
        "reference_use_policy": (
            "Reference trees contribute workflow-stage patterns only; their individual "
            "nodes and paths are not copied into data-derived trees."
        ),
        "decision_policy": (
            "Evidence collection only; no automatic Yes/No decision, root-cause "
            "diagnosis, repair instruction, or escalation instruction."
        ),
    }

_PRECONDITION_EVIDENCE_SIGNALS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "entitlement",
        "Entitlement / contract / subscription",
        (
            "eligible", "eligibility", "entitlement", "contract", "subscription",
            "booked", "booking", "sa code", "service booked", "资格", "合约",
            "订阅", "已订购", "预订",
        ),
    ),
    (
        "activation",
        "Activation / provisioning",
        (
            "activation", "activate", "provision", "provisioning", "preactivation",
            "激活", "开通", "预激活", "配置",
        ),
    ),
    (
        "account_profile",
        "Account / profile / privacy",
        (
            "account", "login", "profile", "bmw id", "privacy", "gcdm",
            "账号", "账户", "登录", "个人资料", "隐私",
        ),
    ),
    (
        "vehicle_online",
        "Vehicle online / connectivity",
        (
            "vehicle online", "online", "network", "connectivity", "sim card",
            "mqtt", "http", "vehicle data", "在线", "网络", "车联网", "sim卡",
        ),
    ),
)


def _workflow_precondition_summary(
    scenarios: list[dict[str, Any]],
) -> tuple[str, str]:
    """Summarize prerequisite evidence actually mentioned in the source records."""
    total_tickets = _ticket_total(scenarios)
    observed_counts: Counter[str] = Counter()
    observed_keys = []
    for evidence_key, evidence_label, indicators in _PRECONDITION_EVIDENCE_SIGNALS:
        covered_tickets = 0
        for scenario in scenarios:
            text = _scenario_text(
                scenario,
                (
                    "customer_symptom",
                    "trigger_condition",
                    "observed_behaviour",
                    "rep_title",
                ),
            )
            if any(indicator.casefold() in text for indicator in indicators):
                covered_tickets += int(scenario.get("ticket_count", 0) or 0)
        if covered_tickets:
            observed_keys.append(evidence_key)
            observed_counts[evidence_label] = covered_tickets
    if not observed_counts:
        return "precondition_not_recorded", "Precondition evidence: not recorded"
    observed_text = "; ".join(
        f"{label} ({count}/{total_tickets} tickets)"
        for label, count in _sorted_weighted_values(observed_counts)
    )
    return "|".join(observed_keys), f"Observed precondition evidence: {observed_text}"


def _workflow_reproduction_context_summary(
    scenarios: list[dict[str, Any]],
) -> tuple[str, str]:
    context_key, context_text = _guided_context_summary(scenarios)
    if context_key == "no_recorded_value":
        return "reproduction_context_not_recorded", "Observed reproduction context: not recorded"
    prefix = "Observed diagnostic context: "
    if context_text.startswith(prefix):
        context_text = context_text[len(prefix):]
    return context_key, f"Observed reproduction context: {context_text}"


def _workflow_technical_evidence_summary(
    scenarios: list[dict[str, Any]],
) -> tuple[str, str]:
    """Report technical-evidence coverage without claiming a diagnosis or root cause."""
    total_tickets = _ticket_total(scenarios)
    ecu_tickets = 0
    software_tickets = 0
    rca_tickets = 0
    for scenario in scenarios:
        ticket_count = int(scenario.get("ticket_count", 0) or 0)
        if _technical_values(scenario.get("top_ecus") or scenario.get("rep_ecu")):
            ecu_tickets += ticket_count
        if _technical_values(scenario.get("top_sw_versions")):
            software_tickets += ticket_count
        if _recorded_technical_text(scenario.get("rca_root_cause")):
            rca_tickets += ticket_count
    evidence_key = (
        f"ecu:{int(ecu_tickets > 0)}|software:{int(software_tickets > 0)}|"
        f"rca:{int(rca_tickets > 0)}"
    )
    return evidence_key, (
        "Technical evidence coverage: "
        f"ECU: {ecu_tickets}/{total_tickets} tickets; "
        f"Software version: {software_tickets}/{total_tickets} tickets; "
        f"Recorded RCA text: {rca_tickets}/{total_tickets} tickets"
    )


def _workflow_recorded_outcome_summary(
    scenarios: list[dict[str, Any]],
) -> tuple[str, str, list[dict[str, Any]], int]:
    recovery_counts: Counter[str] = Counter()
    recovery_scenarios: list[dict[str, Any]] = []
    missing_recovery_tickets = 0
    for scenario in scenarios:
        ticket_count = int(scenario.get("ticket_count", 0) or 0)
        recovery_action = _recorded_evidence_text(scenario.get("recovery_action"))
        if recovery_action:
            recovery_counts[recovery_action] += ticket_count
            recovery_scenarios.append(scenario)
        else:
            missing_recovery_tickets += ticket_count
    if not recovery_counts:
        return "no_recorded_outcome", "", recovery_scenarios, missing_recovery_tickets
    ranked_actions = _sorted_weighted_values(recovery_counts)[:GUIDED_SUMMARY_ITEM_LIMIT]
    if len(ranked_actions) == 1:
        outcome_text = (
            f"Recorded recovery outcome: {ranked_actions[0][0]} "
            f"({ranked_actions[0][1]} tickets)"
        )
    else:
        outcome_text = "Recorded recovery outcomes: " + "; ".join(
            f"{action} ({count} tickets)" for action, count in ranked_actions
        )
        if len(recovery_counts) > len(ranked_actions):
            outcome_text += (
                f"; Other recorded outcomes ({len(recovery_counts) - len(ranked_actions)} groups)"
            )
    return "recorded_recovery_outcome", outcome_text, recovery_scenarios, missing_recovery_tickets


_WORKFLOW_PRECONDITION_VISUAL_DETAILS = {
    "entitlement": "Entitlement",
    "activation": "Activation",
    "account_profile": "Account",
    "vehicle_online": "Online",
}

_WORKFLOW_CONTEXT_VISUAL_DETAILS = {
    "Device: Apple / iPhone": "iPhone",
    "Device: Huawei": "Huawei",
    "Device: Android": "Android",
    "Connection: USB": "USB",
    "Connection: Bluetooth": "BT",
    "Connection: Wireless": "Wireless",
    "Lifecycle: Wake-up": "Wake-up",
    "Lifecycle: Reboot": "Reboot",
    "Lifecycle: Driving": "Driving",
    "Lifecycle: Parking": "Parking",
}


def _workflow_short_detail(
    data_key: str,
    details: dict[str, str],
    fallback: str,
) -> str:
    values = [details.get(value, value) for value in data_key.split("|")]
    values = [value for value in values if value and value not in {"no_recorded_value"}]
    if not values:
        return fallback
    visible_values = values[:3]
    result = "/".join(visible_values)
    if len(values) > len(visible_values):
        result += "/other"
    return result


def _workflow_short_outcome(outcome_text: str) -> str:
    text = re.sub(r"^Recorded recovery outcomes?:\s*", "", outcome_text).strip()
    if not text:
        return "No recorded recovery"
    first_outcome = text.split(";")[0].strip()
    return re.sub(r"\s*\(\d+ tickets\)$", "", first_outcome).strip()


def _workflow_visual_content(node: dict[str, Any]) -> tuple[str, str, str]:
    """Make the SVG scan-friendly while retaining full evidence in node detail."""
    node_type = node["node_type"]
    data_key = str(node["data_key"] or "")
    source_tickets = int(node["source_ticket_count"] or 0)
    ticket_badge = f"{source_tickets} tickets"

    if node_type == "Entry":
        return "Start", str(node["question_or_action"] or ""), ticket_badge
    if node_type == "Customer Complaint":
        return "Customer complaint?", str(node["question_or_action"] or ""), ticket_badge
    if node_type == "Precondition Evidence":
        return (
            "Check prerequisites",
            _workflow_short_detail(
                data_key,
                _WORKFLOW_PRECONDITION_VISUAL_DETAILS,
                "No prerequisite evidence",
            ),
            ticket_badge,
        )
    if node_type == "Reproduction Context":
        return (
            "Reproduce reported context",
            _workflow_short_detail(
                data_key,
                _WORKFLOW_CONTEXT_VISUAL_DETAILS,
                "No context recorded",
            ),
            ticket_badge,
        )
    if node_type == "Technical Evidence":
        detail_parts = []
        if "ecu:1" in data_key:
            detail_parts.append("ECU")
        if "software:1" in data_key:
            detail_parts.append("software")
        if "rca:1" in data_key:
            detail_parts.append("RCA")
        return (
            "Collect technical evidence",
            " / ".join(detail_parts) if detail_parts else "No technical evidence",
            ticket_badge,
        )
    if node_type == "Recorded Outcome":
        return (
            "Review recorded recovery",
            _workflow_short_outcome(str(node["question_or_action"] or "")),
            ticket_badge,
        )
    if node_type == "Escalation Package":
        match = re.search(r"(\d+)/(\d+) tickets", str(node["question_or_action"] or ""))
        missing_tickets = match.group(1) if match else str(source_tickets)
        return "Prepare evidence package", "No recorded recovery", f"{missing_tickets} tickets"
    return node_type, str(node["question_or_action"] or ""), ticket_badge


def _workflow_tree_node(
    *,
    tree_id: str,
    node_id: str,
    node_type: str,
    parent_node_id: str,
    data_key: str,
    question_or_action: str,
    evidence_required: str,
    scenarios: list[dict[str, Any]],
) -> dict[str, Any]:
    node = _data_tree_node(
        tree_id=tree_id,
        node_id=node_id,
        node_type=node_type,
        parent_node_id=parent_node_id,
        data_key=data_key,
        question_or_action=question_or_action,
        evidence_required=evidence_required,
        scenarios=scenarios,
    )
    node["node_source"] = "field_scenario_workflow_template"
    node["workflow_template"] = AFTER_SALES_EVIDENCE_WORKFLOW_ID
    node["decision_status"] = "evidence_collection_only"
    (
        node["visual_prompt"],
        node["visual_detail"],
        node["visual_evidence"],
    ) = _workflow_visual_content(node)
    return node


def build_workflow_template_trees(
    scenarios: list[dict[str, Any]], families: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Use a shared workflow pattern while deriving each tree's content from defects."""
    _, _, mappings = build_service_decision_trees(scenarios, families)
    scenarios_by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for scenario in scenarios:
        scenarios_by_service[scenario["service_function_id"]].append(scenario)

    trees = []
    nodes = []
    for service_function in SERVICE_FUNCTION_CATALOG:
        service_function_id = service_function["service_function_id"]
        tree_id = f"DT-{service_function_id}"
        tree_scenarios = scenarios_by_service[service_function_id]
        problem_type_ids = sorted(
            {scenario["problem_type_id"] for scenario in tree_scenarios},
            key=lambda problem_type_id: PROBLEM_TYPE_ORDER[problem_type_id],
        )
        issue_groups = _guided_issue_groups(tree_scenarios)
        tree = {
            "tree_id": tree_id,
            "tree_name": f"{service_function['service_function']} After-Sales Evidence Workflow",
            "service_function_id": service_function_id,
            "service_function": service_function["service_function"],
            "service_function_label": service_function["display_name"],
            "service_domain": service_function["service_domain"],
            "catalog_status": service_function["catalog_status"],
            "reference_tree_ids": service_function["reference_tree_ids"],
            "scenario_count": len(tree_scenarios),
            "ticket_count": _ticket_total(tree_scenarios),
            "problem_type_ids": problem_type_ids,
            "problem_types": [PROBLEM_TYPE_LABELS[item] for item in problem_type_ids],
            "tree_generation": "field_scenario_workflow_template_v1",
            "workflow_template": AFTER_SALES_EVIDENCE_WORKFLOW_ID,
            "workflow_stages": AFTER_SALES_EVIDENCE_WORKFLOW_STAGES,
            "guided_branch_count": len(issue_groups),
            "source_status": (
                "After-sales workflow-stage template inferred from reference-tree patterns; "
                "all node content and coverage are derived from Phase 09 evidence only"
            ),
        }
        tree_nodes = [_workflow_tree_node(
            tree_id=tree_id,
            node_id="START",
            node_type="Entry",
            parent_node_id="",
            data_key=service_function_id,
            question_or_action=service_function["service_function"],
            evidence_required="",
            scenarios=tree_scenarios,
        )]
        for issue_index, (
            problem_type_id,
            issue_label,
            issue_scenarios,
        ) in enumerate(issue_groups, start=1):
            complaint_node_id = f"COM-{issue_index:02d}"
            tree_nodes.append(_workflow_tree_node(
                tree_id=tree_id,
                node_id=complaint_node_id,
                node_type="Customer Complaint",
                parent_node_id="START",
                data_key=problem_type_id,
                question_or_action=issue_label,
                evidence_required="Customer Symptom",
                scenarios=issue_scenarios,
            ))
            precondition_key, precondition_text = _workflow_precondition_summary(
                issue_scenarios
            )
            precondition_node_id = f"PRE-{issue_index:02d}"
            tree_nodes.append(_workflow_tree_node(
                tree_id=tree_id,
                node_id=precondition_node_id,
                node_type="Precondition Evidence",
                parent_node_id=complaint_node_id,
                data_key=precondition_key,
                question_or_action=precondition_text,
                evidence_required="Ticket symptom, trigger, observed behaviour, and title",
                scenarios=issue_scenarios,
            ))
            context_key, context_text = _workflow_reproduction_context_summary(
                issue_scenarios
            )
            context_node_id = f"CTX-{issue_index:02d}"
            tree_nodes.append(_workflow_tree_node(
                tree_id=tree_id,
                node_id=context_node_id,
                node_type="Reproduction Context",
                parent_node_id=precondition_node_id,
                data_key=context_key,
                question_or_action=context_text,
                evidence_required="Customer Symptom; Trigger Condition; Observed Behaviour",
                scenarios=issue_scenarios,
            ))
            technical_key, technical_text = _workflow_technical_evidence_summary(
                issue_scenarios
            )
            technical_node_id = f"TEC-{issue_index:02d}"
            tree_nodes.append(_workflow_tree_node(
                tree_id=tree_id,
                node_id=technical_node_id,
                node_type="Technical Evidence",
                parent_node_id=context_node_id,
                data_key=technical_key,
                question_or_action=technical_text,
                evidence_required="Assigned ECU; Software Version; RCA Root Cause",
                scenarios=issue_scenarios,
            ))
            (
                outcome_key,
                outcome_text,
                outcome_scenarios,
                missing_recovery_tickets,
            ) = _workflow_recorded_outcome_summary(issue_scenarios)
            outcome_node_id = f"OUT-{issue_index:02d}"
            tree_nodes.append(_workflow_tree_node(
                tree_id=tree_id,
                node_id=outcome_node_id,
                node_type="Recorded Outcome",
                parent_node_id=technical_node_id,
                data_key=outcome_key,
                question_or_action=outcome_text,
                evidence_required=(
                    "Ticket / Scenario Recovery Action" if outcome_text else ""
                ),
                scenarios=outcome_scenarios or issue_scenarios,
            ))
            tree_nodes.append(_workflow_tree_node(
                tree_id=tree_id,
                node_id=f"ESC-{issue_index:02d}",
                node_type="Escalation Package",
                parent_node_id=outcome_node_id,
                data_key="missing_recorded_recovery",
                question_or_action=(
                    f"Escalation evidence package: {missing_recovery_tickets}/"
                    f"{_ticket_total(issue_scenarios)} tickets"
                ),
                evidence_required=(
                    "Customer Symptom; Trigger Condition; Technical Evidence"
                    if missing_recovery_tickets else ""
                ),
                scenarios=issue_scenarios,
            ))
        tree["node_count"] = len(tree_nodes)
        tree["edge_count"] = max(0, len(tree_nodes) - 1)
        trees.append(tree)
        nodes.extend(tree_nodes)
    return trees, nodes, mappings


AFTER_SALES_EVIDENCE_GATE_TEMPLATE_ID = "after_sales_evidence_gate_template_v1"
AFTER_SALES_EVIDENCE_GATE_STAGES = (
    "Customer Complaint Gate",
    "Precondition Evidence Gate",
    "Reproduction Context Gate",
    "Technical Evidence Gate",
    "Recorded Outcome Gate",
)


def after_sales_evidence_gate_metadata() -> dict[str, Any]:
    """Return the evidence-presence gate contract used by V11 exports."""
    return {
        "template_id": AFTER_SALES_EVIDENCE_GATE_TEMPLATE_ID,
        "stage_sequence": list(AFTER_SALES_EVIDENCE_GATE_STAGES),
        "ticket_mapping_policy": (
            "Service Function derives from defect evidence, not from "
            "reference-tree assignment."
        ),
        "reference_use_policy": (
            "Reference trees contribute workflow-stage patterns only; their individual "
            "nodes and paths are not copied into data-derived trees."
        ),
        "decision_policy": (
            "Complaint Gate YES/NO shows whether a historical Scenario matches the data-derived "
            "Problem Type. Every later YES/NO shows whether the specified evidence is recorded "
            "in historic Phase 09 tickets; neither is a live vehicle diagnosis, repair "
            "instruction, root-cause diagnosis, or escalation instruction."
        ),
    }


def _split_evidence_gate_scenarios(
    scenarios: list[dict[str, Any]], predicate
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    yes_scenarios = [scenario for scenario in scenarios if predicate(scenario)]
    no_scenarios = [scenario for scenario in scenarios if not predicate(scenario)]
    return yes_scenarios, no_scenarios


def _has_precondition_evidence(scenario: dict[str, Any]) -> bool:
    evidence_key, _ = _workflow_precondition_summary([scenario])
    return evidence_key != "precondition_not_recorded"


def _has_reproduction_context(scenario: dict[str, Any]) -> bool:
    context_key, _ = _diagnostic_context(scenario, _REPRODUCTION_CONTEXT_FIELDS)
    return context_key != "no_context_evidence"


def _has_technical_evidence(scenario: dict[str, Any]) -> bool:
    return bool(
        _technical_values(scenario.get("top_ecus") or scenario.get("rep_ecu"))
        or _technical_values(scenario.get("top_sw_versions"))
        or _recorded_technical_text(scenario.get("rca_root_cause"))
    )


def _has_recorded_outcome(scenario: dict[str, Any]) -> bool:
    return bool(_recorded_evidence_text(scenario.get("recovery_action")))


def _evidence_gate_tree_node(
    *,
    tree_id: str,
    node_id: str,
    node_type: str,
    parent_node_id: str,
    data_key: str,
    question_or_action: str,
    evidence_required: str,
    scenarios: list[dict[str, Any]],
    yes_scenarios: list[dict[str, Any]] | None = None,
    no_scenarios: list[dict[str, Any]] | None = None,
    true_next_node: str = "",
    false_next_node: str = "",
    next_node: str = "",
) -> dict[str, Any]:
    """Build a gate node whose Yes/No support is derived from source evidence."""
    node = _data_tree_node(
        tree_id=tree_id,
        node_id=node_id,
        node_type=node_type,
        parent_node_id=parent_node_id,
        data_key=data_key,
        question_or_action=question_or_action,
        evidence_required=evidence_required,
        scenarios=scenarios,
    )
    node["true_next_node"] = true_next_node
    node["false_next_node"] = false_next_node
    node["next_node"] = next_node
    node["yes_scenario_count"] = len(yes_scenarios or [])
    node["yes_ticket_count"] = _ticket_total(yes_scenarios or [])
    node["no_scenario_count"] = len(no_scenarios or [])
    node["no_ticket_count"] = _ticket_total(no_scenarios or [])
    node["node_source"] = "field_scenario_evidence_gate_template"
    node["workflow_template"] = AFTER_SALES_EVIDENCE_GATE_TEMPLATE_ID
    node["decision_status"] = "evidence_gate_only"
    return node


def build_evidence_gate_trees(
    scenarios: list[dict[str, Any]], families: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Build Yes/No evidence gates from defects using only an after-sales workflow pattern."""
    _, _, mappings = build_service_decision_trees(scenarios, families)
    scenarios_by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for scenario in scenarios:
        scenarios_by_service[scenario["service_function_id"]].append(scenario)

    trees = []
    nodes = []
    for service_function in SERVICE_FUNCTION_CATALOG:
        service_function_id = service_function["service_function_id"]
        tree_id = f"DT-{service_function_id}"
        tree_scenarios = scenarios_by_service[service_function_id]
        problem_type_ids = sorted(
            {scenario["problem_type_id"] for scenario in tree_scenarios},
            key=lambda problem_type_id: PROBLEM_TYPE_ORDER[problem_type_id],
        )
        issue_groups = _guided_issue_groups(tree_scenarios)
        tree = {
            "tree_id": tree_id,
            "tree_name": f"{service_function['service_function']} Evidence-Gate Triage Tree",
            "service_function_id": service_function_id,
            "service_function": service_function["service_function"],
            "service_function_label": service_function["display_name"],
            "service_domain": service_function["service_domain"],
            "catalog_status": service_function["catalog_status"],
            "reference_tree_ids": service_function["reference_tree_ids"],
            "scenario_count": len(tree_scenarios),
            "ticket_count": _ticket_total(tree_scenarios),
            "problem_type_ids": problem_type_ids,
            "problem_types": [PROBLEM_TYPE_LABELS[item] for item in problem_type_ids],
            "tree_generation": "field_scenario_evidence_gate_v1",
            "gate_template": AFTER_SALES_EVIDENCE_GATE_TEMPLATE_ID,
            "gate_stages": AFTER_SALES_EVIDENCE_GATE_STAGES,
            "guided_branch_count": len(issue_groups),
            "source_status": (
                "Complaint Gate YES/NO is data-derived Problem Type matching; later Yes/No "
                "gates are generated from Phase 09 evidence presence; reference-tree nodes "
                "and paths are not copied"
            ),
        }
        tree_nodes = [_evidence_gate_tree_node(
            tree_id=tree_id,
            node_id="START",
            node_type="Entry",
            parent_node_id="",
            data_key=service_function_id,
            question_or_action=service_function["service_function"],
            evidence_required="",
            scenarios=tree_scenarios,
            true_next_node=(
                "COM-01" if issue_groups else "COM-OTHER" if tree_scenarios else ""
            ),
        )]

        remaining_scenarios = list(tree_scenarios)
        for issue_index, (
            problem_type_id,
            issue_label,
            issue_scenarios,
        ) in enumerate(issue_groups, start=1):
            complaint_node_id = f"COM-{issue_index:02d}"
            remaining_scenarios = [
                scenario
                for scenario in remaining_scenarios
                if scenario not in issue_scenarios
            ]
            next_complaint_node_id = (
                f"COM-{issue_index + 1:02d}"
                if issue_index < len(issue_groups)
                else "COM-OTHER" if remaining_scenarios else ""
            )
            precondition_node_id = f"PRE-{issue_index:02d}"
            tree_nodes.append(_evidence_gate_tree_node(
                tree_id=tree_id,
                node_id=complaint_node_id,
                node_type="Complaint Gate",
                parent_node_id=("START" if issue_index == 1 else f"COM-{issue_index - 1:02d}"),
                data_key=problem_type_id,
                question_or_action=f"Does the complaint match {issue_label}?",
                evidence_required="Customer Symptom",
                scenarios=issue_scenarios + remaining_scenarios,
                yes_scenarios=issue_scenarios,
                no_scenarios=remaining_scenarios,
                true_next_node=precondition_node_id,
                false_next_node=next_complaint_node_id,
            ))

            precondition_yes, precondition_no = _split_evidence_gate_scenarios(
                issue_scenarios, _has_precondition_evidence
            )
            reproduction_node_id = f"REP-{issue_index:02d}"
            collect_precondition_node_id = f"ACT-PRE-{issue_index:02d}"
            tree_nodes.append(_evidence_gate_tree_node(
                tree_id=tree_id,
                node_id=precondition_node_id,
                node_type="Precondition Gate",
                parent_node_id=complaint_node_id,
                data_key="precondition_evidence",
                question_or_action="Is prerequisite evidence recorded?",
                evidence_required="Entitlement; Activation; Account; Vehicle online",
                scenarios=issue_scenarios,
                yes_scenarios=precondition_yes,
                no_scenarios=precondition_no,
                true_next_node=reproduction_node_id,
                false_next_node=(
                    collect_precondition_node_id if precondition_no else ""
                ),
            ))
            if precondition_no:
                tree_nodes.append(_evidence_gate_tree_node(
                    tree_id=tree_id,
                    node_id=collect_precondition_node_id,
                    node_type="Evidence Collection",
                    parent_node_id=precondition_node_id,
                    data_key="collect_precondition_evidence",
                    question_or_action="Collect prerequisite evidence",
                    evidence_required="Entitlement; Activation; Account; Vehicle online",
                    scenarios=precondition_no,
                    next_node=reproduction_node_id,
                ))

            reproduction_yes, reproduction_no = _split_evidence_gate_scenarios(
                issue_scenarios, _has_reproduction_context
            )
            technical_node_id = f"TEC-{issue_index:02d}"
            collect_context_node_id = f"ACT-REP-{issue_index:02d}"
            tree_nodes.append(_evidence_gate_tree_node(
                tree_id=tree_id,
                node_id=reproduction_node_id,
                node_type="Reproduction Gate",
                parent_node_id=precondition_node_id,
                data_key="reproduction_context",
                question_or_action="Is reproduction context recorded?",
                evidence_required="Device; Connection; Lifecycle; Trigger Condition",
                scenarios=issue_scenarios,
                yes_scenarios=reproduction_yes,
                no_scenarios=reproduction_no,
                true_next_node=technical_node_id,
                false_next_node=collect_context_node_id if reproduction_no else "",
            ))
            if reproduction_no:
                tree_nodes.append(_evidence_gate_tree_node(
                    tree_id=tree_id,
                    node_id=collect_context_node_id,
                    node_type="Evidence Collection",
                    parent_node_id=reproduction_node_id,
                    data_key="collect_reproduction_context",
                    question_or_action="Capture reproduction context",
                    evidence_required="Device; Connection; Lifecycle; Trigger Condition",
                    scenarios=reproduction_no,
                    next_node=technical_node_id,
                ))

            technical_yes, technical_no = _split_evidence_gate_scenarios(
                issue_scenarios, _has_technical_evidence
            )
            outcome_node_id = f"OUT-{issue_index:02d}"
            collect_technical_node_id = f"ACT-TEC-{issue_index:02d}"
            tree_nodes.append(_evidence_gate_tree_node(
                tree_id=tree_id,
                node_id=technical_node_id,
                node_type="Technical Evidence Gate",
                parent_node_id=reproduction_node_id,
                data_key="technical_evidence",
                question_or_action="Is ECU, software, or RCA evidence recorded?",
                evidence_required="Assigned ECU; Software Version; RCA Root Cause",
                scenarios=issue_scenarios,
                yes_scenarios=technical_yes,
                no_scenarios=technical_no,
                true_next_node=outcome_node_id,
                false_next_node=collect_technical_node_id if technical_no else "",
            ))
            if technical_no:
                tree_nodes.append(_evidence_gate_tree_node(
                    tree_id=tree_id,
                    node_id=collect_technical_node_id,
                    node_type="Evidence Collection",
                    parent_node_id=technical_node_id,
                    data_key="collect_technical_evidence",
                    question_or_action="Collect ECU, software, or RCA evidence",
                    evidence_required="Assigned ECU; Software Version; RCA Root Cause",
                    scenarios=technical_no,
                    next_node=outcome_node_id,
                ))

            outcome_yes, outcome_no = _split_evidence_gate_scenarios(
                issue_scenarios, _has_recorded_outcome
            )
            recorded_outcome_node_id = f"OUTCOME-{issue_index:02d}"
            evidence_package_node_id = f"ESC-{issue_index:02d}"
            tree_nodes.append(_evidence_gate_tree_node(
                tree_id=tree_id,
                node_id=outcome_node_id,
                node_type="Recorded Outcome Gate",
                parent_node_id=technical_node_id,
                data_key="recorded_outcome",
                question_or_action="Is a recovery outcome recorded?",
                evidence_required="Ticket / Scenario Recovery Action",
                scenarios=issue_scenarios,
                yes_scenarios=outcome_yes,
                no_scenarios=outcome_no,
                true_next_node=recorded_outcome_node_id if outcome_yes else "",
                false_next_node=evidence_package_node_id if outcome_no else "",
            ))
            _, outcome_text, _, _ = _workflow_recorded_outcome_summary(outcome_yes)
            if outcome_yes:
                tree_nodes.append(_evidence_gate_tree_node(
                    tree_id=tree_id,
                    node_id=recorded_outcome_node_id,
                    node_type="Recorded Outcome",
                    parent_node_id=outcome_node_id,
                    data_key="recorded_recovery_outcome",
                    question_or_action=(
                        _workflow_short_outcome(outcome_text) if outcome_text else ""
                    ),
                    evidence_required="Ticket / Scenario Recovery Action",
                    scenarios=outcome_yes,
                ))
            if outcome_no:
                tree_nodes.append(_evidence_gate_tree_node(
                    tree_id=tree_id,
                    node_id=evidence_package_node_id,
                    node_type="Evidence Package",
                    parent_node_id=outcome_node_id,
                    data_key="missing_recorded_recovery",
                    question_or_action="Prepare evidence package",
                    evidence_required="Customer Symptom; Trigger Condition; ECU; Software Version; RCA",
                    scenarios=outcome_no,
                ))

        if remaining_scenarios:
            tree_nodes.append(_evidence_gate_tree_node(
                tree_id=tree_id,
                node_id="COM-OTHER",
                node_type="Evidence Collection",
                parent_node_id=(f"COM-{len(issue_groups):02d}" if issue_groups else "START"),
                data_key="unmatched_customer_complaint",
                question_or_action="Capture customer complaint details",
                evidence_required="Customer Symptom; Trigger Condition",
                scenarios=remaining_scenarios,
            ))
        tree["node_count"] = len(tree_nodes)
        tree["edge_count"] = sum(
            bool(node["true_next_node"])
            + bool(node["false_next_node"])
            + bool(node["next_node"])
            for node in tree_nodes
        )
        trees.append(tree)
        nodes.extend(tree_nodes)
    return trees, nodes, mappings


def render_decision_tree_catalog_html_v4_legacy(
    trees: list[dict[str, Any]], nodes: list[dict[str, Any]]
) -> str:
    """Render a self-contained, selectable SVG decision-tree catalogue."""
    nodes_by_tree: dict[str, list[dict[str, Any]]] = defaultdict(list)
    references_by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        nodes_by_tree[node["tree_id"]].append(node)
    for reference_tree in DECISION_TREE_SOURCE_CATALOG:
        references_by_service[reference_tree["service_function_id"]].append(
            reference_tree
        )
    tree_data = [
        {
            **tree,
            "nodes": nodes_by_tree[tree["tree_id"]],
            "reference_trees": references_by_service[tree["service_function_id"]],
        }
        for tree in trees
    ]
    serialized_trees = json.dumps(tree_data, ensure_ascii=False).replace("<", "\\u003c").replace(
        ">", "\\u003e"
    ).replace("&", "\\u0026")
    template = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IDCEVO Phase 09 After-sales Decision Tree Catalog</title>
<style>
:root { color: #18212b; background: #f2f4f5; font-family: Aptos, "Noto Sans SC", "Microsoft YaHei", sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; }
main { max-width: 1440px; margin: 0 auto; padding: 32px 28px 52px; }
header { border-bottom: 4px solid #167a72; padding-bottom: 20px; }
h1 { margin: 0; color: #123c4a; font-size: 28px; letter-spacing: 0; }
p { line-height: 1.5; }
.subtitle { margin: 8px 0 0; color: #52616b; font-size: 15px; }
.controls { margin: 24px 0 16px; }
label { color: #33414b; font-size: 13px; font-weight: 700; }
select { display: block; width: min(500px, 100%); min-height: 42px; margin-top: 7px; padding: 8px 12px; border: 1px solid #7d919d; border-radius: 5px; background: #fff; color: #18212b; font: inherit; }
.tree-meta { display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 1px; border: 1px solid #c7d0d6; background: #c7d0d6; }
.tree-meta div { min-height: 72px; padding: 12px 14px; background: #fff; }
.tree-meta span { display: block; color: #5a6871; font-size: 12px; }
.tree-meta strong { display: block; margin-top: 6px; color: #183c4a; font-size: 15px; line-height: 1.25; }
.status { margin: 16px 0 0; padding: 10px 12px; border-left: 4px solid #d68b25; background: #fff4de; color: #5c3e13; font-size: 13px; }
.reference-assets { display: flex; gap: 8px 12px; align-items: baseline; flex-wrap: wrap; margin-top: 14px; padding: 10px 12px; border: 1px solid #c7d0d6; background: #fff; color: #33414b; font-size: 13px; }
.reference-assets strong { color: #183c4a; }
.reference-assets a { color: #116c6d; text-decoration-thickness: 1px; text-underline-offset: 2px; }
.diagram-shell { margin-top: 20px; border: 1px solid #b9c6cc; background: #fff; }
svg { display: block; width: 100%; height: auto; }
.node-table { width: 100%; margin-top: 20px; border-collapse: collapse; background: #fff; font-size: 13px; }
.node-table th, .node-table td { padding: 9px 10px; border: 1px solid #c7d0d6; text-align: left; vertical-align: top; line-height: 1.4; }
.node-table th { background: #123c4a; color: #fff; }
@media (max-width: 760px) { main { padding: 22px 16px 36px; } h1 { font-size: 23px; } .tree-meta { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
</head>
<body>
<main>
  <header><h1>IDCEVO Phase 09 After-sales Decision Tree Catalog</h1><p class="subtitle">功能入口 -> 问题类型 -> 触发条件 -> 诊断证据 -> 恢复或升级</p></header>
  <section class="controls"><label for="tree-selector">Service Function<select id="tree-selector"></select></label></section>
  <section class="tree-meta"><div><span>Decision Tree</span><strong id="tree-title"></strong></div><div><span>Mapped Scenarios</span><strong id="scenario-count"></strong></div><div><span>Mapped Tickets</span><strong id="ticket-count"></strong></div><div><span>Problem Types</span><strong id="problem-types"></strong></div></section>
  <p class="status" id="source-status"></p>
    <section class="reference-assets" aria-label="After-sales reference trees"><strong>After-sales reference trees</strong><span id="reference-assets"></span></section>
    <section class="diagram-shell"><svg id="decision-tree" viewBox="0 0 840 880" role="img" aria-label="Decision tree with TRUE and FALSE branches"></svg></section>
  <table class="node-table"><thead><tr><th>Node</th><th>Type</th><th>Question / Action</th><th>TRUE</th><th>FALSE</th><th>Next</th><th>Evidence Required</th></tr></thead><tbody id="node-rows"></tbody></table>
</main>
<script>
const trees = __TREE_DATA__;
const NS = "http://www.w3.org/2000/svg";
const positions = {
    C01: { x: 420, y: 64, width: 260, height: 76, diamond: true }, R00: { x: 135, y: 142, width: 220, height: 68 },
    C02: { x: 420, y: 198, width: 290, height: 98, diamond: true }, R01: { x: 135, y: 274, width: 220, height: 74 },
    C03: { x: 420, y: 342, width: 280, height: 82, diamond: true }, C04: { x: 420, y: 474, width: 300, height: 88, diamond: true },
    A01: { x: 700, y: 452, width: 220, height: 78 }, C05: { x: 420, y: 610, width: 300, height: 88, diamond: true },
    R02: { x: 250, y: 772, width: 240, height: 78 }, E01: { x: 610, y: 772, width: 240, height: 78 }
};
const colors = { Condition: "#d7ead1", Route: "#e4e9ec", Action: "#fff0c6", Resolution: "#d5e7f5", Escalation: "#f7d4d4" };
function svgElement(name, attrs = {}) { const item = document.createElementNS(NS, name); Object.entries(attrs).forEach(([key, value]) => item.setAttribute(key, String(value))); return item; }
function edgePoint(source, target) { const dx = target.x - source.x; const dy = target.y - source.y; return Math.abs(dx) > Math.abs(dy) ? { x: source.x + (dx > 0 ? source.width / 2 : -source.width / 2), y: source.y } : { x: source.x, y: source.y + (dy > 0 ? source.height / 2 : -source.height / 2) }; }
function drawText(svg, value, x, y, maxChars) { const characters = Array.from(String(value)); const lines = []; while (characters.length && lines.length < 4) lines.push(characters.splice(0, maxChars).join("")); const text = svgElement("text", { x, y: y - (lines.length - 1) * 8, "text-anchor": "middle", "font-size": 13, fill: "#18212b" }); lines.forEach((line, index) => { const span = svgElement("tspan", { x, dy: index ? 16 : 0 }); span.textContent = line; text.appendChild(span); }); svg.appendChild(text); }
function drawEdge(svg, sourceId, targetId, label) { const source = positions[sourceId]; const target = positions[targetId]; if (!source || !target) return; const start = edgePoint(source, target); const end = edgePoint(target, source); svg.appendChild(svgElement("line", { x1: start.x, y1: start.y, x2: end.x, y2: end.y, stroke: "#536670", "stroke-width": 1.5, "marker-end": "url(#arrow)" })); if (label) { const branch = svgElement("text", { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 - 6, "text-anchor": "middle", "font-size": 11, "font-weight": 700, fill: "#51626c" }); branch.textContent = label; svg.appendChild(branch); } }
function drawNode(svg, node) { const position = positions[node.node_id]; if (!position) return; const fill = colors[node.node_type] || "#fff"; if (position.diamond) svg.appendChild(svgElement("polygon", { points: `${position.x},${position.y - position.height / 2} ${position.x + position.width / 2},${position.y} ${position.x},${position.y + position.height / 2} ${position.x - position.width / 2},${position.y}`, fill, stroke: "#55716d", "stroke-width": 1.4 })); else svg.appendChild(svgElement("rect", { x: position.x - position.width / 2, y: position.y - position.height / 2, width: position.width, height: position.height, rx: 5, fill, stroke: "#55716d", "stroke-width": 1.4 })); const nodeId = svgElement("text", { x: position.x - position.width / 2 + 12, y: position.y - position.height / 2 + 18, "font-size": 11, "font-weight": 700, fill: "#34545e" }); nodeId.textContent = node.node_id; svg.appendChild(nodeId); drawText(svg, node.question_or_action, position.x, position.y + 4, position.diamond ? 26 : 30); }
function renderDiagram(tree) { const svg = document.getElementById("decision-tree"); svg.replaceChildren(); const defs = svgElement("defs"); const marker = svgElement("marker", { id: "arrow", markerWidth: 8, markerHeight: 8, refX: 7, refY: 4, orient: "auto", markerUnits: "strokeWidth" }); marker.appendChild(svgElement("path", { d: "M 0 0 L 8 4 L 0 8 z", fill: "#536670" })); defs.appendChild(marker); svg.appendChild(defs); tree.nodes.forEach((node) => { drawEdge(svg, node.node_id, node.true_next_node, "TRUE"); drawEdge(svg, node.node_id, node.false_next_node, "FALSE"); drawEdge(svg, node.node_id, node.next_node, ""); }); tree.nodes.forEach((node) => drawNode(svg, node)); }
function renderRows(tree) { const body = document.getElementById("node-rows"); body.replaceChildren(); tree.nodes.forEach((node) => { const row = document.createElement("tr"); [node.node_id, node.node_type, node.question_or_action, node.true_next_node, node.false_next_node, node.next_node, node.evidence_required].forEach((value) => { const cell = document.createElement("td"); cell.textContent = value; row.appendChild(cell); }); body.appendChild(row); }); }
function renderReferenceAssets(tree) { const container = document.getElementById("reference-assets"); container.replaceChildren(); if (!tree.reference_trees.length) { container.textContent = "No supplied reference tree; data-derived candidate only."; return; } tree.reference_trees.forEach((reference, index) => { if (index) container.append(" | "); const link = document.createElement("a"); link.href = reference.source_asset; link.target = "_blank"; link.rel = "noopener"; link.textContent = `${reference.reference_tree_id} (${reference.tree_version})`; link.title = `${reference.source_file}: ${reference.triage_pattern}`; container.appendChild(link); }); }
function selectTree(treeId) { const tree = trees.find((item) => item.tree_id === treeId) || trees[0]; document.getElementById("tree-title").textContent = tree.service_function_label; document.getElementById("scenario-count").textContent = String(tree.scenario_count); document.getElementById("ticket-count").textContent = String(tree.ticket_count); document.getElementById("problem-types").textContent = tree.problem_types.length ? tree.problem_types.join("; ") : "No mapped scenario"; document.getElementById("source-status").textContent = tree.source_status; renderReferenceAssets(tree); renderDiagram(tree); renderRows(tree); }
const selector = document.getElementById("tree-selector"); trees.forEach((tree) => { const option = document.createElement("option"); option.value = tree.tree_id; option.textContent = `${tree.service_function_label} (${tree.scenario_count} scenarios / ${tree.ticket_count} tickets)`; selector.appendChild(option); }); const defaultTree = trees.find((tree) => tree.tree_id === "DT-CARPLAY") || trees[0]; selector.value = defaultTree.tree_id; selector.addEventListener("change", (event) => selectTree(event.target.value)); selectTree(defaultTree.tree_id);
</script>
</body>
</html>
"""
    return template.replace("__TREE_DATA__", serialized_trees)


def render_guided_diagnostic_tree_catalog_html(
    trees: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    *,
    reference_tree_catalog: list[dict[str, Any]] | None = None,
) -> str:
    """Render one complete, aggregated guided diagnostic tree per Service Function."""
    if reference_tree_catalog is None:
        reference_tree_catalog = DECISION_TREE_SOURCE_CATALOG
    nodes_by_tree: dict[str, list[dict[str, Any]]] = defaultdict(list)
    references_by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        nodes_by_tree[node["tree_id"]].append(node)
    for reference_tree in reference_tree_catalog:
        references_by_service[reference_tree["service_function_id"]].append(
            reference_tree
        )
    tree_data = [
        {
            **tree,
            "nodes": nodes_by_tree[tree["tree_id"]],
            "reference_trees": references_by_service[tree["service_function_id"]],
        }
        for tree in trees
    ]
    serialized_trees = json.dumps(tree_data, ensure_ascii=False).replace(
        "<", "\\u003c"
    ).replace(">", "\\u003e").replace("&", "\\u0026")
    template = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IDCEVO Phase 09 Guided Diagnostic Tree Catalog</title>
<style>
:root { color: #18212b; background: #f2f4f5; font-family: Aptos, "Noto Sans SC", "Microsoft YaHei", sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; }
main { max-width: 1560px; margin: 0 auto; padding: 32px 28px 52px; }
header { border-bottom: 4px solid #167a72; padding-bottom: 20px; }
h1 { margin: 0; color: #123c4a; font-size: 28px; letter-spacing: 0; }
p { line-height: 1.5; }
.subtitle { margin: 8px 0 0; color: #52616b; font-size: 15px; }
.controls { margin: 24px 0 16px; }
label { color: #33414b; font-size: 13px; font-weight: 700; }
select { display: block; width: min(520px, 100%); min-height: 42px; margin-top: 7px; padding: 8px 12px; border: 1px solid #7d919d; border-radius: 5px; background: #fff; color: #18212b; font: inherit; }
.tree-meta { display: grid; grid-template-columns: repeat(5, minmax(130px, 1fr)); gap: 1px; border: 1px solid #c7d0d6; background: #c7d0d6; }
.tree-meta div { min-height: 72px; padding: 12px 14px; background: #fff; }
.tree-meta span { display: block; color: #5a6871; font-size: 12px; }
.tree-meta strong { display: block; margin-top: 6px; color: #183c4a; font-size: 15px; line-height: 1.25; }
.status { margin: 16px 0 0; padding: 10px 12px; border-left: 4px solid #d68b25; background: #fff4de; color: #5c3e13; font-size: 13px; }
.reference-assets { display: flex; gap: 8px 12px; align-items: baseline; flex-wrap: wrap; margin-top: 14px; padding: 10px 12px; border: 1px solid #c7d0d6; background: #fff; color: #33414b; font-size: 13px; }
.reference-assets strong { color: #183c4a; }
.reference-assets a { color: #116c6d; text-decoration-thickness: 1px; text-underline-offset: 2px; }
.diagram-shell { margin-top: 20px; overflow: auto; border: 1px solid #b9c6cc; background: #fff; }
svg { display: block; min-width: 1480px; width: 100%; height: auto; }
.node-table { width: 100%; margin-top: 20px; border-collapse: collapse; background: #fff; font-size: 13px; }
.node-table th, .node-table td { padding: 9px 10px; border: 1px solid #c7d0d6; text-align: left; vertical-align: top; line-height: 1.4; }
.node-table th { background: #123c4a; color: #fff; }
.node-table td:nth-child(1), .node-table td:nth-child(2), .node-table td:nth-child(3) { white-space: nowrap; }
@media (max-width: 760px) { main { padding: 22px 16px 36px; } h1 { font-size: 23px; } .tree-meta { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
</head>
<body>
<main>
  <header><h1>IDCEVO Phase 09 Guided Diagnostic Tree Catalog</h1><p class="subtitle">Service Function -> Issue Group -> Observed Pattern -> Trigger -> Context -> Evidence -> Recorded Recovery</p></header>
  <section class="controls"><label for="tree-selector">Service Function<select id="tree-selector"></select></label></section>
  <section class="tree-meta"><div><span>Service Function</span><strong id="tree-title"></strong></div><div><span>Mapped Scenarios</span><strong id="scenario-count"></strong></div><div><span>Mapped Tickets</span><strong id="ticket-count"></strong></div><div><span>Issue Group Branches</span><strong id="branch-count"></strong></div><div><span>Guided Nodes</span><strong id="node-count"></strong></div></section>
  <p class="status" id="source-status"></p>
  <section class="reference-assets" aria-label="After-sales reference trees"><strong>After-sales reference trees</strong><span id="reference-assets"></span></section>
  <section class="diagram-shell"><svg id="decision-tree" role="img" aria-label="Guided Diagnostic Tree"></svg></section>
  <table class="node-table"><thead><tr><th>Node</th><th>Parent</th><th>Type</th><th>Observed Data Summary</th><th>Evidence Source</th><th>Scenario Count</th><th>Ticket Count</th><th>Source Scenario IDs</th></tr></thead><tbody id="node-rows"></tbody></table>
</main>
<script>
const trees = __TREE_DATA__;
const NS = "http://www.w3.org/2000/svg";
const nodeColors = { Entry: "#d7ead1", "Issue Group": "#b8d7a9", "Symptom Pattern": "#d5e7f5", "Trigger Pattern": "#fff0c6", "Diagnostic Context": "#f5e5bf", "Technical Evidence": "#e4d9f2", Resolution: "#e4e9ec" };
const nodeX = { Entry: 120, "Issue Group": 370, "Symptom Pattern": 650, "Trigger Pattern": 930, "Diagnostic Context": 1210, "Technical Evidence": 1490, Resolution: 1770 };
function svgElement(name, attrs = {}) { const element = document.createElementNS(NS, name); Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, String(value))); return element; }
function rootNode(tree) { return tree.nodes.find((node) => node.node_type === "Entry") || null; }
function childrenByParent(nodes) { const children = new Map(); nodes.forEach((node) => { const parent = node.parent_node_id || ""; if (!children.has(parent)) children.set(parent, []); children.get(parent).push(node); }); children.forEach((items) => items.sort((left, right) => right.source_ticket_count - left.source_ticket_count || left.node_id.localeCompare(right.node_id))); return children; }
function issueNodes(tree) { const root = rootNode(tree); if (!root) return []; return (childrenByParent(tree.nodes).get(root.node_id) || []).filter((node) => node.node_type === "Issue Group"); }
function wrapLines(value, limit) { const characters = Array.from(String(value || "")); const lines = []; while (characters.length && lines.length < 4) lines.push(characters.splice(0, limit).join("")); return lines; }
function drawText(svg, value, x, y, limit, attrs = {}) { const lines = wrapLines(value, limit); const text = svgElement("text", { x, y: y - Math.max(0, lines.length - 1) * 8, "text-anchor": "middle", "font-size": 12, fill: "#18212b", ...attrs }); lines.forEach((line, index) => { const span = svgElement("tspan", { x, dy: index ? 15 : 0 }); span.textContent = line; text.appendChild(span); }); svg.appendChild(text); }
function nodePosition(tree, children, issueIndexById, node, resolutionIndexByParent) { if (node.node_type === "Entry") return { x: nodeX.Entry, y: Math.max(120, issueIndexById.size * 100) }; let issueId = node.node_type === "Issue Group" ? node.node_id : node.parent_node_id; while (issueId && !issueIndexById.has(issueId)) { const parent = tree.nodes.find((item) => item.node_id === issueId); issueId = parent ? parent.parent_node_id : ""; } const issueIndex = issueIndexById.get(issueId) || 0; const laneY = 120 + issueIndex * 190; if (node.node_type !== "Resolution") return { x: nodeX[node.node_type] || 1770, y: laneY }; const siblings = (children.get(node.parent_node_id) || []).filter((item) => item.node_type === "Resolution"); const siblingIndex = resolutionIndexByParent.get(node.node_id) || 0; return { x: nodeX.Resolution, y: laneY + (siblingIndex - (siblings.length - 1) / 2) * 72 }; }
function layoutGuidedTree(tree) { const svg = document.getElementById("decision-tree"); svg.replaceChildren(); const root = rootNode(tree); if (!root) return; const children = childrenByParent(tree.nodes); const issues = issueNodes(tree); const issueIndexById = new Map(issues.map((node, index) => [node.node_id, index])); const resolutionIndexByParent = new Map(); children.forEach((items) => { items.filter((node) => node.node_type === "Resolution").forEach((node, index) => resolutionIndexByParent.set(node.node_id, index)); }); const height = Math.max(360, 170 + Math.max(1, issues.length) * 190); svg.setAttribute("viewBox", `0 0 1920 ${height}`); const defs = svgElement("defs"); const marker = svgElement("marker", { id: "guided-arrow", markerWidth: 8, markerHeight: 8, refX: 7, refY: 4, orient: "auto", markerUnits: "strokeWidth" }); marker.appendChild(svgElement("path", { d: "M 0 0 L 8 4 L 0 8 z", fill: "#536670" })); defs.appendChild(marker); svg.appendChild(defs); const positions = new Map(); tree.nodes.forEach((node) => positions.set(node.node_id, nodePosition(tree, children, issueIndexById, node, resolutionIndexByParent))); tree.nodes.forEach((node) => { if (!node.parent_node_id || !positions.has(node.parent_node_id)) return; const parent = positions.get(node.parent_node_id); const child = positions.get(node.node_id); svg.appendChild(svgElement("line", { x1: parent.x + 112, y1: parent.y, x2: child.x - 112, y2: child.y, stroke: "#536670", "stroke-width": 1.35, "marker-end": "url(#guided-arrow)" })); }); tree.nodes.forEach((node) => { const position = positions.get(node.node_id); const fill = nodeColors[node.node_type] || "#fff"; const title = svgElement("title"); title.textContent = `${node.node_type}: ${node.question_or_action || "No recorded recovery action"}\n${node.source_scenario_ids}`; if (node.node_type === "Entry") svg.appendChild(svgElement("ellipse", { cx: position.x, cy: position.y, rx: 100, ry: 46, fill, stroke: "#55716d", "stroke-width": 1.4 })); else if (node.node_type === "Issue Group") svg.appendChild(svgElement("polygon", { points: `${position.x},${position.y - 54} ${position.x + 120},${position.y} ${position.x},${position.y + 54} ${position.x - 120},${position.y}`, fill, stroke: "#55716d", "stroke-width": 1.4 })); else svg.appendChild(svgElement("rect", { x: position.x - 112, y: position.y - 48, width: 224, height: 96, rx: 5, fill, stroke: "#55716d", "stroke-width": 1.3 })); svg.lastElementChild.appendChild(title); const label = svgElement("text", { x: position.x - 100, y: position.y - 29, "font-size": 10, "font-weight": 700, fill: "#34545e" }); label.textContent = `${node.node_id} | ${node.node_type}`; svg.appendChild(label); drawText(svg, node.question_or_action, position.x, position.y - 2, node.node_type === "Issue Group" ? 20 : 28); const count = svgElement("text", { x: position.x, y: position.y + 33, "text-anchor": "middle", "font-size": 10, fill: "#52616b" }); count.textContent = `${node.source_scenario_count} scenarios / ${node.source_ticket_count} tickets`; svg.appendChild(count); }); }
function renderRows(tree) { const body = document.getElementById("node-rows"); body.replaceChildren(); tree.nodes.forEach((node) => { const row = document.createElement("tr"); [node.node_id, node.parent_node_id, node.node_type, node.question_or_action, node.evidence_required, node.source_scenario_count, node.source_ticket_count, node.source_scenario_ids].forEach((value) => { const cell = document.createElement("td"); cell.textContent = value || ""; row.appendChild(cell); }); body.appendChild(row); }); }
function renderReferenceAssets(tree) { const container = document.getElementById("reference-assets"); container.replaceChildren(); if (!tree.reference_trees.length) { container.textContent = "No supplied reference tree; data-derived guided tree only."; return; } tree.reference_trees.forEach((reference, index) => { if (index) container.append(" | "); const link = document.createElement("a"); link.href = reference.source_asset; link.target = "_blank"; link.rel = "noopener"; link.textContent = `${reference.reference_tree_id} (${reference.tree_version})`; link.title = `${reference.source_file}: ${reference.triage_pattern}`; container.appendChild(link); }); }
function selectTree(treeId) { const tree = trees.find((item) => item.tree_id === treeId) || trees[0]; document.getElementById("tree-title").textContent = tree.service_function_label; document.getElementById("scenario-count").textContent = String(tree.scenario_count); document.getElementById("ticket-count").textContent = String(tree.ticket_count); document.getElementById("branch-count").textContent = String(tree.guided_branch_count); document.getElementById("node-count").textContent = String(tree.node_count); document.getElementById("source-status").textContent = tree.source_status; renderReferenceAssets(tree); layoutGuidedTree(tree); renderRows(tree); }
const selector = document.getElementById("tree-selector"); trees.forEach((tree) => { const option = document.createElement("option"); option.value = tree.tree_id; option.textContent = `${tree.service_function_label} (${tree.scenario_count} scenarios / ${tree.ticket_count} tickets)`; selector.appendChild(option); }); const defaultTree = trees.find((tree) => tree.tree_id === "DT-CARPLAY") || trees[0]; selector.value = defaultTree.tree_id; selector.addEventListener("change", (event) => selectTree(event.target.value)); selectTree(defaultTree.tree_id);
</script>
</body>
</html>
"""
    html = template.replace("__TREE_DATA__", serialized_trees)
    return (
        html.replace(
            'function wrapLines(value, limit) { const characters = Array.from(String(value || "")); const lines = []; while (characters.length && lines.length < 4) lines.push(characters.splice(0, limit).join("")); return lines; }',
            'function wrapLines(value, limit) { const characters = Array.from(String(value || "")); const lines = []; while (characters.length && lines.length < 3) lines.push(characters.splice(0, limit).join("")); return lines; } function displayNodeText(node) { let text = String(node.question_or_action || ""); text = text.replace(/^(Observed symptom patterns|Observed trigger patterns|Observed diagnostic context):\\s*/u, ""); if (node.node_type === "Technical Evidence") text = text.replace(/; Top ECU:.*$/u, "").replace(/; Top software version:.*$/u, ""); return text; }',
        ).replace(
            'label.textContent = `${node.node_id} | ${node.node_type}`;',
            'label.textContent = node.node_type;',
        ).replace(
            'drawText(svg, node.question_or_action, position.x, position.y - 2, node.node_type === "Issue Group" ? 20 : 28);',
            'drawText(svg, displayNodeText(node), position.x, position.y - 2, node.node_type === "Issue Group" ? 20 : 28);',
        ).replace(
            'y: position.y - 29', 'y: position.y - 43'
        ).replace(
            'y: position.y + 33', 'y: position.y + 50'
        ).replace(
            'y: position.y - 48, width: 224, height: 96',
            'y: position.y - 64, width: 224, height: 128',
        ).replace(
            'Math.max(120, issueIndexById.size * 100)',
            'Math.max(140, issueIndexById.size * 115)',
        ).replace(
            'const laneY = 120 + issueIndex * 190',
            'const laneY = 140 + issueIndex * 230',
        ).replace(
            ' * 72 }; }', ' * 84 }; }'
        ).replace(
            'Math.max(360, 170 + Math.max(1, issues.length) * 190)',
            'Math.max(420, 220 + Math.max(1, issues.length) * 230)',
        ).replace(
            'parent.x + 112, y1: parent.y, x2: child.x - 112',
            'parent.x + 120, y1: parent.y, x2: child.x - 120',
        )
    )


def render_symptom_solution_tree_catalog_html(
    trees: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    *,
    reference_tree_catalog: list[dict[str, Any]] | None = None,
) -> str:
    """Render the compact problem path without technical-evidence stages."""
    return (
        render_guided_diagnostic_tree_catalog_html(
            trees,
            nodes,
            reference_tree_catalog=reference_tree_catalog,
        )
        .replace(
            "IDCEVO Phase 09 Guided Diagnostic Tree Catalog",
            "IDCEVO Phase 09 Symptom-to-Solution Tree Catalog",
        )
        .replace(
            "Service Function -> Issue Group -> Observed Pattern -> Trigger -> Context -> Evidence -> Recorded Recovery",
            "Service Function -> Issue Group -> Symptom Pattern -> Trigger Pattern -> Ticket-recorded Solution",
        )
        .replace("Guided Nodes", "Problem-path Nodes")
        .replace(
            '<div><span>Problem-path Nodes</span><strong id="node-count"></strong></div>',
            '<div><span>Problem-path Nodes</span><strong id="node-count"></strong></div><div><span>Catalog Status</span><strong id="catalog-status"></strong></div><div><span>Source Ticket Mentions</span><strong id="source-ticket-mentions"></strong></div><div><span>Phase 09 Evidence Status</span><strong id="phase09-evidence-status"></strong></div>',
        )
        .replace(
            "grid-template-columns: repeat(5, minmax(130px, 1fr));",
            "grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));",
        )
        .replace("Guided Diagnostic Tree", "Symptom-to-Solution Tree")
        .replace("Observed Data Summary", "Question / Solution")
        .replace("No recorded recovery action", "No recorded ticket solution")
        .replace(
            "No supplied reference tree; data-derived guided tree only.",
            "No supplied reference tree; data-derived symptom-to-solution tree only.",
        )
        .replace(
            'const nodeColors = { Entry: "#d7ead1", "Issue Group": "#b8d7a9", "Symptom Pattern": "#d5e7f5", "Trigger Pattern": "#fff0c6", "Diagnostic Context": "#f5e5bf", "Technical Evidence": "#e4d9f2", Resolution: "#e4e9ec" };',
            'const nodeColors = { Entry: "#d7ead1", "Issue Group": "#b8d7a9", "Symptom Pattern": "#d5e7f5", "Trigger Pattern": "#fff0c6", "Ticket Solution": "#e4e9ec", "Diagnostic Context": "#f5e5bf", "Technical Evidence": "#e4d9f2", Resolution: "#e4e9ec" };',
        )
        .replace(
            'const nodeX = { Entry: 120, "Issue Group": 370, "Symptom Pattern": 650, "Trigger Pattern": 930, "Diagnostic Context": 1210, "Technical Evidence": 1490, Resolution: 1770 };',
            'const nodeX = { Entry: 120, "Issue Group": 370, "Symptom Pattern": 650, "Trigger Pattern": 930, "Ticket Solution": 1210, "Diagnostic Context": 1210, "Technical Evidence": 1490, Resolution: 1770 };',
        )
        .replace(
            'if (node.node_type !== "Resolution") return { x: nodeX[node.node_type] || 1770, y: laneY }; const siblings = (children.get(node.parent_node_id) || []).filter((item) => item.node_type === "Resolution");',
            'if (!["Resolution", "Ticket Solution"].includes(node.node_type)) return { x: nodeX[node.node_type] || 1770, y: laneY }; const siblings = (children.get(node.parent_node_id) || []).filter((item) => ["Resolution", "Ticket Solution"].includes(item.node_type));',
        )
        .replace(
            "return { x: nodeX.Resolution, y: laneY +",
            "return { x: nodeX[node.node_type] || nodeX.Resolution, y: laneY +",
        )
        .replace(
            'items.filter((node) => node.node_type === "Resolution").forEach((node, index) => resolutionIndexByParent.set(node.node_id, index));',
            'items.filter((node) => ["Resolution", "Ticket Solution"].includes(node.node_type)).forEach((node, index) => resolutionIndexByParent.set(node.node_id, index));',
        )
        .replace(
            'document.getElementById("node-count").textContent = String(tree.node_count);',
            'document.getElementById("node-count").textContent = String(tree.node_count); document.getElementById("catalog-status").textContent = tree.catalog_status; document.getElementById("source-ticket-mentions").textContent = String(tree.source_ticket_mention_count); document.getElementById("phase09-evidence-status").textContent = tree.phase09_evidence_status;',
        )
    )


def render_workflow_template_tree_catalog_html(
    trees: list[dict[str, Any]], nodes: list[dict[str, Any]]
) -> str:
    """Render complete per-function trees using the shared evidence-workflow template."""
    nodes_by_tree: dict[str, list[dict[str, Any]]] = defaultdict(list)
    references_by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        nodes_by_tree[node["tree_id"]].append(node)
    for reference_tree in DECISION_TREE_SOURCE_CATALOG:
        references_by_service[reference_tree["service_function_id"]].append(
            reference_tree
        )
    tree_data = [
        {
            **tree,
            "nodes": nodes_by_tree[tree["tree_id"]],
            "reference_trees": references_by_service[tree["service_function_id"]],
        }
        for tree in trees
    ]
    serialized_trees = json.dumps(tree_data, ensure_ascii=False).replace(
        "<", "\\u003c"
    ).replace(">", "\\u003e").replace("&", "\\u0026")
    template = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IDCEVO Phase 09 After-Sales Evidence Workflow</title>
<style>
:root { color: #18212b; background: #f2f4f5; font-family: Aptos, "Noto Sans SC", "Microsoft YaHei", sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; }
main { max-width: 1560px; margin: 0 auto; padding: 32px 28px 52px; }
header { border-bottom: 4px solid #167a72; padding-bottom: 20px; }
h1 { margin: 0; color: #123c4a; font-size: 28px; letter-spacing: 0; }
p { line-height: 1.5; }
.subtitle { margin: 8px 0 0; color: #52616b; font-size: 15px; }
.workflow-stages { display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)); gap: 1px; margin-top: 18px; border: 1px solid #b9c6cc; background: #b9c6cc; }
.workflow-stages span { min-height: 48px; padding: 9px 10px; background: #fff; color: #183c4a; font-size: 12px; font-weight: 700; line-height: 1.25; }
.controls { margin: 24px 0 16px; }
label { color: #33414b; font-size: 13px; font-weight: 700; }
select { display: block; width: min(520px, 100%); min-height: 42px; margin-top: 7px; padding: 8px 12px; border: 1px solid #7d919d; border-radius: 5px; background: #fff; color: #18212b; font: inherit; }
.tree-meta { display: grid; grid-template-columns: repeat(5, minmax(130px, 1fr)); gap: 1px; border: 1px solid #c7d0d6; background: #c7d0d6; }
.tree-meta div { min-height: 72px; padding: 12px 14px; background: #fff; }
.tree-meta span { display: block; color: #5a6871; font-size: 12px; }
.tree-meta strong { display: block; margin-top: 6px; color: #183c4a; font-size: 15px; line-height: 1.25; }
.status { margin: 16px 0 0; padding: 10px 12px; border-left: 4px solid #d68b25; background: #fff4de; color: #5c3e13; font-size: 13px; }
.reference-assets { display: flex; gap: 8px 12px; align-items: baseline; flex-wrap: wrap; margin-top: 14px; padding: 10px 12px; border: 1px solid #c7d0d6; background: #fff; color: #33414b; font-size: 13px; }
.reference-assets strong { color: #183c4a; }
.reference-assets a { color: #116c6d; text-decoration-thickness: 1px; text-underline-offset: 2px; }
.diagram-shell { margin-top: 20px; overflow: auto; border: 1px solid #b9c6cc; background: #fff; }
svg { display: block; min-width: 1780px; width: 100%; height: auto; }
.node-table { width: 100%; margin-top: 20px; border-collapse: collapse; background: #fff; font-size: 13px; }
.node-table th, .node-table td { padding: 9px 10px; border: 1px solid #c7d0d6; text-align: left; vertical-align: top; line-height: 1.4; }
.node-table th { background: #123c4a; color: #fff; }
.node-table td:first-child, .node-table td:nth-child(5), .node-table td:nth-child(6) { white-space: nowrap; }
@media (max-width: 760px) { main { padding: 22px 16px 36px; } h1 { font-size: 23px; } .workflow-stages, .tree-meta { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
</head>
<body>
<main>
  <header>
    <h1>IDCEVO Phase 09 After-Sales Evidence Workflow</h1>
    <p class="subtitle">Common workflow pattern from after-sales trees; every branch value below is aggregated from Phase 09 defect evidence.</p>
    <section class="workflow-stages" aria-label="After-Sales Evidence Workflow stages"><span>Customer Complaint</span><span>Precondition Evidence</span><span>Reproduction Context</span><span>Technical Evidence</span><span>Recorded Outcome</span><span>Escalation Package</span></section>
  </header>
  <section class="controls"><label for="tree-selector">Service Function<select id="tree-selector"></select></label></section>
  <section class="tree-meta"><div><span>Service Function</span><strong id="tree-title"></strong></div><div><span>Mapped Scenarios</span><strong id="scenario-count"></strong></div><div><span>Mapped Tickets</span><strong id="ticket-count"></strong></div><div><span>Complaint Branches</span><strong id="branch-count"></strong></div><div><span>Workflow Nodes</span><strong id="node-count"></strong></div></section>
  <p class="status" id="source-status"></p>
  <section class="reference-assets" aria-label="After-sales reference trees"><strong>After-sales reference trees</strong><span id="reference-assets"></span></section>
  <section class="diagram-shell"><svg id="decision-tree" role="img" aria-label="After-Sales Evidence Workflow tree"></svg></section>
  <table class="node-table"><thead><tr><th>Workflow Stage</th><th>Observed Evidence Summary</th><th>Evidence Source</th><th>Source Scenarios</th><th>Source Tickets</th><th>Decision Status</th><th>Source Scenario IDs</th></tr></thead><tbody id="node-rows"></tbody></table>
</main>
<script>
const trees = __TREE_DATA__;
const NS = "http://www.w3.org/2000/svg";
const nodeColors = { Entry: "#d7ead1", "Customer Complaint": "#b8d7a9", "Precondition Evidence": "#fff0c6", "Reproduction Context": "#d5e7f5", "Technical Evidence": "#e4d9f2", "Recorded Outcome": "#e4e9ec", "Escalation Package": "#f7d4d4" };
const nodeX = { Entry: 125, "Customer Complaint": 390, "Precondition Evidence": 665, "Reproduction Context": 940, "Technical Evidence": 1215, "Recorded Outcome": 1490, "Escalation Package": 1765 };
function svgElement(name, attrs = {}) { const element = document.createElementNS(NS, name); Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, String(value))); return element; }
function rootNode(tree) { return tree.nodes.find((node) => node.node_type === "Entry") || null; }
function childrenByParent(nodes) { const children = new Map(); nodes.forEach((node) => { const parent = node.parent_node_id || ""; if (!children.has(parent)) children.set(parent, []); children.get(parent).push(node); }); children.forEach((items) => items.sort((left, right) => right.source_ticket_count - left.source_ticket_count || left.node_id.localeCompare(right.node_id))); return children; }
function complaintNodes(tree) { const root = rootNode(tree); if (!root) return []; return (childrenByParent(tree.nodes).get(root.node_id) || []).filter((node) => node.node_type === "Customer Complaint"); }
function wrapLines(value, limit) { const characters = Array.from(String(value || "")); const lines = []; while (characters.length && lines.length < 3) lines.push(characters.splice(0, limit).join("")); return lines; }
function displayWorkflowNodeText(node) { return [node.visual_prompt, node.visual_detail].filter(Boolean).join(": "); }
function drawText(svg, value, x, y, limit, attrs = {}) { const lines = wrapLines(value, limit); const text = svgElement("text", { x, y: y - Math.max(0, lines.length - 1) * 8, "text-anchor": "middle", "font-size": 12, fill: "#18212b", ...attrs }); lines.forEach((line, index) => { const span = svgElement("tspan", { x, dy: index ? 15 : 0 }); span.textContent = line; text.appendChild(span); }); svg.appendChild(text); }
function nodePosition(tree, complaintIndexById, node) { if (node.node_type === "Entry") return { x: nodeX.Entry, y: Math.max(145, complaintIndexById.size * 120) }; let complaintId = node.node_type === "Customer Complaint" ? node.node_id : node.parent_node_id; while (complaintId && !complaintIndexById.has(complaintId)) { const parent = tree.nodes.find((item) => item.node_id === complaintId); complaintId = parent ? parent.parent_node_id : ""; } const complaintIndex = complaintIndexById.get(complaintId) || 0; return { x: nodeX[node.node_type] || 1765, y: 145 + complaintIndex * 245 }; }
function layoutWorkflowTree(tree) { const svg = document.getElementById("decision-tree"); svg.replaceChildren(); const root = rootNode(tree); if (!root) return; const complaints = complaintNodes(tree); const complaintIndexById = new Map(complaints.map((node, index) => [node.node_id, index])); const height = Math.max(450, 255 + Math.max(1, complaints.length) * 245); svg.setAttribute("viewBox", `0 0 1910 ${height}`); const defs = svgElement("defs"); const marker = svgElement("marker", { id: "workflow-arrow", markerWidth: 8, markerHeight: 8, refX: 7, refY: 4, orient: "auto", markerUnits: "strokeWidth" }); marker.appendChild(svgElement("path", { d: "M 0 0 L 8 4 L 0 8 z", fill: "#536670" })); defs.appendChild(marker); svg.appendChild(defs); const positions = new Map(); tree.nodes.forEach((node) => positions.set(node.node_id, nodePosition(tree, complaintIndexById, node))); tree.nodes.forEach((node) => { if (!node.parent_node_id || !positions.has(node.parent_node_id)) return; const parent = positions.get(node.parent_node_id); const child = positions.get(node.node_id); svg.appendChild(svgElement("line", { x1: parent.x + 116, y1: parent.y, x2: child.x - 116, y2: child.y, stroke: "#536670", "stroke-width": 1.35, "marker-end": "url(#workflow-arrow)" })); }); tree.nodes.forEach((node) => { const position = positions.get(node.node_id); const fill = nodeColors[node.node_type] || "#fff"; const title = svgElement("title"); title.textContent = `${node.node_type}: ${node.question_or_action || "No recorded outcome"}\\n${node.source_scenario_ids}`; if (node.node_type === "Entry") svg.appendChild(svgElement("ellipse", { cx: position.x, cy: position.y, rx: 104, ry: 48, fill, stroke: "#55716d", "stroke-width": 1.4 })); else if (node.node_type === "Customer Complaint") svg.appendChild(svgElement("polygon", { points: `${position.x},${position.y - 58} ${position.x + 124},${position.y} ${position.x},${position.y + 58} ${position.x - 124},${position.y}`, fill, stroke: "#55716d", "stroke-width": 1.4 })); else svg.appendChild(svgElement("rect", { x: position.x - 116, y: position.y - 60, width: 232, height: 120, rx: 5, fill, stroke: "#55716d", "stroke-width": 1.3 })); svg.lastElementChild.appendChild(title); const stage = svgElement("text", { x: position.x, y: position.y - 40, "text-anchor": "middle", "font-size": 10, "font-weight": 700, fill: "#34545e" }); stage.textContent = node.node_type; svg.appendChild(stage); drawText(svg, displayNodeText(node), position.x, position.y - 3, node.node_type === "Customer Complaint" ? 20 : 28); const count = svgElement("text", { x: position.x, y: position.y + 44, "text-anchor": "middle", "font-size": 10, fill: "#52616b" }); count.textContent = `${node.source_scenario_count} scenarios / ${node.source_ticket_count} tickets`; svg.appendChild(count); }); }
function renderRows(tree) { const body = document.getElementById("node-rows"); body.replaceChildren(); tree.nodes.forEach((node) => { const row = document.createElement("tr"); [node.node_type, node.question_or_action, node.evidence_required, node.source_scenario_count, node.source_ticket_count, node.decision_status, node.source_scenario_ids].forEach((value) => { const cell = document.createElement("td"); cell.textContent = value || ""; row.appendChild(cell); }); body.appendChild(row); }); }
function renderReferenceAssets(tree) { const container = document.getElementById("reference-assets"); container.replaceChildren(); if (!tree.reference_trees.length) { container.textContent = "No supplied reference tree; workflow stages still use defect evidence only."; return; } tree.reference_trees.forEach((reference, index) => { if (index) container.append(" | "); const link = document.createElement("a"); link.href = reference.source_asset; link.target = "_blank"; link.rel = "noopener"; link.textContent = reference.source_file; link.title = reference.triage_pattern; container.appendChild(link); }); }
function selectTree(treeId) { const tree = trees.find((item) => item.tree_id === treeId) || trees[0]; document.getElementById("tree-title").textContent = tree.service_function_label; document.getElementById("scenario-count").textContent = String(tree.scenario_count); document.getElementById("ticket-count").textContent = String(tree.ticket_count); document.getElementById("branch-count").textContent = String(tree.guided_branch_count); document.getElementById("node-count").textContent = String(tree.node_count); document.getElementById("source-status").textContent = tree.source_status; renderReferenceAssets(tree); layoutWorkflowTree(tree); renderRows(tree); }
const selector = document.getElementById("tree-selector"); trees.forEach((tree) => { const option = document.createElement("option"); option.value = tree.tree_id; option.textContent = `${tree.service_function_label} (${tree.scenario_count} scenarios / ${tree.ticket_count} tickets)`; selector.appendChild(option); }); const defaultTree = trees.find((tree) => tree.tree_id === "DT-CARPLAY") || trees[0]; selector.value = defaultTree.tree_id; selector.addEventListener("change", (event) => selectTree(event.target.value)); selectTree(defaultTree.tree_id);
</script>
</body>
</html>
"""
    html = template.replace("__TREE_DATA__", serialized_trees)
    return (
        html.replace(
            'function displayWorkflowNodeText(node) { return [node.visual_prompt, node.visual_detail].filter(Boolean).join(": "); }',
            'function displayWorkflowNodeText(node) { return node.visual_detail || ""; }',
        ).replace(
            'stage.textContent = node.node_type;',
            'stage.textContent = node.visual_prompt || node.node_type;',
        ).replace(
            'drawText(svg, displayNodeText(node), position.x, position.y - 3, node.node_type === "Customer Complaint" ? 20 : 28);',
            'drawText(svg, displayWorkflowNodeText(node), position.x, position.y - 3, node.node_type === "Customer Complaint" ? 20 : 28);',
        ).replace(
            'count.textContent = `${node.source_scenario_count} scenarios / ${node.source_ticket_count} tickets`;',
            'count.textContent = node.visual_evidence || `${node.source_ticket_count} tickets`;',
        )
    )


def render_evidence_gate_tree_catalog_html(
    trees: list[dict[str, Any]], nodes: list[dict[str, Any]]
) -> str:
    """Render compact after-sales-style Yes/No evidence-gate flowcharts."""
    nodes_by_tree: dict[str, list[dict[str, Any]]] = defaultdict(list)
    references_by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        nodes_by_tree[node["tree_id"]].append(node)
    for reference_tree in DECISION_TREE_SOURCE_CATALOG:
        references_by_service[reference_tree["service_function_id"]].append(
            reference_tree
        )
    tree_data = [
        {
            **tree,
            "nodes": nodes_by_tree[tree["tree_id"]],
            "reference_trees": references_by_service[tree["service_function_id"]],
        }
        for tree in trees
    ]
    serialized_trees = json.dumps(tree_data, ensure_ascii=False).replace(
        "<", "\\u003c"
    ).replace(">", "\\u003e").replace("&", "\\u0026")
    template = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IDCEVO Phase 09 Evidence-Gate Triage Tree</title>
<style>
:root { color: #18212b; background: #f2f4f5; font-family: Aptos, "Noto Sans SC", "Microsoft YaHei", sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; }
main { max-width: 1560px; margin: 0 auto; padding: 32px 28px 52px; }
header { border-bottom: 4px solid #167a72; padding-bottom: 20px; }
h1 { margin: 0; color: #123c4a; font-size: 28px; letter-spacing: 0; }
p { line-height: 1.5; }
.subtitle { margin: 8px 0 0; color: #52616b; font-size: 15px; }
.controls { margin: 24px 0 16px; }
label { color: #33414b; font-size: 13px; font-weight: 700; }
select { display: block; width: min(520px, 100%); min-height: 42px; margin-top: 7px; padding: 8px 12px; border: 1px solid #7d919d; border-radius: 5px; background: #fff; color: #18212b; font: inherit; }
.tree-meta { display: grid; grid-template-columns: repeat(5, minmax(130px, 1fr)); gap: 1px; border: 1px solid #c7d0d6; background: #c7d0d6; }
.tree-meta div { min-height: 72px; padding: 12px 14px; background: #fff; }
.tree-meta span { display: block; color: #5a6871; font-size: 12px; }
.tree-meta strong { display: block; margin-top: 6px; color: #183c4a; font-size: 15px; line-height: 1.25; }
.status { margin: 16px 0 0; padding: 10px 12px; border-left: 4px solid #d68b25; background: #fff4de; color: #5c3e13; font-size: 13px; }
.reference-assets { display: flex; gap: 8px 12px; align-items: baseline; flex-wrap: wrap; margin-top: 14px; padding: 10px 12px; border: 1px solid #c7d0d6; background: #fff; color: #33414b; font-size: 13px; }
.reference-assets strong { color: #183c4a; }
.reference-assets a { color: #116c6d; text-decoration-thickness: 1px; text-underline-offset: 2px; }
.diagram-shell { margin-top: 20px; overflow: auto; border: 1px solid #b9c6cc; background: #fff; }
svg { display: block; min-width: 2240px; width: 100%; height: auto; }
.node-table { width: 100%; margin-top: 20px; border-collapse: collapse; background: #fff; font-size: 13px; }
.node-table th, .node-table td { padding: 9px 10px; border: 1px solid #c7d0d6; text-align: left; vertical-align: top; line-height: 1.4; }
.node-table th { background: #123c4a; color: #fff; }
.node-table td:first-child, .node-table td:nth-child(4), .node-table td:nth-child(5), .node-table td:nth-child(6) { white-space: nowrap; }
@media (max-width: 760px) { main { padding: 22px 16px 36px; } h1 { font-size: 23px; } .tree-meta { grid-template-columns: repeat(2, minmax(0, 1fr)); } .tree-meta div:last-child { grid-column: span 2; } }
</style>
</head>
<body>
<main>
  <header><h1>IDCEVO Phase 09 Evidence-Gate Triage Tree</h1><p class="subtitle">Customer complaint -> evidence question -> YES / NO -> next question or evidence collection</p></header>
  <section class="controls"><label for="tree-selector">Service Function<select id="tree-selector"></select></label></section>
  <section class="tree-meta"><div><span>Service Function</span><strong id="tree-title"></strong></div><div><span>Mapped Scenarios</span><strong id="scenario-count"></strong></div><div><span>Mapped Tickets</span><strong id="ticket-count"></strong></div><div><span>Complaint Gates</span><strong id="branch-count"></strong></div><div><span>Evidence-Gate Nodes</span><strong id="node-count"></strong></div></section>
  <p class="status" id="source-status"></p>
  <section class="reference-assets" aria-label="After-sales reference trees"><strong>After-sales reference trees</strong><span id="reference-assets"></span></section>
  <section class="diagram-shell"><svg id="decision-tree" role="img" aria-label="Evidence-Gate Triage Tree"></svg></section>
  <table class="node-table"><thead><tr><th>Node Type</th><th>Question / Action</th><th>Evidence Source</th><th>YES Tickets</th><th>NO Tickets</th><th>Source Tickets</th><th>Decision Status</th><th>Source Scenario IDs</th></tr></thead><tbody id="node-rows"></tbody></table>
</main>
<script>
const trees = __TREE_DATA__;
const NS = "http://www.w3.org/2000/svg";
const questionTypes = new Set(["Complaint Gate", "Precondition Gate", "Reproduction Gate", "Technical Evidence Gate", "Recorded Outcome Gate"]);
const nodeColors = { Entry: "#d7ead1", "Complaint Gate": "#b8d7a9", "Precondition Gate": "#fff0c6", "Reproduction Gate": "#d5e7f5", "Technical Evidence Gate": "#e4d9f2", "Recorded Outcome Gate": "#d7ead1", "Evidence Collection": "#f7d4d4", "Recorded Outcome": "#e4e9ec", "Evidence Package": "#f7d4d4" };
const nodeX = { Entry: 100, "Complaint Gate": 400, "Precondition Gate": 750, "Reproduction Gate": 1100, "Technical Evidence Gate": 1450, "Recorded Outcome Gate": 1800, "Recorded Outcome": 2150, "Evidence Package": 2150 };
function svgElement(name, attrs = {}) { const element = document.createElementNS(NS, name); Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, String(value))); return element; }
function rootNode(tree) { return tree.nodes.find((node) => node.node_type === "Entry") || null; }
function gateIndex(node) { const match = node.node_id.match(/-([0-9]{2})$/); return match ? Number(match[1]) - 1 : 0; }
function complaintNodes(tree) { return tree.nodes.filter((node) => node.node_type === "Complaint Gate").sort((left, right) => gateIndex(left) - gateIndex(right)); }
function compactQuestion(node) { if (node.node_type === "Complaint Gate") return String(node.question_or_action || "").replace(/^Does the complaint match +/u, "").replace(/[?]$/u, "?"); if (node.node_type === "Precondition Gate") return "Prerequisites recorded?"; if (node.node_type === "Reproduction Gate") return "Reproduction context recorded?"; if (node.node_type === "Technical Evidence Gate") return "ECU / SW / RCA recorded?"; if (node.node_type === "Recorded Outcome Gate") return "Recovery outcome recorded?"; if (node.node_type === "Evidence Collection") return String(node.question_or_action || "").replace(/^Collect /u, "Collect ").replace(/^Capture /u, "Capture "); if (node.node_type === "Recorded Outcome") return node.question_or_action || "No recorded recovery"; if (node.node_type === "Evidence Package") return "Prepare evidence package"; return node.question_or_action || ""; }
function wrapLines(value, limit) { const lines = []; String(value || "").split(String.fromCharCode(10)).forEach((sourceLine) => { if (lines.length >= 3) return; const words = sourceLine.trim().split(" ").filter(Boolean); if (words.length > 1) { let line = ""; words.forEach((word) => { if (lines.length >= 3) return; const candidate = line ? `${line} ${word}` : word; if (line && Array.from(candidate).length > limit) { lines.push(line); line = word; } else { line = candidate; } }); if (line && lines.length < 3) lines.push(line); return; } const characters = Array.from(sourceLine); while (characters.length && lines.length < 3) lines.push(characters.splice(0, limit).join("")); }); return lines.length ? lines : [""]; }
function drawText(svg, value, x, y, limit, attrs = {}) { const lines = wrapLines(value, limit); const text = svgElement("text", { x, y: y - Math.max(0, lines.length - 1) * 8, "text-anchor": "middle", "font-size": 12, fill: "#18212b", ...attrs }); lines.forEach((line, index) => { const span = svgElement("tspan", { x, dy: index ? 15 : 0 }); span.textContent = line; text.appendChild(span); }); svg.appendChild(text); }
function positionForNode(tree, node, complaintCount) { if (node.node_type === "Entry") return { x: nodeX.Entry, y: 150 }; if (node.node_id === "COM-OTHER") return { x: nodeX["Complaint Gate"], y: 150 + complaintCount * 290 }; const index = gateIndex(node); const baseY = 150 + index * 290; if (node.node_id.startsWith("ACT-PRE-")) return { x: nodeX["Precondition Gate"], y: baseY + 160 }; if (node.node_id.startsWith("ACT-REP-")) return { x: nodeX["Reproduction Gate"], y: baseY + 160 }; if (node.node_id.startsWith("ACT-TEC-")) return { x: nodeX["Technical Evidence Gate"], y: baseY + 160 }; if (node.node_type === "Evidence Package") return { x: nodeX["Evidence Package"], y: baseY + 160 }; if (node.node_type === "Recorded Outcome") return { x: nodeX["Recorded Outcome"], y: baseY - 100 }; return { x: nodeX[node.node_type] || nodeX["Complaint Gate"], y: baseY }; }
function edgePoint(source, target) { const dx = target.x - source.x; const dy = target.y - source.y; if (Math.abs(dx) > Math.abs(dy)) return { x: source.x + (dx > 0 ? 130 : -130), y: source.y }; return { x: source.x, y: source.y + (dy > 0 ? 64 : -64) }; }
function drawEdge(svg, positions, source, targetId, label, kind) { if (!targetId || !positions.has(targetId)) return; const target = positions.get(targetId); const start = edgePoint(source, target); const end = edgePoint(target, source); const color = kind === "yes" ? "#26734d" : kind === "no" ? "#a25721" : "#536670"; svg.appendChild(svgElement("line", { x1: start.x, y1: start.y, x2: end.x, y2: end.y, stroke: color, "stroke-width": 1.45, "marker-end": `url(#gate-arrow-${kind})` })); if (label) { const text = svgElement("text", { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 - 7, "text-anchor": "middle", "font-size": 10, "font-weight": 700, fill: color }); text.textContent = label; svg.appendChild(text); } }
function layoutEvidenceGateTree(tree) { const svg = document.getElementById("decision-tree"); svg.replaceChildren(); const complaints = complaintNodes(tree); const height = Math.max(520, 240 + (complaints.length + 1) * 290); svg.setAttribute("viewBox", `0 0 2300 ${height}`); const defs = svgElement("defs"); [["yes", "#26734d"], ["no", "#a25721"], ["next", "#536670"]].forEach(([kind, color]) => { const marker = svgElement("marker", { id: `gate-arrow-${kind}`, markerWidth: 8, markerHeight: 8, refX: 7, refY: 4, orient: "auto", markerUnits: "strokeWidth" }); marker.appendChild(svgElement("path", { d: "M 0 0 L 8 4 L 0 8 z", fill: color })); defs.appendChild(marker); }); svg.appendChild(defs); const positions = new Map(); tree.nodes.forEach((node) => positions.set(node.node_id, positionForNode(tree, node, complaints.length))); tree.nodes.forEach((node) => { const source = positions.get(node.node_id); if (!source) return; const isQuestion = questionTypes.has(node.node_type); drawEdge(svg, positions, source, node.true_next_node, isQuestion ? `YES (${node.yes_ticket_count})` : "", "yes"); drawEdge(svg, positions, source, node.false_next_node, isQuestion ? `NO (${node.no_ticket_count})` : "", "no"); drawEdge(svg, positions, source, node.next_node, "", "next"); }); tree.nodes.forEach((node) => { const position = positions.get(node.node_id); if (!position) return; const group = svgElement("g", { "data-yes-ticket-count": node.yes_ticket_count, "data-no-ticket-count": node.no_ticket_count }); const fill = nodeColors[node.node_type] || "#fff"; const tooltip = svgElement("title"); tooltip.textContent = `${node.question_or_action || "No recorded outcome"}\nYES: ${node.yes_ticket_count} tickets | NO: ${node.no_ticket_count} tickets\n${node.source_scenario_ids}`; group.appendChild(tooltip); if (node.node_type === "Entry") group.appendChild(svgElement("ellipse", { cx: position.x, cy: position.y, rx: 100, ry: 46, fill, stroke: "#55716d", "stroke-width": 1.4 })); else if (questionTypes.has(node.node_type)) group.appendChild(svgElement("polygon", { points: `${position.x},${position.y - 58} ${position.x + 126},${position.y} ${position.x},${position.y + 58} ${position.x - 126},${position.y}`, fill, stroke: "#55716d", "stroke-width": 1.4 })); else group.appendChild(svgElement("rect", { x: position.x - 108, y: position.y - 42, width: 216, height: 84, rx: 5, fill, stroke: "#55716d", "stroke-width": 1.3 })); svg.appendChild(group); const stage = svgElement("text", { x: position.x, y: position.y - (questionTypes.has(node.node_type) ? 38 : 25), "text-anchor": "middle", "font-size": 10, "font-weight": 700, fill: "#34545e" }); stage.textContent = questionTypes.has(node.node_type) ? "QUESTION" : "ACTION"; svg.appendChild(stage); drawText(svg, compactQuestion(node), position.x, position.y - 4, questionTypes.has(node.node_type) ? 22 : 27); svg.appendChild(svgElement("rect", { x: position.x - 40, y: position.y + 22, width: 80, height: 18, rx: 5, fill: "#fffdf5", stroke: "#9aa9ad", "stroke-width": 0.8 })); const count = svgElement("text", { x: position.x, y: position.y + 35, "text-anchor": "middle", "font-size": 10, fill: "#52616b" }); count.textContent = `${node.source_ticket_count} tickets`; svg.appendChild(count); }); }
function renderRows(tree) { const body = document.getElementById("node-rows"); body.replaceChildren(); tree.nodes.forEach((node) => { const row = document.createElement("tr"); [node.node_type, node.question_or_action, node.evidence_required, node.yes_ticket_count, node.no_ticket_count, node.source_ticket_count, node.decision_status, node.source_scenario_ids].forEach((value) => { const cell = document.createElement("td"); cell.textContent = value || ""; row.appendChild(cell); }); body.appendChild(row); }); }
function renderReferenceAssets(tree) { const container = document.getElementById("reference-assets"); container.replaceChildren(); if (!tree.reference_trees.length) { container.textContent = "No supplied reference tree; Yes/No gates use defect evidence only."; return; } tree.reference_trees.forEach((reference, index) => { if (index) container.append(" | "); const link = document.createElement("a"); link.href = reference.source_asset; link.target = "_blank"; link.rel = "noopener"; link.textContent = reference.source_file; link.title = reference.triage_pattern; container.appendChild(link); }); }
function selectTree(treeId) { const tree = trees.find((item) => item.tree_id === treeId) || trees[0]; document.getElementById("tree-title").textContent = tree.service_function_label; document.getElementById("scenario-count").textContent = String(tree.scenario_count); document.getElementById("ticket-count").textContent = String(tree.ticket_count); document.getElementById("branch-count").textContent = String(tree.guided_branch_count); document.getElementById("node-count").textContent = String(tree.node_count); document.getElementById("source-status").textContent = tree.source_status; renderReferenceAssets(tree); layoutEvidenceGateTree(tree); renderRows(tree); }
const selector = document.getElementById("tree-selector"); trees.forEach((tree) => { const option = document.createElement("option"); option.value = tree.tree_id; option.textContent = `${tree.service_function_label} (${tree.scenario_count} scenarios / ${tree.ticket_count} tickets)`; selector.appendChild(option); }); const defaultTree = trees.find((tree) => tree.tree_id === "DT-CARPLAY") || trees[0]; selector.value = defaultTree.tree_id; selector.addEventListener("change", (event) => selectTree(event.target.value)); selectTree(defaultTree.tree_id);
</script>
</body>
</html>
"""
    return template.replace("__TREE_DATA__", serialized_trees)


def render_decision_tree_catalog_html(
    trees: list[dict[str, Any]], nodes: list[dict[str, Any]]
) -> str:
    """Render a compact, data-derived decision-tree browser without source images."""
    nodes_by_tree: dict[str, list[dict[str, Any]]] = defaultdict(list)
    references_by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        nodes_by_tree[node["tree_id"]].append(node)
    for reference_tree in DECISION_TREE_SOURCE_CATALOG:
        references_by_service[reference_tree["service_function_id"]].append(
            reference_tree
        )
    tree_data = [
        {
            **tree,
            "nodes": nodes_by_tree[tree["tree_id"]],
            "reference_trees": references_by_service[tree["service_function_id"]],
        }
        for tree in trees
    ]
    serialized_trees = json.dumps(tree_data, ensure_ascii=False).replace(
        "<", "\\u003c"
    ).replace(">", "\\u003e").replace("&", "\\u0026")
    template = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IDCEVO Phase 09 Evidence-Grounded Decision Tree Catalog</title>
<style>
:root { color: #18212b; background: #f2f4f5; font-family: Aptos, "Noto Sans SC", "Microsoft YaHei", sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; }
main { max-width: 1480px; margin: 0 auto; padding: 32px 28px 52px; }
header { border-bottom: 4px solid #167a72; padding-bottom: 20px; }
h1 { margin: 0; color: #123c4a; font-size: 28px; letter-spacing: 0; }
p { line-height: 1.5; }
.subtitle { margin: 8px 0 0; color: #52616b; font-size: 15px; }
.controls { display: flex; gap: 18px; flex-wrap: wrap; margin: 24px 0 16px; }
label { color: #33414b; font-size: 13px; font-weight: 700; }
select { display: block; width: min(480px, 100%); min-height: 42px; margin-top: 7px; padding: 8px 12px; border: 1px solid #7d919d; border-radius: 5px; background: #fff; color: #18212b; font: inherit; }
.tree-meta { display: grid; grid-template-columns: repeat(5, minmax(130px, 1fr)); gap: 1px; border: 1px solid #c7d0d6; background: #c7d0d6; }
.tree-meta div { min-height: 72px; padding: 12px 14px; background: #fff; }
.tree-meta span { display: block; color: #5a6871; font-size: 12px; }
.tree-meta strong { display: block; margin-top: 6px; color: #183c4a; font-size: 15px; line-height: 1.25; }
.status { margin: 16px 0 0; padding: 10px 12px; border-left: 4px solid #d68b25; background: #fff4de; color: #5c3e13; font-size: 13px; }
.reference-assets { display: flex; gap: 8px 12px; align-items: baseline; flex-wrap: wrap; margin-top: 14px; padding: 10px 12px; border: 1px solid #c7d0d6; background: #fff; color: #33414b; font-size: 13px; }
.reference-assets strong { color: #183c4a; }
.reference-assets a { color: #116c6d; text-decoration-thickness: 1px; text-underline-offset: 2px; }
.diagram-shell { margin-top: 20px; overflow: auto; border: 1px solid #b9c6cc; background: #fff; }
svg { display: block; min-width: 960px; width: 100%; height: auto; }
.node-table { width: 100%; margin-top: 20px; border-collapse: collapse; background: #fff; font-size: 13px; }
.node-table th, .node-table td { padding: 9px 10px; border: 1px solid #c7d0d6; text-align: left; vertical-align: top; line-height: 1.4; }
.node-table th { background: #123c4a; color: #fff; }
.node-table td:nth-child(1), .node-table td:nth-child(2), .node-table td:nth-child(3) { white-space: nowrap; }
@media (max-width: 760px) { main { padding: 22px 16px 36px; } h1 { font-size: 23px; } .tree-meta { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
</head>
<body>
<main>
    <header><h1>IDCEVO Phase 09 Evidence-Grounded Decision Tree Catalog</h1><p class="subtitle">Service Function -> Customer Symptom -> Trigger -> Diagnostic Context -> Technical Evidence -> Recorded Recovery</p></header>
  <section class="controls">
    <label for="tree-selector">Service Function<select id="tree-selector"></select></label>
    <label for="symptom-selector">Customer Symptom branch<select id="symptom-selector"></select></label>
        <label for="trigger-selector">Trigger Condition branch<select id="trigger-selector"></select></label>
  </section>
  <section class="tree-meta"><div><span>Service Function</span><strong id="tree-title"></strong></div><div><span>Mapped Scenarios</span><strong id="scenario-count"></strong></div><div><span>Mapped Tickets</span><strong id="ticket-count"></strong></div><div><span>Data Nodes</span><strong id="node-count"></strong></div><div><span>Problem Types</span><strong id="problem-types"></strong></div></section>
  <p class="status" id="source-status"></p>
  <section class="reference-assets" aria-label="After-sales reference trees"><strong>After-sales reference trees</strong><span id="reference-assets"></span></section>
  <section class="diagram-shell"><svg id="decision-tree" role="img" aria-label="Data-derived decision tree"></svg></section>
  <table class="node-table"><thead><tr><th>Node</th><th>Parent</th><th>Type</th><th>Data Key</th><th>Question / Action</th><th>Scenario Count</th><th>Ticket Count</th><th>Scenario IDs</th><th>Node Source</th></tr></thead><tbody id="node-rows"></tbody></table>
</main>
<script>
const trees = __TREE_DATA__;
const NS = "http://www.w3.org/2000/svg";
const nodeColors = { Entry: "#d7ead1", "Customer Symptom": "#d5e7f5", "Trigger Condition": "#fff0c6", "Diagnostic Context": "#f5e5bf", "Technical Evidence": "#e4d9f2", Resolution: "#e4e9ec" };
let activeTree = null;

function svgElement(name, attrs = {}) { const element = document.createElementNS(NS, name); Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, String(value))); return element; }
function childMap(nodes) { const result = new Map(); nodes.forEach((node) => { const parent = node.parent_node_id || ""; if (!result.has(parent)) result.set(parent, []); result.get(parent).push(node); }); result.forEach((items) => items.sort((left, right) => left.node_id.localeCompare(right.node_id))); return result; }
function rootNode(tree) { return tree.nodes.find((node) => node.node_type === "Entry") || null; }
function branchNodes(tree, symptomNodeId) { const root = rootNode(tree); if (!root) return []; const children = childMap(tree.nodes); if (!symptomNodeId) return [root, ...(children.get(root.node_id) || [])]; const triggers = children.get(symptomNodeId) || []; const triggerNodeId = document.getElementById("trigger-selector").value; if (!triggerNodeId) return [root, tree.nodes.find((node) => node.node_id === symptomNodeId), ...triggers].filter(Boolean); const wanted = new Set([root.node_id, symptomNodeId, triggerNodeId]); const queue = [triggerNodeId]; for (let index = 0; index < queue.length; index += 1) { const current = queue[index]; (children.get(current) || []).forEach((child) => { if (!wanted.has(child.node_id)) { wanted.add(child.node_id); queue.push(child.node_id); } }); } return tree.nodes.filter((node) => wanted.has(node.node_id)); }
function wrapLines(value, limit) { const characters = Array.from(String(value || "")); const lines = []; while (characters.length && lines.length < 3) lines.push(characters.splice(0, limit).join("")); return lines; }
function drawText(svg, value, x, y, limit, attrs = {}) { const lines = wrapLines(value, limit); const text = svgElement("text", { x, y: y - Math.max(0, lines.length - 1) * 8, "text-anchor": "middle", "font-size": 12, fill: "#18212b", ...attrs }); lines.forEach((line, index) => { const span = svgElement("tspan", { x, dy: index ? 15 : 0 }); span.textContent = line; text.appendChild(span); }); svg.appendChild(text); }
function layoutDataTree(tree, symptomNodeId) { const svg = document.getElementById("decision-tree"); svg.replaceChildren(); const visibleNodes = branchNodes(tree, symptomNodeId); const root = rootNode(tree); if (!root) return; const byId = new Map(visibleNodes.map((node) => [node.node_id, node])); const children = childMap(visibleNodes); const depth = new Map([[root.node_id, 0]]); const queue = [root.node_id]; for (let index = 0; index < queue.length; index += 1) { const parentId = queue[index]; const parentDepth = depth.get(parentId); (children.get(parentId) || []).forEach((child) => { if (!depth.has(child.node_id)) { depth.set(child.node_id, parentDepth + 1); queue.push(child.node_id); } }); } const levels = new Map(); visibleNodes.forEach((node) => { const level = depth.get(node.node_id) || 0; if (!levels.has(level)) levels.set(level, []); levels.get(level).push(node); }); const maxDepth = Math.max(...levels.keys()); const maxRows = Math.max(...Array.from(levels.values(), (items) => items.length)); const width = Math.max(960, 150 + (maxDepth + 1) * 300); const height = Math.max(340, 100 + maxRows * 118); svg.setAttribute("viewBox", `0 0 ${width} ${height}`); const defs = svgElement("defs"); const marker = svgElement("marker", { id: "data-arrow", markerWidth: 8, markerHeight: 8, refX: 7, refY: 4, orient: "auto", markerUnits: "strokeWidth" }); marker.appendChild(svgElement("path", { d: "M 0 0 L 8 4 L 0 8 z", fill: "#536670" })); defs.appendChild(marker); svg.appendChild(defs); const positions = new Map(); Array.from(levels.entries()).forEach(([level, levelNodes]) => { levelNodes.sort((left, right) => left.node_id.localeCompare(right.node_id)); const gap = height / (levelNodes.length + 1); levelNodes.forEach((node, index) => positions.set(node.node_id, { x: 140 + level * 300, y: gap * (index + 1) })); }); visibleNodes.forEach((node) => { if (!node.parent_node_id || !byId.has(node.parent_node_id)) return; const parent = positions.get(node.parent_node_id); const child = positions.get(node.node_id); svg.appendChild(svgElement("line", { x1: parent.x + 112, y1: parent.y, x2: child.x - 112, y2: child.y, stroke: "#536670", "stroke-width": 1.4, "marker-end": "url(#data-arrow)" })); }); visibleNodes.forEach((node) => { const position = positions.get(node.node_id); const fill = nodeColors[node.node_type] || "#fff"; svg.appendChild(svgElement("rect", { x: position.x - 112, y: position.y - 42, width: 224, height: 84, rx: 5, fill, stroke: "#55716d", "stroke-width": 1.3 })); const label = svgElement("text", { x: position.x - 100, y: position.y - 25, "font-size": 10, "font-weight": 700, fill: "#34545e" }); label.textContent = `${node.node_id} | ${node.node_type}`; svg.appendChild(label); drawText(svg, node.question_or_action, position.x, position.y - 2, 28); const count = svgElement("text", { x: position.x, y: position.y + 30, "text-anchor": "middle", "font-size": 10, fill: "#52616b" }); count.textContent = `${node.source_scenario_count} scenarios / ${node.source_ticket_count} tickets`; svg.appendChild(count); }); }
function renderRows(tree, symptomNodeId) { const body = document.getElementById("node-rows"); body.replaceChildren(); branchNodes(tree, symptomNodeId).forEach((node) => { const row = document.createElement("tr"); [node.node_id, node.parent_node_id, node.node_type, node.data_key, node.question_or_action, node.source_scenario_count, node.source_ticket_count, node.source_scenario_ids, node.node_source].forEach((value) => { const cell = document.createElement("td"); cell.textContent = value || ""; row.appendChild(cell); }); body.appendChild(row); }); }
function renderReferenceAssets(tree) { const container = document.getElementById("reference-assets"); container.replaceChildren(); if (!tree.reference_trees.length) { container.textContent = "No supplied reference tree; data-derived tree only."; return; } tree.reference_trees.forEach((reference, index) => { if (index) container.append(" | "); const link = document.createElement("a"); link.href = reference.source_asset; link.target = "_blank"; link.rel = "noopener"; link.textContent = `${reference.reference_tree_id} (${reference.tree_version})`; link.title = `${reference.source_file}: ${reference.triage_pattern}`; container.appendChild(link); }); }
function populateSymptomSelector(tree) { const selector = document.getElementById("symptom-selector"); selector.replaceChildren(); const allOption = document.createElement("option"); allOption.value = ""; allOption.textContent = "All customer symptom branches"; selector.appendChild(allOption); const root = rootNode(tree); if (!root) return; const symptomNodes = tree.nodes.filter((node) => node.parent_node_id === root.node_id && node.node_type === "Customer Symptom").sort((left, right) => right.source_ticket_count - left.source_ticket_count || left.node_id.localeCompare(right.node_id)); symptomNodes.forEach((node) => { const option = document.createElement("option"); option.value = node.node_id; option.textContent = `${node.question_or_action || node.data_key} (${node.source_scenario_count} scenarios / ${node.source_ticket_count} tickets)`; selector.appendChild(option); }); const defaultSymptomNode = symptomNodes[0]; if (defaultSymptomNode) selector.value = defaultSymptomNode.node_id; }
function populateTriggerSelector(tree, symptomNodeId) { const selector = document.getElementById("trigger-selector"); selector.replaceChildren(); const allOption = document.createElement("option"); allOption.value = ""; allOption.textContent = "All trigger condition branches"; selector.appendChild(allOption); const root = rootNode(tree); if (!root || !symptomNodeId) { selector.disabled = true; return; } const triggerNodes = tree.nodes.filter((node) => node.parent_node_id === symptomNodeId && node.node_type === "Trigger Condition").sort((left, right) => right.source_ticket_count - left.source_ticket_count || left.node_id.localeCompare(right.node_id)); triggerNodes.forEach((node) => { const option = document.createElement("option"); option.value = node.node_id; option.textContent = `${node.question_or_action || node.data_key} (${node.source_scenario_count} scenarios / ${node.source_ticket_count} tickets)`; selector.appendChild(option); }); const defaultTriggerNode = triggerNodes[0]; selector.disabled = !defaultTriggerNode; if (defaultTriggerNode) selector.value = defaultTriggerNode.node_id; }
function renderActiveBranch() { const symptomNodeId = document.getElementById("symptom-selector").value; layoutDataTree(activeTree, symptomNodeId); renderRows(activeTree, symptomNodeId); }
function selectSymptomBranch() { populateTriggerSelector(activeTree, document.getElementById("symptom-selector").value); renderActiveBranch(); }
function selectTree(treeId) { activeTree = trees.find((tree) => tree.tree_id === treeId) || trees[0]; document.getElementById("tree-title").textContent = activeTree.service_function_label; document.getElementById("scenario-count").textContent = String(activeTree.scenario_count); document.getElementById("ticket-count").textContent = String(activeTree.ticket_count); document.getElementById("node-count").textContent = String(activeTree.node_count); document.getElementById("problem-types").textContent = activeTree.problem_types.length ? activeTree.problem_types.join("; ") : "No mapped scenario"; document.getElementById("source-status").textContent = activeTree.source_status; renderReferenceAssets(activeTree); populateSymptomSelector(activeTree); populateTriggerSelector(activeTree, document.getElementById("symptom-selector").value); renderActiveBranch(); }
const treeSelector = document.getElementById("tree-selector"); trees.forEach((tree) => { const option = document.createElement("option"); option.value = tree.tree_id; option.textContent = `${tree.service_function_label} (${tree.scenario_count} scenarios / ${tree.ticket_count} tickets)`; treeSelector.appendChild(option); }); const defaultTree = trees.find((tree) => tree.tree_id === "DT-CARPLAY") || trees[0]; treeSelector.value = defaultTree.tree_id; treeSelector.addEventListener("change", (event) => selectTree(event.target.value)); document.getElementById("symptom-selector").addEventListener("change", selectSymptomBranch); document.getElementById("trigger-selector").addEventListener("change", renderActiveBranch); selectTree(defaultTree.tree_id);
</script>
</body>
</html>
"""
    return template.replace("__TREE_DATA__", serialized_trees)


# ═══════════════════════════════════════════════════════════════════════════════
# Risk Scoring
# ═══════════════════════════════════════════════════════════════════════════════

RISK_WEIGHTS = {
    "customer_severity": 0.30,
    "production_applicability": 0.25,
    "exposure_frequency": 0.20,
    "perceptibility": 0.10,
    "diagnosability": 0.10,
    "recoverability": 0.05,
}

# Severity keyword scoring (1-5 scale)
SEVERITY_KEYWORDS = {
    5: ["安全", "不可驾驶", "充电", "无法启动", "safety", "无法行驶", "高压"],
    4: ["核心功能中断", "全车无", "黑屏", "完全无", "崩溃", "无法使用"],
    3: ["主要体验", "无声音", "无响应", "卡顿", "断连", "误识别", "显示异常"],
    2: ["轻微", "偶发", "间歇", "轻微偏差", "偶现"],
    1: ["文案", "装饰", "ui细节", "颜色偏差"],
}


def score_severity(symptom: str, observed: str) -> int:
    """Score customer severity 1-5 based on symptom keywords."""
    combined = f"{symptom} {observed}".lower()
    for score in [5, 4, 3, 2, 1]:
        for keyword in SEVERITY_KEYWORDS[score]:
            if keyword.lower() in combined:
                return score
    return 3  # Default to moderate


def score_production_applicability(
    prod_exposure_ratio: float, risk_scores: list[int]
) -> float:
    """Score 1-5: higher = more likely to reach production/customers."""
    if prod_exposure_ratio >= 0.5:
        return 5.0
    if prod_exposure_ratio >= 0.3:
        return 4.0
    if prod_exposure_ratio >= 0.15:
        return 3.0
    if prod_exposure_ratio >= 0.05:
        return 2.0
    return 1.0


def score_exposure_frequency(cluster_size: int, total_tickets: int) -> float:
    """Score 1-5 based on how many tickets share this scenario."""
    ratio = cluster_size / total_tickets
    if ratio >= 0.02:
        return 5.0
    if ratio >= 0.01:
        return 4.0
    if ratio >= 0.005:
        return 3.0
    if ratio >= 0.002:
        return 2.0
    return 1.0


def score_perceptibility(symptom: str, observed: str) -> int:
    """Score 1-5: how easily a customer notices the failure."""
    combined = f"{symptom} {observed}".lower()
    high = ["黑屏", "无声音", "无法启动", "全车无", "崩溃", "完全无"]
    medium = ["卡顿", "延迟", "偶发", "间歇", "偏差", "异常"]
    for kw in high:
        if kw in combined:
            return 5
    for kw in medium:
        if kw in combined:
            return 3
    return 2


def score_diagnosability(rca_recovery: str, rca_root_cause: str, rca_observed: str) -> float:
    """Score 1-5: 5=easy to diagnose, 1=no diagnostic info."""
    combined = f"{rca_recovery} {rca_root_cause} {rca_observed}".lower()
    if not combined.strip():
        return 1.0
    if any(kw in combined for kw in ["dtc", "log", "trace", "诊断", "0x"]):
        return 4.0
    if any(kw in combined for kw in ["reset", "复位", "重启", "workaround", "规避"]):
        return 3.0
    if rca_root_cause.strip() and rca_root_cause.strip() != "none":
        return 3.0
    return 2.0


def score_recoverability(recovery_action: str) -> float:
    """Score 1-5: 5=easy recovery (low risk), 1=no recovery (high risk)."""
    ra_lower = recovery_action.lower()
    if not ra_lower or ra_lower in ("none", "none recorded", "无", ""):
        return 1.0
    if any(kw in ra_lower for kw in ["reset", "复位", "重启", "restart", "reboot", "12v"]):
        return 4.0
    if any(kw in ra_lower for kw in ["workaround", "规避", "exit", "退出"]):
        return 3.0
    if ra_lower.strip():
        return 2.5
    return 1.0


def compute_risk_index(
    scenario: dict[str, Any], total_tickets: int
) -> dict[str, Any]:
    """Compute the weighted Field Escape Risk Index for a scenario."""
    symptom = scenario.get("customer_symptom", "")
    observed = scenario.get("observed_behaviour", "")
    recovery = scenario.get("recovery_action", "")
    root_cause = scenario.get("rca_root_cause", "")
    rca_observed = scenario.get("rca_observed", "")

    member_risk_scores = scenario.get("member_risk_scores", [])
    prod_exposure_count = scenario.get("prod_exposure_count", 0)
    cluster_size = scenario.get("ticket_count", 1)

    prod_ratio = prod_exposure_count / cluster_size if cluster_size > 0 else 0

    severity = score_severity(symptom, observed)
    prod_applicability = score_production_applicability(prod_ratio, member_risk_scores)
    exposure_freq = score_exposure_frequency(cluster_size, total_tickets)
    perceptibility = score_perceptibility(symptom, observed)
    diagnosability = score_diagnosability(recovery, root_cause, rca_observed)
    recoverability = score_recoverability(recovery)

    risk_index = (
        RISK_WEIGHTS["customer_severity"] * severity
        + RISK_WEIGHTS["production_applicability"] * prod_applicability
        + RISK_WEIGHTS["exposure_frequency"] * exposure_freq
        + RISK_WEIGHTS["perceptibility"] * perceptibility
        + RISK_WEIGHTS["diagnosability"] * diagnosability
        + RISK_WEIGHTS["recoverability"] * recoverability
    )

    if risk_index >= 4.0:
        band = "Critical"
    elif risk_index >= 3.0:
        band = "High"
    elif risk_index >= 2.0:
        band = "Medium"
    else:
        band = "Low"

    return {
        "risk_index": round(risk_index, 2),
        "risk_band": band,
        "severity": severity,
        "production_applicability": round(prod_applicability, 1),
        "exposure_frequency": round(exposure_freq, 1),
        "perceptibility": perceptibility,
        "diagnosability": round(diagnosability, 1),
        "recoverability": round(recoverability, 1),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Excel styling helpers
# ═══════════════════════════════════════════════════════════════════════════════

THIN = Side(style="thin", color="CCCCCC")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HDR_FILL = PatternFill("solid", fgColor="2F5496")
HDR_FONT = Font(bold=True, color="FFFFFF", size=11)
WRAP = Alignment(wrap_text=True, vertical="top")
CENTER_WRAP = Alignment(wrap_text=True, vertical="center", horizontal="center")

RISK_FILLS = {
    "Critical": PatternFill("solid", fgColor="FFC7CE"),
    "High": PatternFill("solid", fgColor="FFD966"),
    "Medium": PatternFill("solid", fgColor="FFF2CC"),
    "Low": PatternFill("solid", fgColor="D9D9D9"),
}


def style_header(ws, ncols, row=1):
    for c in range(1, ncols + 1):
        cl = ws.cell(row=row, column=c)
        cl.font = HDR_FONT
        cl.fill = HDR_FILL
        cl.alignment = WRAP
        cl.border = BORDER


def autosize(ws, widths):
    for i, wd in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = wd


def add_filter_and_freeze(ws, ncols, freeze_cell="C2"):
    last_col = get_column_letter(ncols)
    ws.auto_filter.ref = f"A1:{last_col}{ws.max_row}"
    ws.freeze_panes = freeze_cell


# ═══════════════════════════════════════════════════════════════════════════════
# Main pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def load_extractions() -> list[dict]:
    """Load LLM scenario extraction outputs."""
    print("Loading scenario extractions ...")
    records = []
    with open(EXTRACTION_JSONL, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    print(f"  Loaded {len(records)} extraction records")
    return records


def load_excel_metadata() -> dict[str, dict]:
    """Load ticket metadata from Excel sheet 05_全部明细."""
    print("Loading Excel metadata ...")
    wb = openpyxl.load_workbook(EXCEL_PATH, read_only=True, data_only=True)
    ws = wb["05_全部明细"]
    header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    col = {name: idx for idx, name in enumerate(header) if name}

    metadata: dict[str, dict] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        def g(name: str, default: str = "") -> str:
            idx = col.get(name)
            if idx is None or idx >= len(row):
                return default
            v = row[idx]
            s = str(v).strip() if v is not None else ""
            return s or default

        tid = g("Ticket ID")
        if not tid:
            continue
        metadata[tid] = {
            "ticket_id": tid,
            "function": g("功能"),
            "ecu": g("Assigned ECU"),
            "software_version": g("Software Version"),
            "project": g("原始项目（project）"),
            "tproject": g("最终项目（tproject）"),
            "risk_tier": g("风险层级"),
            "risk_score": g("风险分"),
            "closure_reason": g("关闭原因"),
            "closure_disposition": g("关闭处置"),
            "title": g("Ticket 标题"),
            "description_excerpt": g("问题描述摘录"),
            "llm_summary": g("LLM 处理摘要"),
            "rca_observed": g("RCA Observed Behaviour"),
            "rca_expected": g("RCA Expected Behaviour"),
            "rca_recovery": g("RCA Recovery Action"),
            "rca_root_cause": g("RCA Root Cause"),
            "function_responsible": g("Function Responsible"),
            "problem_finder": g("Problem Finder Team / fallback"),
        }
    wb.close()
    print(f"  Loaded metadata for {len(metadata)} tickets")
    return metadata


def build_scenarios(
    extractions: list[dict], metadata: dict[str, dict]
) -> tuple[list[dict], list[dict]]:
    """
    Normalize, cluster, and build scenario records.
    Returns (scenarios, excluded_tickets).
    """
    print("Building scenarios ...")
    total_tickets = len(extractions)

    # Step 2+3: Normalize and cluster
    clusters: dict[str, list[dict]] = defaultdict(list)
    excluded: list[dict] = []

    for rec in extractions:
        tid = str(rec.get("ticket_id", ""))
        ext = rec.get("extraction", {})
        meta = metadata.get(tid, {})

        ticket_record = {
            "ticket_id": tid,
            "extraction": ext,
            "metadata": meta,
            "model": rec.get("model", ""),
        }

        is_field = bool(ext.get("is_field_scenario", True))
        if not is_field:
            excluded.append({
                "ticket_id": tid,
                "exclusion_reason": ext.get("exclusion_reason", "unknown"),
                "function": meta.get("function", ""),
                "title": meta.get("title", ""),
                "risk_tier": meta.get("risk_tier", ""),
                "closure_reason": meta.get("closure_reason", ""),
            })
            continue

        symptom = ext.get("customer_symptom", "")
        trigger = ext.get("trigger_condition", "")
        affected_function = ext.get("affected_function", "")
        raw_function = meta.get("function", "")

        # Canonical function domain
        canonical_domain = canonical_function_domain(raw_function, affected_function)
        engineering_family = classify_engineering_family({
            "rep_function": raw_function,
            "function": raw_function,
            "function_responsible": meta.get("function_responsible", ""),
            "affected_function": affected_function,
            "rep_title": meta.get("title", ""),
            "customer_symptom": symptom,
            "trigger_condition": trigger,
        })

        key = scenario_key(symptom, trigger, canonical_domain)
        ticket_record["canonical_domain"] = canonical_domain
        ticket_record["engineering_family"] = engineering_family
        clusters[key].append(ticket_record)

    # Build scenario records
    scenarios: list[dict] = []
    for key, members in clusters.items():
        # Pick representative: highest risk_score among members
        members_sorted = sorted(
            members,
            key=lambda m: -int(m["metadata"].get("risk_score", "0") or "0"),
        )
        rep = members_sorted[0]
        rep_ext = rep["extraction"]
        rep_meta = rep["metadata"]

        # Aggregate stats
        risk_scores = [
            int(m["metadata"].get("risk_score", "0") or "0")
            for m in members
        ]
        prod_exposure_count = sum(
            1 for m in members
            if "production-exposure" in m["metadata"].get("risk_tier", "").lower()
        )

        # Collect vehicle lines / SW versions
        sw_versions = Counter(
            m["metadata"].get("software_version", "")
            for m in members if m["metadata"].get("software_version", "")
        )
        ecus = Counter(
            m["metadata"].get("ecu", "")
            for m in members if m["metadata"].get("ecu", "")
        )
        closure_reasons = Counter(
            m["metadata"].get("closure_reason", "")
            for m in members if m["metadata"].get("closure_reason", "")
        )
        risk_tiers = Counter(
            m["metadata"].get("risk_tier", "")
            for m in members if m["metadata"].get("risk_tier", "")
        )
        owners = Counter(
            m["metadata"].get("function_responsible", "")
            for m in members if m["metadata"].get("function_responsible", "")
        )
        engineering_families = Counter(
            m["engineering_family"]["engineering_family_id"] for m in members
        )
        engineering_family_id = engineering_families.most_common(1)[0][0]
        engineering_family = ENGINEERING_FAMILY_BY_ID[engineering_family_id]
        engineering_match_sources = Counter(
            m["engineering_family"].get("match_source", "") for m in members
        )
        found_in_function_members: dict[tuple[str, str], list[str]] = defaultdict(list)
        for member in members_sorted:
            found_in_function = (
                member["metadata"].get("function", "").strip() or "(not recorded)"
            )
            function_responsible = (
                member["metadata"].get("function_responsible", "").strip()
                or "(not recorded)"
            )
            found_in_function_members[(found_in_function, function_responsible)].append(
                member["ticket_id"]
            )

        scenario = {
            "scenario_key": key,
            "customer_symptom": rep_ext.get("customer_symptom", ""),
            "trigger_condition": rep_ext.get("trigger_condition", ""),
            "affected_function": rep.get("canonical_domain", ""),
            "affected_function_raw": rep_ext.get("affected_function", ""),
            "observed_behaviour": rep_ext.get("observed_behaviour", ""),
            "expected_behaviour": rep_ext.get("expected_behaviour", ""),
            "recovery_action": rep_ext.get("recovery_action", ""),
            "closure_decision": rep_ext.get("closure_decision", ""),
            "evidence_confidence": rep_ext.get("evidence_confidence", ""),
            "engineering_family_id": engineering_family_id,
            "engineering_family": engineering_family["engineering_family"],
            "engineering_family_match_sources": engineering_match_sources.most_common(3),
            "rca_root_cause": rep_meta.get("rca_root_cause", ""),
            "rca_observed": rep_meta.get("rca_observed", ""),
            "rca_recovery": rep_meta.get("rca_recovery", ""),
            "ticket_count": len(members),
            "ticket_ids": [m["ticket_id"] for m in members_sorted],
            "rep_ticket_id": rep["ticket_id"],
            "rep_title": rep_meta.get("title", ""),
            "rep_function": rep_meta.get("function", ""),
            "found_in_function_members": [
                {
                    "found_in_function": found_in_function,
                    "function_responsible": function_responsible,
                    "ticket_ids": ticket_ids,
                }
                for (found_in_function, function_responsible), ticket_ids in sorted(
                    found_in_function_members.items()
                )
            ],
            "rep_ecu": rep_meta.get("ecu", ""),
            "member_risk_scores": risk_scores,
            "max_risk_score": max(risk_scores) if risk_scores else 0,
            "avg_risk_score": round(sum(risk_scores) / len(risk_scores), 1) if risk_scores else 0,
            "prod_exposure_count": prod_exposure_count,
            "top_sw_versions": sw_versions.most_common(5),
            "top_ecus": ecus.most_common(5),
            "closure_reasons": closure_reasons.most_common(5),
            "risk_tiers": risk_tiers.most_common(5),
            "owners": owners.most_common(5),
        }
        scenarios.append(scenario)

    # Sort scenarios by max_risk_score descending
    scenarios.sort(key=lambda s: -s["max_risk_score"])

    # Assign Scenario IDs
    for i, s in enumerate(scenarios):
        s["scenario_id"] = f"FS-{i + 1:04d}"

    print(f"  Total scenarios: {len(scenarios)}")
    print(f"  Excluded tickets: {len(excluded)}")
    return scenarios, excluded


def build_families(scenarios: list[dict]) -> list[dict]:
    """Group scenarios by stable R&D responsibility family."""
    print("Building scenario families ...")
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for s in scenarios:
        by_domain[s["engineering_family_id"]].append(s)

    families: list[dict] = []
    for engineering_family_id, fam_scenarios in sorted(
        by_domain.items(),
        key=lambda kv: -sum(s["ticket_count"] for s in kv[1]),
    ):
        engineering_family = ENGINEERING_FAMILY_BY_ID[engineering_family_id]
        families.append({
            "family_id": f"FAM-{engineering_family_id}",
            "engineering_family_id": engineering_family_id,
            "family_name": engineering_family["engineering_family"],
            "scenario_count": len(fam_scenarios),
            "ticket_count": sum(s["ticket_count"] for s in fam_scenarios),
            "max_risk_score": max(s["max_risk_score"] for s in fam_scenarios),
            "scenarios": sorted(fam_scenarios, key=lambda s: -s["max_risk_score"]),
        })

    print(f"  Total families: {len(families)}")
    return families


def build_risk_scores(
    scenarios: list[dict], total_tickets: int
) -> list[dict]:
    """Compute risk scores for all scenarios."""
    print("Computing risk scores ...")
    risk_records = []
    for s in scenarios:
        risk = compute_risk_index(s, total_tickets)
        s.update(risk)
        risk_records.append({
            "scenario_id": s["scenario_id"],
            "customer_symptom": s["customer_symptom"],
            "trigger_condition": s["trigger_condition"],
            "affected_function": s["affected_function"],
            "ticket_count": s["ticket_count"],
            **risk,
        })
    return risk_records


def build_knowledge_cards(scenarios: list[dict], families: list[dict]) -> list[dict]:
    """Build service knowledge cards for each scenario."""
    print("Building service knowledge cards ...")
    domain_by_id = {}
    for f in families:
        for s in f["scenarios"]:
            domain_by_id[s["scenario_id"]] = f["family_id"]

    cards = []
    for s in scenarios:
        # Diagnostic entry point
        diag_parts = []
        if s.get("rep_ecu"):
            diag_parts.append(f"ECU: {s['rep_ecu']}")
        if s.get("rca_root_cause") and s["rca_root_cause"] != "None":
            diag_parts.append(f"根因线索: {s['rca_root_cause'][:100]}")

        # Evidence gaps
        gaps = []
        if s.get("evidence_confidence") == "low":
            gaps.append("证据置信度低，场景提取不确定")
        if s.get("rca_root_cause", "").strip() in ("", "None", "none"):
            gaps.append("无根因分析记录")
        if s.get("recovery_action", "").strip() in ("", "None", "none recorded", "none"):
            gaps.append("无已知恢复措施")

        # Vehicle lines / SW versions
        sw_versions = "; ".join(f"{v}({n})" for v, n in s.get("top_sw_versions", [])[:3])

        cards.append({
            "scenario_id": s["scenario_id"],
            "family_id": domain_by_id.get(s["scenario_id"], ""),
            "customer_symptom": s["customer_symptom"],
            "trigger_condition": s["trigger_condition"],
            "affected_function": s["affected_function"],
            "risk_band": s["risk_band"],
            "risk_index": s["risk_index"],
            "diagnostic_entry": "; ".join(diag_parts),
            "recovery_action": _recorded_evidence_text(s.get("recovery_action")),
            "escalation_criteria": "",
            "evidence_gaps": "; ".join(gaps) if gaps else "无显著证据缺口",
            "vehicle_versions": sw_versions,
            "rep_ticket_id": s["rep_ticket_id"],
            "ticket_count": s["ticket_count"],
        })
    return cards


def build_scenario_found_in_function_mappings(
    scenarios: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flatten all original R&D function and owner links for every Scenario."""
    mappings = []
    for scenario in scenarios:
        for member in scenario.get("found_in_function_members", []):
            ticket_ids = member["ticket_ids"]
            mappings.append({
                "scenario_id": scenario["scenario_id"],
                "found_in_function": member["found_in_function"],
                "function_responsible": member["function_responsible"],
                "ticket_count": len(ticket_ids),
                "ticket_ids": "; ".join(ticket_ids),
                "mapping_role": "R&D attribution",
            })
    return sorted(
        mappings,
        key=lambda mapping: (
            mapping["scenario_id"],
            mapping["found_in_function"],
            mapping["function_responsible"],
        ),
    )


def _summarize_counter(values: Counter[str], limit: int = 5) -> str:
    return "; ".join(
        f"{value}({count})" for value, count in values.most_common(limit)
    )


def build_affected_function_mappings(scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate raw R&D functions without forcing them into one service entry."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for scenario in scenarios:
        grouped[scenario.get("rep_function") or "(not recorded)"].append(scenario)

    mappings = []
    for raw_function, mapped_scenarios in grouped.items():
        engineering_families = Counter(
            scenario.get("engineering_family_id", "OTHER_UNCLASSIFIED")
            for scenario in mapped_scenarios
        )
        engineering_family_id = engineering_families.most_common(1)[0][0]
        engineering_family = ENGINEERING_FAMILY_BY_ID[engineering_family_id]
        owners = Counter(
            owner
            for scenario in mapped_scenarios
            for owner, _ in scenario.get("owners", [])
            if owner
        )
        service_functions = Counter(
            scenario.get("service_function_id", "CDUNSPECIFIED")
            for scenario in mapped_scenarios
        )
        affected_functions = Counter(
            scenario.get("affected_function", "") for scenario in mapped_scenarios
        )
        reference_tree_ids = sorted({
            reference_tree_id
            for scenario in mapped_scenarios
            for reference_tree_id in scenario.get("reference_tree_ids", ())
        })
        mapping_sources = Counter(
            scenario.get("service_function_match_source", "")
            for scenario in mapped_scenarios
        )
        mappings.append({
            "raw_function": raw_function,
            "engineering_family_id": engineering_family_id,
            "engineering_family": engineering_family["engineering_family"],
            "owners": _summarize_counter(owners),
            "service_function_distribution": _summarize_counter(service_functions),
            "reference_tree_ids": "; ".join(reference_tree_ids),
            "scenario_count": len(mapped_scenarios),
            "ticket_count": sum(
                int(scenario.get("ticket_count", 0) or 0)
                for scenario in mapped_scenarios
            ),
            "affected_function_samples": _summarize_counter(affected_functions),
            "mapping_evidence": _summarize_counter(mapping_sources),
            "review_status": (
                "Review required: multiple service entries"
                if len(service_functions) > 1
                else "Single service entry"
            ),
        })
    return sorted(
        mappings,
        key=lambda mapping: (-mapping["ticket_count"], mapping["raw_function"]),
    )


def build_tree_growth_candidates(scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive candidate function branches from all Scenario evidence."""
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for scenario in scenarios:
        key = (
            scenario.get("service_domain", ""),
            scenario.get("service_function_id", ""),
            scenario.get("problem_type_id", ""),
            scenario.get("engineering_family_id", ""),
        )
        grouped[key].append(scenario)

    candidates = []
    for (
        service_domain,
        service_function_id,
        problem_type_id,
        engineering_family_id,
    ), candidate_scenarios in grouped.items():
        service_function = SERVICE_FUNCTION_BY_ID[service_function_id]
        engineering_family = ENGINEERING_FAMILY_BY_ID[engineering_family_id]
        recovery_actions = Counter(
            str(scenario.get("recovery_action", "") or "").strip()
            for scenario in candidate_scenarios
        )
        candidates.append({
            "service_domain": service_domain,
            "service_function_id": service_function_id,
            "problem_type_id": problem_type_id,
            "problem_type": PROBLEM_TYPE_LABELS[problem_type_id],
            "engineering_family_id": engineering_family_id,
            "engineering_family": engineering_family["engineering_family"],
            "reference_tree_ids": "; ".join(service_function["reference_tree_ids"]),
            "catalog_status": service_function["catalog_status"],
            "scenario_count": len(candidate_scenarios),
            "ticket_count": sum(
                int(scenario.get("ticket_count", 0) or 0)
                for scenario in candidate_scenarios
            ),
            "top_scenario_ids": "; ".join(
                scenario["scenario_id"]
                for scenario in sorted(
                    candidate_scenarios,
                    key=lambda scenario: (
                        -int(scenario.get("ticket_count", 0) or 0),
                        scenario["scenario_id"],
                    ),
                )[:10]
            ),
            "recovery_action_samples": _summarize_counter(
                Counter({
                    action: count
                    for action, count in recovery_actions.items()
                    if action and action.casefold() not in {"none", "none recorded", "无"}
                }),
                limit=3,
            ),
            "growth_status": (
                "Reference tree expansion candidate"
                if service_function["reference_tree_ids"]
                else "R&D-derived candidate tree; after-sales review required"
            ),
        })
    return sorted(
        candidates,
        key=lambda candidate: (
            candidate["service_domain"],
            candidate["service_function_id"],
            candidate["problem_type_id"],
            candidate["engineering_family_id"],
        ),
    )


def build_decision_tree_knowledge_base_payload(
    decision_trees: list[dict[str, Any]],
    affected_function_mappings: list[dict[str, Any]],
    tree_growth_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a small machine-readable index without embedding source images."""
    return {
        "schema_version": "phase09-decision-tree-kb-v1",
        "asset_policy": (
            "Source PNG is preserved externally; source_asset paths reference "
            "lossless WebP copies and are not embedded in this JSON."
        ),
        "after_sales_reference_trees": list(DECISION_TREE_SOURCE_CATALOG),
        "service_functions": list(SERVICE_FUNCTION_CATALOG),
        "engineering_families": list(ENGINEERING_FAMILY_CATALOG),
        "decision_trees": decision_trees,
        "affected_function_mappings": affected_function_mappings,
        "tree_growth_candidates": tree_growth_candidates,
    }


def _scenario_attribution_summary(scenario: dict[str, Any]) -> tuple[str, str, int]:
    found_in_functions = Counter()
    function_responsibles = Counter()
    members = scenario.get("found_in_function_members", [])
    for member in members:
        ticket_count = len(member["ticket_ids"])
        found_in_functions[member["found_in_function"]] += ticket_count
        function_responsibles[member["function_responsible"]] += ticket_count
    return (
        _summarize_counter(found_in_functions),
        _summarize_counter(function_responsibles),
        len(members),
    )


def build_service_function_tree_growth_candidates(
    scenarios: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Aggregate data-derived tree branches without using an R&D Family layer."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for scenario in scenarios:
        grouped[(
            scenario["service_function_id"],
            scenario["problem_type_id"],
        )].append(scenario)

    candidates = []
    for (service_function_id, problem_type_id), candidate_scenarios in grouped.items():
        service_function = SERVICE_FUNCTION_BY_ID[service_function_id]
        found_in_functions = Counter()
        function_responsibles = Counter()
        for scenario in candidate_scenarios:
            for member in scenario.get("found_in_function_members", []):
                ticket_count = len(member["ticket_ids"])
                found_in_functions[member["found_in_function"]] += ticket_count
                function_responsibles[member["function_responsible"]] += ticket_count
        reproduction_contexts = Counter(
            _diagnostic_context(scenario, _REPRODUCTION_CONTEXT_FIELDS)[1]
            for scenario in candidate_scenarios
        )
        technical_evidences = Counter(
            _technical_evidence(scenario)[1] for scenario in candidate_scenarios
        )
        candidates.append({
            "service_domain": service_function["service_domain"],
            "service_function": service_function["service_function"],
            "service_function_id": service_function_id,
            "problem_type_id": problem_type_id,
            "problem_type": PROBLEM_TYPE_LABELS[problem_type_id],
            "reference_tree_ids": "; ".join(service_function["reference_tree_ids"]),
            "scenario_count": len(candidate_scenarios),
            "ticket_count": sum(
                int(scenario.get("ticket_count", 0) or 0)
                for scenario in candidate_scenarios
            ),
            "customer_symptom_samples": _summarize_counter(Counter(
                _recorded_evidence_text(scenario.get("customer_symptom"))
                for scenario in candidate_scenarios
            ), limit=5),
            "trigger_condition_samples": _summarize_counter(Counter(
                _recorded_evidence_text(scenario.get("trigger_condition"))
                for scenario in candidate_scenarios
            ), limit=5),
            "diagnostic_context_samples": _summarize_counter(
                reproduction_contexts, limit=5
            ),
            "technical_evidence_samples": _summarize_counter(
                technical_evidences, limit=5
            ),
            "found_in_function_distribution": _summarize_counter(found_in_functions),
            "function_responsible_distribution": _summarize_counter(function_responsibles),
            "top_scenario_ids": "; ".join(
                scenario["scenario_id"]
                for scenario in sorted(
                    candidate_scenarios,
                    key=lambda scenario: (
                        -int(scenario.get("ticket_count", 0) or 0),
                        scenario["scenario_id"],
                    ),
                )[:10]
            ),
            "recorded_recovery_action_samples": _summarize_counter(Counter(
                _recorded_evidence_text(scenario.get("recovery_action"))
                for scenario in candidate_scenarios
                if _recorded_evidence_text(scenario.get("recovery_action"))
            ), limit=3),
            "growth_status": "Data-derived branch; review tree-specific diagnostic logic",
        })
    return sorted(
        candidates,
        key=lambda candidate: (
            candidate["service_function_id"],
            candidate["problem_type_id"],
        ),
    )


def build_service_function_knowledge_base_payload(
    decision_trees: list[dict[str, Any]],
    decision_nodes: list[dict[str, Any]],
    scenario_found_in_function_mappings: list[dict[str, Any]],
    tree_growth_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Serialize the customer-first evidence-gate model without Family routing."""
    return {
        "schema_version": "phase09-decision-tree-kb-v6",
        "workflow_template": after_sales_evidence_gate_metadata(),
        "tree_generation": (
            "After-sales-style Yes/No evidence gates generated from Service Function, customer "
            "complaint, and recorded Phase 09 evidence. YES/NO is evidence presence only; "
            "reference images are linked only."
        ),
        "asset_policy": (
            "Source PNG is preserved externally; source_asset paths reference "
            "lossless WebP copies and are not embedded in this JSON."
        ),
        "after_sales_reference_trees": list(DECISION_TREE_SOURCE_CATALOG),
        "service_functions": list(SERVICE_FUNCTION_CATALOG),
        "decision_trees": decision_trees,
        "decision_tree_nodes": decision_nodes,
        "scenario_found_in_function_mappings": scenario_found_in_function_mappings,
        "tree_growth_candidates": tree_growth_candidates,
    }


def build_symptom_solution_knowledge_base_payload(
    decision_trees: list[dict[str, Any]],
    decision_nodes: list[dict[str, Any]],
    scenario_found_in_function_mappings: list[dict[str, Any]],
    tree_growth_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Serialize the V12 problem-navigation model without evidence gates."""
    return {
        "schema_version": "phase09-decision-tree-kb-v7",
        "tree_generation": (
            "Symptom-driven problem navigation from Service Function through issue group, "
            "symptom pattern, and trigger pattern."
        ),
        "solution_source_policy": (
            "Ticket-recorded solution text is copied from Recovery Action only; it can be a "
            "workaround and does not establish root cause or an approved repair. Blank means "
            "no solution was recorded."
        ),
        "asset_policy": (
            "Source PNG is preserved externally; source_asset paths reference "
            "lossless WebP copies and are not embedded in this JSON."
        ),
        "after_sales_reference_trees": list(DECISION_TREE_SOURCE_CATALOG),
        "service_functions": list(SERVICE_FUNCTION_CATALOG),
        "decision_trees": decision_trees,
        "decision_tree_nodes": decision_nodes,
        "scenario_found_in_function_mappings": scenario_found_in_function_mappings,
        "tree_growth_candidates": tree_growth_candidates,
    }


def build_excel_v4_legacy(
    scenarios: list[dict],
    families: list[dict],
    risk_records: list[dict],
    knowledge_cards: list[dict],
    excluded: list[dict],
    extractions: list[dict],
    metadata: dict[str, dict],
    output_xlsx: Path = OUTPUT_XLSX,
    decision_tree_html: Path | None = None,
    knowledge_base_json: Path | None = None,
) -> None:
    """Build the multi-sheet Excel output."""
    print("Building Excel output ...")
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    total_tickets = len(extractions)
    decision_trees, decision_nodes, scenario_tree_mappings = build_service_decision_trees(
        scenarios, families
    )
    scenario_by_id = {scenario["scenario_id"]: scenario for scenario in scenarios}
    affected_function_mappings = build_affected_function_mappings(scenarios)
    tree_growth_candidates = build_tree_growth_candidates(scenarios)
    if decision_tree_html is None:
        decision_tree_html = output_xlsx.with_name(
            f"{output_xlsx.stem}_Decision_Tree_Catalog.html"
        )
    if knowledge_base_json is None:
        knowledge_base_json = output_xlsx.with_name(
            f"{output_xlsx.stem}_Decision_Tree_Knowledge_Base.json"
        )

    # ── Sheet 00: 总览 ──
    s0 = wb.create_sheet("00_总览")
    s0.append(["IDCEVO Phase 09 V4 Service-Domain Decision Tree Knowledge Base"])
    s0.append([])
    s0.append(["数据快照", "2026-09-09", "分析范围", "18,000 Phase 09 Ticket"])
    s0.append(["聚类主键", "Customer Symptom + Trigger Condition + Affected Function"])
    s0.append([])
    s0.append(["指标", "数量", "占比", "解读"])
    s0.append(["总Ticket数", total_tickets, "100%", "全量 Phase 09"])
    s0.append(["现场场景Ticket数", sum(s["ticket_count"] for s in scenarios), f"{sum(s['ticket_count'] for s in scenarios)/total_tickets*100:.1f}%", "is_field_scenario=True"])
    s0.append(["排除Ticket数", len(excluded), f"{len(excluded)/total_tickets*100:.1f}%", "非客户现场场景"])
    s0.append(["Scenario总数", len(scenarios), "", "按症状+触发+功能聚类"])
    s0.append(["Scenario Family总数", len(families), "", "按稳定研发责任域分组"])
    s0.append(["售后参考树数", len(DECISION_TREE_SOURCE_CATALOG), "", "售后提供的 PNG 决策树知识资产"])
    s0.append(["数据派生服务入口数", sum(1 for tree in decision_trees if tree["scenario_count"]), "", "有客户现场 Scenario 映射的 Service Function"])
    s0.append([])
    s0.append(["风险等级分布", "Scenario数", "Ticket数", "占比"])
    for band in ["Critical", "High", "Medium", "Low"]:
        band_scenarios = [s for s in scenarios if s.get("risk_band") == band]
        band_tickets = sum(s["ticket_count"] for s in band_scenarios)
        s0.append([
            band,
            len(band_scenarios),
            band_tickets,
            f"{band_tickets/total_tickets*100:.1f}%",
        ])
    s0.append([])
    s0.append(["排除原因分布", "Ticket数", "占比", ""])
    excl_reasons = Counter(e["exclusion_reason"] for e in excluded)
    for reason, count in excl_reasons.most_common():
        s0.append([reason, count, f"{count/total_tickets*100:.1f}%", ""])
    s0.append([])
    s0.append(["Top 15 Scenario Family", "Scenario数", "Ticket数", "最高风险分"])
    for f in families[:15]:
        s0.append([f["family_name"], f["scenario_count"], f["ticket_count"], f["max_risk_score"]])
    style_header(s0, 4, row=7)
    s0.cell(row=1, column=1).font = Font(bold=True, size=14)
    autosize(s0, [30, 15, 15, 40])

    # ── Sheet 01: Scenario Library ──
    s1 = wb.create_sheet("01_Scenario_Library")
    s1_headers = [
        "Scenario ID", "Family ID", "Engineering Family ID", "Engineering Family",
        "R&D Function", "R&D Owner", "Service Domain", "Service Function ID",
        "Service Function", "Problem Type", "Reference Tree IDs", "Service Mapping Source",
        "Catalog Status", "Customer Symptom", "Trigger Condition", "Affected Function",
        "Ticket Count", "Risk Band", "Risk Index", "Max Risk Score", "Prod Exposure Count",
        "Rep Ticket ID", "Rep Title", "Observed Behaviour", "Expected Behaviour",
        "Recovery Action", "Closure Decision", "Evidence Confidence",
    ]
    s1.append(s1_headers)
    # Build family lookup
    family_lookup: dict[str, str] = {}
    for f in families:
        for s in f["scenarios"]:
            family_lookup[s["scenario_id"]] = f["family_id"]
    for s in scenarios:
        owner_str = "; ".join(f"{o}({n})" for o, n in s.get("owners", [])) if s.get("owners") else ""
        s1.append([
            s["scenario_id"], family_lookup.get(s["scenario_id"], ""),
            s.get("engineering_family_id", ""), s.get("engineering_family", ""),
            s.get("rep_function", ""), owner_str, s.get("service_domain", ""),
            s["service_function_id"], s["service_function"], s["problem_type"],
            "; ".join(s.get("reference_tree_ids", ())),
            s.get("service_function_match_source", ""),
            s.get("service_function_catalog_status", ""), s["customer_symptom"],
            s["trigger_condition"], s["affected_function"], s["ticket_count"],
            s["risk_band"], s["risk_index"], s["max_risk_score"],
            s["prod_exposure_count"], s["rep_ticket_id"], s["rep_title"][:100],
            s["observed_behaviour"][:200], s["expected_behaviour"][:200],
            s["recovery_action"][:200], s["closure_decision"], s["evidence_confidence"],
        ])
    style_header(s1, len(s1_headers))
    autosize(s1, [10, 28, 28, 38, 46, 32, 32, 28, 30, 34, 48, 42, 24, 30, 35, 26, 10, 10, 10, 12, 14, 14, 42, 42, 42, 36, 22, 12])
    add_filter_and_freeze(s1, len(s1_headers), "D2")
    risk_band_column = s1_headers.index("Risk Band") + 1
    for row_idx in range(2, s1.max_row + 1):
        band = s1.cell(row=row_idx, column=risk_band_column).value
        fill = RISK_FILLS.get(band)
        if fill:
            for c in range(1, len(s1_headers) + 1):
                s1.cell(row=row_idx, column=c).fill = fill

    # ── Sheet 02: Scenario Families ──
    s2 = wb.create_sheet("02_Scenario_Families")
    s2_headers = [
        "Family ID", "Engineering Family ID", "R&D Responsibility Family", "Scenario Count", "Ticket Count",
        "Max Risk Score", "Critical", "High", "Medium", "Low",
    ]
    s2.append(s2_headers)
    for f in families:
        band_counts = Counter(s.get("risk_band", "") for s in f["scenarios"])
        s2.append([
            f["family_id"], f.get("engineering_family_id", ""), f["family_name"],
            f["scenario_count"], f["ticket_count"],
            f["max_risk_score"],
            band_counts.get("Critical", 0), band_counts.get("High", 0),
            band_counts.get("Medium", 0), band_counts.get("Low", 0),
        ])
    style_header(s2, len(s2_headers))
    autosize(s2, [30, 28, 42, 14, 14, 14, 10, 10, 10, 10])
    add_filter_and_freeze(s2, len(s2_headers), "C2")

    # ── Sheet 03: Field Escape Risk ──
    s3 = wb.create_sheet("03_Field_Escape_Risk")
    s3_headers = [
        "Scenario ID", "Customer Symptom", "Trigger Condition", "Affected Function",
        "Ticket Count", "Customer Severity (0.30)",
        "Production Applicability (0.25)", "Exposure Frequency (0.20)",
        "Perceptibility (0.10)", "Diagnosability (0.10)",
        "Recoverability (0.05)", "Risk Index", "Risk Band",
    ]
    s3.append(s3_headers)
    for s in sorted(scenarios, key=lambda s: -s["risk_index"]):
        s3.append([
            s["scenario_id"], s["customer_symptom"], s["trigger_condition"],
            s["affected_function"], s["ticket_count"],
            s["severity"], s["production_applicability"],
            s["exposure_frequency"], s["perceptibility"],
            s["diagnosability"], s["recoverability"],
            s["risk_index"], s["risk_band"],
        ])
    style_header(s3, len(s3_headers))
    autosize(s3, [10, 30, 35, 22, 8, 12, 14, 12, 10, 10, 10, 8, 8])
    add_filter_and_freeze(s3, len(s3_headers), "E2")
    for row_idx in range(2, s3.max_row + 1):
        band = s3.cell(row=row_idx, column=13).value
        fill = RISK_FILLS.get(band)
        if fill:
            for c in range(1, len(s3_headers) + 1):
                s3.cell(row=row_idx, column=c).fill = fill

    # ── Sheet 04: Service Knowledge Cards ──
    s4 = wb.create_sheet("04_Service_Knowledge_Cards")
    s4_headers = [
        "Scenario ID", "Family ID", "Engineering Family", "R&D Function", "Service Domain",
        "Service Function", "Problem Type", "Reference Tree IDs", "客户语言症状", "触发条件",
        "适用功能", "风险等级", "风险指数", "诊断入口",
        "已知恢复/Workaround", "升级条件", "证据限制", "涉及车型/版本", "代表Ticket", "Ticket数",
    ]
    s4.append(s4_headers)
    for card in sorted(knowledge_cards, key=lambda c: -c["risk_index"]):
        scenario = scenario_by_id.get(card["scenario_id"], {})
        s4.append([
            card["scenario_id"], card["family_id"],
            scenario.get("engineering_family", ""), scenario.get("rep_function", ""),
            scenario.get("service_domain", ""), scenario.get("service_function", ""),
            scenario.get("problem_type", ""), "; ".join(scenario.get("reference_tree_ids", ())),
            card["customer_symptom"], card["trigger_condition"], card["affected_function"],
            card["risk_band"], card["risk_index"], card["diagnostic_entry"],
            card["recovery_action"][:200], card["escalation_criteria"],
            card["evidence_gaps"], card["vehicle_versions"], card["rep_ticket_id"],
            card["ticket_count"],
        ])
    style_header(s4, len(s4_headers))
    autosize(s4, [10, 28, 38, 46, 32, 30, 34, 48, 30, 35, 26, 10, 10, 40, 40, 40, 40, 30, 12, 8])
    add_filter_and_freeze(s4, len(s4_headers), "D2")

    # ── Sheet 05: Ticket Mapping ──
    s5 = wb.create_sheet("05_Ticket_Mapping")
    s5_headers = [
        "Ticket ID", "Scenario ID", "Family ID", "Engineering Family ID", "Engineering Family",
        "R&D Function", "R&D Owner", "Service Domain", "Service Function ID", "Service Function",
        "Problem Type", "Reference Tree IDs", "Affected Function", "Customer Symptom",
        "Trigger Condition", "is_field_scenario", "Risk Tier", "Risk Score", "Closure Reason", "Ticket Title",
    ]
    s5.append(s5_headers)
    # Build ticket→scenario lookup
    ticket_to_scenario: dict[str, dict] = {}
    for s in scenarios:
        for tid in s["ticket_ids"]:
            ticket_to_scenario[tid] = s
    for rec in extractions:
        tid = str(rec.get("ticket_id", ""))
        ext = rec.get("extraction", {})
        meta = metadata.get(tid, {})
        is_field = bool(ext.get("is_field_scenario", True))
        scenario = ticket_to_scenario.get(tid)
        if scenario:
            s5.append([
                tid, scenario["scenario_id"],
                family_lookup.get(scenario["scenario_id"], ""),
                scenario.get("engineering_family_id", ""), scenario.get("engineering_family", ""),
                meta.get("function", ""), meta.get("function_responsible", ""),
                scenario.get("service_domain", ""), scenario["service_function_id"],
                scenario["service_function"], scenario["problem_type"],
                "; ".join(scenario.get("reference_tree_ids", ())),
                scenario["affected_function"], scenario["customer_symptom"],
                scenario["trigger_condition"], "是" if is_field else "否",
                meta.get("risk_tier", ""), meta.get("risk_score", ""),
                meta.get("closure_reason", ""), meta.get("title", "")[:80],
            ])
        else:
            s5.append([
                tid, "", "", "", "", meta.get("function", ""),
                meta.get("function_responsible", ""), "", "", "", "", "", "", "", "", "否",
                meta.get("risk_tier", ""), meta.get("risk_score", ""),
                meta.get("closure_reason", ""), meta.get("title", "")[:80],
            ])
    style_header(s5, len(s5_headers))
    autosize(s5, [12, 12, 28, 28, 38, 48, 32, 32, 28, 30, 34, 48, 26, 30, 35, 10, 22, 10, 22, 42])
    add_filter_and_freeze(s5, len(s5_headers), "B2")

    # ── Sheet 06: Excluded Tickets ──
    s6 = wb.create_sheet("06_Excluded_Tickets")
    s6_headers = [
        "Ticket ID", "Exclusion Reason", "Function", "Title",
        "Risk Tier", "Closure Reason",
    ]
    s6.append(s6_headers)
    for e in sorted(excluded, key=lambda e: e["ticket_id"]):
        s6.append([
            e["ticket_id"], e["exclusion_reason"], e["function"][:80],
            e["title"][:100], e["risk_tier"], e["closure_reason"],
        ])
    style_header(s6, len(s6_headers))
    autosize(s6, [12, 20, 30, 50, 22, 20])
    add_filter_and_freeze(s6, len(s6_headers), "C2")

    # ── Sheet 07: Data Dictionary ──
    s7 = wb.create_sheet("07_Data_Dictionary")
    s7.append(["字段定义与治理规则"])
    s7.append(["用于全量扩展和后续 Claim / 售后数据回流"])
    s7.append([])
    s7.append(["字段", "层级", "定义", "允许值 / 规则"])
    dict_rows = [
        ("Scenario ID", "Scenario", "标准化失效场景唯一标识", "同一客户症状+触发条件+功能上下文共用 ID"),
        ("Family ID", "Family", "研发功能域/责任上下文中的场景族标识", "FAM-NN；不作为售后排查入口"),
        ("Engineering Family ID", "Engineering Family", "稳定的研发责任域标识", "由原始 R&D Function 优先匹配；与售后入口独立"),
        ("Engineering Family", "Engineering Family", "研发问题分类和负责人回溯的上层域", "例如 Audio / Media Platform、Driver Assistance、Phone Projection"),
        ("R&D Function", "Ticket / Scenario", "Octane 原始功能字段", "保留原始研发颗粒度；可在 14_Affected_Function_Mapping 审核归属"),
        ("Service Domain", "After-sales", "售后大功能域", "例如 Phone Projection、Remote / Mobile Services、In-Car Entertainment / Apps"),
        ("Service Function ID", "Service Function", "售后选择 Decision Tree 的标准功能入口", "固定目录；无匹配时进入 CDUNSPECIFIED 默认树"),
        ("Problem Type", "Problem Type", "客户可感知问题的二级标准分类", "连接/配对、声音/媒体、显示/投屏、稳定性等；不改变 Family"),
        ("Reference Tree ID", "After-sales Reference", "售后提供的原始决策树图的稳定标识", "DTREF-*；对应 12_After_Sales_Reference_Trees 和无损 WebP 资产"),
        ("Tree ID", "Decision Tree", "一个 Service Function 对应的可执行排查树标识", "DT-<SERVICE_FUNCTION_ID>"),
        ("Node ID", "Decision Tree Node", "决策树内判断、动作、路由、处置或升级节点标识", "C=Condition；A=Action；R=Route/Resolution；E=Escalation"),
        ("TRUE Next Node / FALSE Next Node", "Decision Tree Node", "Condition 节点的明确分支去向", "TRUE/FALSE 必须指向同一 Tree ID 内的 Node ID"),
        ("Source Status", "Decision Tree", "树规则的来源与批准状态", "当前为工单数据驱动模板，须经售后规则校准后作为正式流程"),
        ("Customer Symptom", "Scenario", "客户感知到的故障现象", "客户/售后语言描述，≤120字"),
        ("Trigger Condition", "Scenario", "触发故障的具体条件或操作链", "≤150字，需包含具体触发动作"),
        ("Affected Function", "Scenario", "受影响的功能域", "标准化功能域名称"),
        ("Risk Index", "Scenario", "加权风险指数", "Severity×0.30+ProdApp×0.25+Exposure×0.20+Percept×0.10+Diag×0.10+Recov×0.05"),
        ("Risk Band", "Scenario", "风险等级", "Critical(≥4.0)/High(≥3.0)/Medium(≥2.0)/Low(<2.0)"),
        ("is_field_scenario", "Ticket", "是否为客户现场场景", "true=进入场景库; false=排除"),
        ("exclusion_reason", "Ticket", "排除原因", "spec_confirmed/plant_only/test_tooling/not_customer_facing/document_only/master_fixed/pure_process/no_symptom"),
        ("evidence_confidence", "Ticket", "证据置信度", "high/medium/low"),
        ("Customer Severity", "Scenario", "客户影响严重度", "1-5, 5=安全/不可驾驶/充电风险"),
        ("Production Applicability", "Scenario", "生产适用性", "1-5, 基于生产暴露票占比"),
        ("Exposure Frequency", "Scenario", "暴露频率", "1-5, 基于场景票数占比"),
        ("Perceptibility", "Scenario", "可感知性", "1-5, 客户是否容易察觉"),
        ("Diagnosability", "Scenario", "可诊断性", "1-5, DTC/日志/根因可用性"),
        ("Recoverability", "Scenario", "可恢复性", "1-5, 已知workaround=高分"),
    ]
    for row_data in dict_rows:
        s7.append(list(row_data))
    style_header(s7, 4, row=4)
    s7.cell(row=1, column=1).font = Font(bold=True, size=14)
    autosize(s7, [25, 10, 40, 50])

    # ── Sheet 08: Service Function Catalog ──
    s8 = wb.create_sheet("08_Service_Function_Catalog")
    s8_headers = [
        "Service Function ID", "Service Function", "Service Domain", "Display Name", "Tree ID",
        "Reference Tree IDs", "Catalog Status", "Scenario Count", "Ticket Count", "Problem Types", "Source Status",
    ]
    s8.append(s8_headers)
    for tree in decision_trees:
        s8.append([
            tree["service_function_id"], tree["service_function"],
            tree["service_domain"], tree["service_function_label"], tree["tree_id"],
            "; ".join(tree["reference_tree_ids"]), tree["catalog_status"],
            tree["scenario_count"], tree["ticket_count"], "; ".join(tree["problem_types"]),
            tree["source_status"],
        ])
    style_header(s8, len(s8_headers))
    autosize(s8, [24, 32, 34, 42, 20, 48, 24, 14, 14, 50, 56])
    add_filter_and_freeze(s8, len(s8_headers), "D2")

    # ── Sheet 09: Decision Tree Catalog ──
    s9 = wb.create_sheet("09_Decision_Tree_Catalog")
    s9_headers = [
        "Tree ID", "Tree Name", "Service Domain", "Service Function ID", "Service Function",
        "Reference Tree IDs", "Catalog Status", "Scenario Count", "Ticket Count", "Problem Types",
        "Source Status", "Visual Diagram",
    ]
    s9.append(s9_headers)
    for tree in decision_trees:
        s9.append([
            tree["tree_id"], tree["tree_name"], tree["service_domain"],
            tree["service_function_id"], tree["service_function"],
            "; ".join(tree["reference_tree_ids"]), tree["catalog_status"],
            tree["scenario_count"], tree["ticket_count"], "; ".join(tree["problem_types"]),
            tree["source_status"],
            decision_tree_html.name,
        ])
        diagram_cell = s9.cell(row=s9.max_row, column=12)
        diagram_cell.hyperlink = decision_tree_html.name
        diagram_cell.style = "Hyperlink"
    style_header(s9, len(s9_headers))
    autosize(s9, [20, 38, 34, 24, 30, 48, 24, 14, 14, 50, 56, 36])
    add_filter_and_freeze(s9, len(s9_headers), "C2")

    # ── Sheet 10: Decision Tree Nodes ──
    s10 = wb.create_sheet("10_Decision_Tree_Nodes")
    s10_headers = [
        "Tree ID", "Node ID", "Node Type", "Question / Action", "TRUE Next Node",
        "FALSE Next Node", "Next Node", "Evidence Required",
    ]
    s10.append(s10_headers)
    for node in decision_nodes:
        s10.append([
            node["tree_id"], node["node_id"], node["node_type"],
            node["question_or_action"], node["true_next_node"],
            node["false_next_node"], node["next_node"], node["evidence_required"],
        ])
    style_header(s10, len(s10_headers))
    autosize(s10, [20, 12, 16, 60, 18, 18, 14, 42])
    add_filter_and_freeze(s10, len(s10_headers), "D2")

    # ── Sheet 11: Scenario-to-Tree Mapping ──
    s11 = wb.create_sheet("11_Scenario_Tree_Mapping")
    s11_headers = [
        "Scenario ID", "Tree ID", "Family ID", "Engineering Family ID", "Service Domain",
        "Service Function ID", "Service Function", "Problem Type ID", "Problem Type",
        "Reference Tree IDs", "Mapping Source", "Catalog Status", "Mapping Role",
    ]
    s11.append(s11_headers)
    for mapping in scenario_tree_mappings:
        scenario = scenario_by_id[mapping["scenario_id"]]
        s11.append([
            mapping["scenario_id"], mapping["tree_id"], mapping["family_id"],
            scenario.get("engineering_family_id", ""), scenario.get("service_domain", ""),
            mapping["service_function_id"], mapping["service_function"],
            mapping["problem_type_id"], mapping["problem_type"],
            "; ".join(scenario.get("reference_tree_ids", ())),
            scenario.get("service_function_match_source", ""),
            scenario.get("service_function_catalog_status", ""), mapping["mapping_role"],
        ])
    style_header(s11, len(s11_headers))
    autosize(s11, [14, 20, 28, 28, 34, 28, 30, 28, 36, 48, 42, 24, 24])
    add_filter_and_freeze(s11, len(s11_headers), "D2")

    # ── Sheet 12: After-sales Reference Trees ──
    s12 = wb.create_sheet("12_After_Sales_Reference_Trees")
    s12_headers = [
        "Reference Tree ID", "Source File", "Service Function ID", "Service Function",
        "Service Domain", "Tree Version", "Source Asset", "Triage Pattern",
        "Cross-Tree Routes", "Catalog Status",
    ]
    s12.append(s12_headers)
    for reference_tree in DECISION_TREE_SOURCE_CATALOG:
        service_function = SERVICE_FUNCTION_BY_ID[
            reference_tree["service_function_id"]
        ]
        s12.append([
            reference_tree["reference_tree_id"], reference_tree["source_file"],
            reference_tree["service_function_id"], service_function["service_function"],
            service_function["service_domain"], reference_tree["tree_version"],
            reference_tree["source_asset"], reference_tree["triage_pattern"],
            reference_tree["cross_tree_routes"], reference_tree["catalog_status"],
        ])
        asset_cell = s12.cell(row=s12.max_row, column=7)
        asset_cell.hyperlink = reference_tree["source_asset"]
        asset_cell.style = "Hyperlink"
    style_header(s12, len(s12_headers))
    autosize(s12, [24, 42, 25, 32, 32, 14, 58, 68, 40, 22])
    add_filter_and_freeze(s12, len(s12_headers), "D2")

    # ── Sheet 13: Engineering Family Catalog ──
    s13 = wb.create_sheet("13_Engineering_Family_Catalog")
    s13_headers = [
        "Engineering Family ID", "Engineering Family", "R&D Function Match Terms",
        "Default Service Function", "Default Service Domain",
    ]
    s13.append(s13_headers)
    for engineering_family in ENGINEERING_FAMILY_CATALOG:
        engineering_family_id = engineering_family["engineering_family_id"]
        default_service_function_id = _ENGINEERING_FAMILY_SERVICE_FALLBACKS[
            engineering_family_id
        ]
        default_service_function = SERVICE_FUNCTION_BY_ID[
            default_service_function_id
        ]
        s13.append([
            engineering_family_id, engineering_family["engineering_family"],
            "; ".join(engineering_family["aliases"]),
            default_service_function_id, default_service_function["service_domain"],
        ])
    style_header(s13, len(s13_headers))
    autosize(s13, [28, 42, 80, 28, 36])
    add_filter_and_freeze(s13, len(s13_headers), "C2")

    # ── Sheet 14: Affected Function Mapping ──
    s14 = wb.create_sheet("14_Affected_Function_Mapping")
    s14_headers = [
        "R&D Function", "Engineering Family ID", "Engineering Family", "R&D Owners",
        "Service Function Distribution", "Reference Tree IDs", "Scenario Count", "Ticket Count",
        "Affected Function Samples", "Mapping Evidence", "Review Status",
    ]
    s14.append(s14_headers)
    for mapping in affected_function_mappings:
        s14.append([
            mapping["raw_function"], mapping["engineering_family_id"],
            mapping["engineering_family"], mapping["owners"],
            mapping["service_function_distribution"], mapping["reference_tree_ids"],
            mapping["scenario_count"], mapping["ticket_count"],
            mapping["affected_function_samples"], mapping["mapping_evidence"],
            mapping["review_status"],
        ])
    style_header(s14, len(s14_headers))
    autosize(s14, [48, 28, 38, 36, 45, 45, 14, 14, 55, 55, 34])
    add_filter_and_freeze(s14, len(s14_headers), "D2")

    # ── Sheet 15: Tree Growth Candidates ──
    s15 = wb.create_sheet("15_Tree_Growth_Candidates")
    s15_headers = [
        "Service Domain", "Service Function ID", "Problem Type ID", "Problem Type",
        "Engineering Family ID", "Engineering Family", "Reference Tree IDs", "Catalog Status",
        "Scenario Count", "Ticket Count", "Top Scenario IDs", "Recovery Action Samples",
        "Growth Status",
    ]
    s15.append(s15_headers)
    for candidate in tree_growth_candidates:
        s15.append([
            candidate["service_domain"], candidate["service_function_id"],
            candidate["problem_type_id"], candidate["problem_type"],
            candidate["engineering_family_id"], candidate["engineering_family"],
            candidate["reference_tree_ids"], candidate["catalog_status"],
            candidate["scenario_count"], candidate["ticket_count"],
            candidate["top_scenario_ids"], candidate["recovery_action_samples"],
            candidate["growth_status"],
        ])
    style_header(s15, len(s15_headers))
    autosize(s15, [34, 28, 28, 36, 28, 38, 42, 24, 14, 14, 68, 68, 48])
    add_filter_and_freeze(s15, len(s15_headers), "D2")

    # ── Save ──
    print("Saving Excel ...")
    output_xlsx.parent.mkdir(parents=True, exist_ok=True)
    decision_tree_html.parent.mkdir(parents=True, exist_ok=True)
    knowledge_base_json.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_xlsx)
    decision_tree_html.write_text(
        render_decision_tree_catalog_html(decision_trees, decision_nodes),
        encoding="utf-8",
    )
    knowledge_base_json.write_text(
        json.dumps(
            build_decision_tree_knowledge_base_payload(
                decision_trees,
                affected_function_mappings,
                tree_growth_candidates,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Saved Excel to: {output_xlsx}")
    print(f"Saved decision trees to: {decision_tree_html}")
    print(f"Saved decision-tree knowledge base to: {knowledge_base_json}")


def build_excel(
    scenarios: list[dict],
    families: list[dict],
    risk_records: list[dict],
    knowledge_cards: list[dict],
    excluded: list[dict],
    extractions: list[dict],
    metadata: dict[str, dict],
    output_xlsx: Path = OUTPUT_XLSX,
    decision_tree_html: Path | None = None,
    knowledge_base_json: Path | None = None,
) -> None:
    """Build the V12 customer-facing symptom-to-solution workbook."""
    print("Building V12 after-sales symptom-to-solution workbook ...")
    decision_trees, decision_nodes, _ = build_symptom_solution_trees(
        scenarios, families
    )
    enrich_service_function_source_evidence(decision_trees, metadata)
    scenario_by_id = {scenario["scenario_id"]: scenario for scenario in scenarios}
    service_function_by_tree_id = {
        tree["tree_id"]: tree["service_function"] for tree in decision_trees
    }
    found_in_function_mappings = build_scenario_found_in_function_mappings(scenarios)
    tree_growth_candidates = build_service_function_tree_growth_candidates(scenarios)
    if decision_tree_html is None:
        decision_tree_html = output_xlsx.with_name(
            f"{output_xlsx.stem}_Decision_Tree_Catalog.html"
        )
    if knowledge_base_json is None:
        knowledge_base_json = output_xlsx.with_name(
            f"{output_xlsx.stem}_Decision_Tree_Knowledge_Base.json"
        )

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    total_tickets = len(extractions)

    # ── Sheet 00: Overview ──
    s0 = wb.create_sheet("00_总览")
    s0.append(["IDCEVO Phase 09 V12 Symptom-to-Solution Trees"])
    s0.append([])
    s0.append(["数据快照", "2026-09-17", "分析范围", "Phase 09 Ticket"])
    s0.append(["问题路径", "Service Function -> Issue Group -> Symptom Pattern -> Trigger Pattern -> Ticket-recorded Solution"])
    s0.append([])
    s0.append(["指标", "数量", "占比", "解读"])
    s0.append(["总 Ticket 数", total_tickets, "100%", "全量 Phase 09"])
    field_ticket_count = sum(scenario["ticket_count"] for scenario in scenarios)
    s0.append(["现场场景 Ticket 数", field_ticket_count, f"{field_ticket_count / total_tickets * 100:.1f}%", "is_field_scenario=True"])
    s0.append(["排除 Ticket 数", len(excluded), f"{len(excluded) / total_tickets * 100:.1f}%", "非客户现场场景"])
    s0.append(["Scenario 数", len(scenarios), "", "保留客户症状与触发条件"])
    s0.append(["有场景的 Service Function 数", sum(tree["scenario_count"] > 0 for tree in decision_trees), "", "售后决策树入口"])
    s0.append(["参考树图数", len(DECISION_TREE_SOURCE_CATALOG), "", "售后提供图，仅作参考"])
    s0.append(["Found In Function 映射数", len(found_in_function_mappings), "", "Scenario 到研发功能/负责人的一对多关系"])
    s0.append([])
    s0.append(["风险等级分布", "Scenario 数", "Ticket 数", "占比"])
    for band in ["Critical", "High", "Medium", "Low"]:
        band_scenarios = [scenario for scenario in scenarios if scenario.get("risk_band") == band]
        band_tickets = sum(scenario["ticket_count"] for scenario in band_scenarios)
        s0.append([band, len(band_scenarios), band_tickets, f"{band_tickets / total_tickets * 100:.1f}%"])
    style_header(s0, 4, row=7)
    s0.cell(row=1, column=1).font = Font(bold=True, size=14)
    autosize(s0, [34, 18, 16, 62])

    # ── Sheet 01: Scenario Library ──
    s1 = wb.create_sheet("01_Scenario_Library")
    s1_headers = [
        "Service Function", "Scenario ID", "Problem Type", "Customer Symptom",
        "Trigger Condition",
        "Found In Functions", "Function Responsible", "Found In Function Count",
        "Ticket Count", "Risk Band", "Risk Index", "Rep Ticket ID", "Rep Title",
        "Observed Behaviour", "Expected Behaviour", "Recorded Recovery Action",
        "Closure Decision", "Evidence Confidence", "Service Mapping Source", "Catalog Status",
    ]
    s1.append(s1_headers)
    for scenario in sorted(
        scenarios,
        key=lambda item: (
            item["service_function"],
            item["customer_symptom"],
            item["scenario_id"],
        ),
    ):
        found_in_functions, function_responsibles, found_in_function_count = (
            _scenario_attribution_summary(scenario)
        )
        s1.append([
            scenario["service_function"], scenario["scenario_id"], scenario["problem_type"],
            scenario["customer_symptom"], scenario["trigger_condition"],
            found_in_functions, function_responsibles, found_in_function_count,
            scenario["ticket_count"], scenario["risk_band"], scenario["risk_index"],
            scenario["rep_ticket_id"], scenario["rep_title"][:100],
            scenario["observed_behaviour"][:200], scenario["expected_behaviour"][:200],
            _recorded_evidence_text(scenario.get("recovery_action"))[:200],
            scenario["closure_decision"], scenario["evidence_confidence"],
            scenario.get("service_function_match_source", ""),
            scenario.get("service_function_catalog_status", ""),
        ])
    style_header(s1, len(s1_headers))
    autosize(s1, [30, 14, 34, 36, 38, 48, 42, 16, 12, 12, 12, 14, 44, 44, 44, 44, 24, 18, 42, 26])
    add_filter_and_freeze(s1, len(s1_headers), "F2")
    risk_band_column = s1_headers.index("Risk Band") + 1
    for row_idx in range(2, s1.max_row + 1):
        fill = RISK_FILLS.get(s1.cell(row=row_idx, column=risk_band_column).value)
        if fill:
            for column_idx in range(1, len(s1_headers) + 1):
                s1.cell(row=row_idx, column=column_idx).fill = fill

    # ── Sheet 02: Scenario -> Found In Function ──
    s2 = wb.create_sheet("02_Scenario_FoundIn_Function")
    s2_headers = [
        "Service Function", "Scenario ID", "Customer Symptom", "Trigger Condition",
        "Found In Function", "Function Responsible", "Ticket Count", "Ticket IDs",
        "Mapping Role",
    ]
    s2.append(s2_headers)
    for mapping in found_in_function_mappings:
        scenario = scenario_by_id[mapping["scenario_id"]]
        s2.append([
            scenario["service_function"], mapping["scenario_id"],
            scenario["customer_symptom"], scenario["trigger_condition"],
            mapping["found_in_function"], mapping["function_responsible"],
            mapping["ticket_count"], mapping["ticket_ids"], mapping["mapping_role"],
        ])
    style_header(s2, len(s2_headers))
    autosize(s2, [30, 14, 36, 38, 52, 42, 12, 50, 22])
    add_filter_and_freeze(s2, len(s2_headers), "D2")

    # ── Sheet 03: Service Function Catalog ──
    s3 = wb.create_sheet("03_Service_Function_Catalog")
    s3_headers = [
        "Service Function", "Catalog Status", "Scenario Count", "Ticket Count",
        "Source Ticket Mentions", "Phase 09 Evidence Status", "Problem Types",
        "Diagnostic Layers", "Solution Source Policy", "Node Count", "Edge Count",
        "Tree Generation", "Source Status",
    ]
    s3.append(s3_headers)
    for tree in decision_trees:
        s3.append([
            tree["service_function"], tree["catalog_status"], tree["scenario_count"],
            tree["ticket_count"], tree["source_ticket_mention_count"],
            tree["phase09_evidence_status"], "; ".join(tree["problem_types"]),
            "; ".join(tree["diagnostic_layers"]), tree["solution_source_policy"],
            tree["node_count"], tree["edge_count"], tree["tree_generation"],
            tree["source_status"],
        ])
    style_header(s3, len(s3_headers))
    autosize(s3, [32, 26, 14, 14, 20, 48, 52, 62, 82, 14, 14, 34, 72])
    add_filter_and_freeze(s3, len(s3_headers), "C2")

    # ── Sheet 04: Data-derived Decision Tree Catalog ──
    s4 = wb.create_sheet("04_Decision_Tree_Catalog")
    s4_headers = [
        "Service Function", "Scenario Count", "Ticket Count", "Node Count", "Edge Count",
        "Source Ticket Mentions", "Phase 09 Evidence Status", "Problem Types",
        "Diagnostic Layers", "Solution Source Policy", "Tree Generation", "Source Status",
        "Visual Diagram",
    ]
    s4.append(s4_headers)
    for tree in decision_trees:
        s4.append([
            tree["service_function"], tree["scenario_count"], tree["ticket_count"],
            tree["node_count"], tree["edge_count"], tree["source_ticket_mention_count"],
            tree["phase09_evidence_status"], "; ".join(tree["problem_types"]),
            "; ".join(tree["diagnostic_layers"]), tree["solution_source_policy"],
            tree["tree_generation"], tree["source_status"], decision_tree_html.name,
        ])
        diagram_cell = s4.cell(row=s4.max_row, column=13)
        diagram_cell.hyperlink = decision_tree_html.name
        diagram_cell.style = "Hyperlink"
    style_header(s4, len(s4_headers))
    autosize(s4, [32, 14, 14, 14, 14, 20, 48, 52, 62, 82, 34, 72, 42])
    add_filter_and_freeze(s4, len(s4_headers), "C2")

    # ── Sheet 05: Data-derived Decision Tree Nodes ──
    s5 = wb.create_sheet("05_Decision_Tree_Nodes")
    s5_headers = [
        "Service Function", "Node ID", "Parent Node ID", "Node Type", "Data Key",
        "Question / Solution", "Evidence Source", "Source Scenario Count",
        "Source Ticket Count", "Source Scenario IDs", "Solution Status", "Node Source",
    ]
    s5.append(s5_headers)
    for node in decision_nodes:
        s5.append([
            service_function_by_tree_id[node["tree_id"]], node["node_id"],
            node["parent_node_id"], node["node_type"],
            node["data_key"], node["question_or_action"], node["evidence_required"],
            node["source_scenario_count"], node["source_ticket_count"],
            node["source_scenario_ids"], node.get("solution_status", ""),
            node["node_source"],
        ])
    style_header(s5, len(s5_headers))
    autosize(s5, [32, 26, 26, 24, 32, 68, 34, 18, 18, 70, 28, 32])
    add_filter_and_freeze(s5, len(s5_headers), "D2")

    # ── Sheet 06: Service Knowledge Cards ──
    s6 = wb.create_sheet("06_Service_Knowledge_Cards")
    s6_headers = [
        "Service Function", "Scenario ID", "Customer Symptom", "Trigger Condition",
        "Found In Functions", "Function Responsible", "Ticket 已记录方案",
        "方案状态", "场景提取置信度", "代表 Ticket", "Ticket 数",
    ]
    s6.append(s6_headers)
    for card in sorted(knowledge_cards, key=lambda item: -item["risk_index"]):
        scenario = scenario_by_id[card["scenario_id"]]
        found_in_functions, function_responsibles, _ = _scenario_attribution_summary(scenario)
        s6.append([
            scenario["service_function"], card["scenario_id"], card["customer_symptom"],
            card["trigger_condition"], found_in_functions, function_responsibles,
            card["recovery_action"][:200],
            "Ticket 已记录方案" if card["recovery_action"] else "",
            scenario["evidence_confidence"], card["rep_ticket_id"], card["ticket_count"],
        ])
    style_header(s6, len(s6_headers))
    autosize(s6, [30, 14, 36, 38, 48, 42, 44, 26, 18, 16, 12])
    add_filter_and_freeze(s6, len(s6_headers), "C2")

    # ── Sheet 07: Ticket Mapping ──
    s7 = wb.create_sheet("07_Ticket_Mapping")
    s7_headers = [
        "Ticket ID", "Scenario ID", "Service Function", "Customer Symptom",
        "Trigger Condition", "Found In Function", "Function Responsible",
        "Risk Tier", "Risk Score", "Closure Reason", "Ticket Title",
    ]
    s7.append(s7_headers)
    ticket_to_scenario = {
        ticket_id: scenario
        for scenario in scenarios
        for ticket_id in scenario["ticket_ids"]
    }
    for extraction in extractions:
        ticket_id = str(extraction.get("ticket_id", ""))
        scenario = ticket_to_scenario.get(ticket_id)
        metadata_row = metadata.get(ticket_id, {})
        if scenario:
            s7.append([
                ticket_id, scenario["scenario_id"], scenario["service_function"],
                scenario["customer_symptom"], scenario["trigger_condition"],
                metadata_row.get("function", ""), metadata_row.get("function_responsible", ""),
                metadata_row.get("risk_tier", ""), metadata_row.get("risk_score", ""),
                metadata_row.get("closure_reason", ""), metadata_row.get("title", "")[:100],
            ])
        else:
            s7.append([
                ticket_id, "", "", "", "", metadata_row.get("function", ""),
                metadata_row.get("function_responsible", ""), metadata_row.get("risk_tier", ""),
                metadata_row.get("risk_score", ""), metadata_row.get("closure_reason", ""),
                metadata_row.get("title", "")[:100],
            ])
    style_header(s7, len(s7_headers))
    autosize(s7, [14, 14, 30, 36, 38, 52, 42, 24, 12, 24, 54])
    add_filter_and_freeze(s7, len(s7_headers), "B2")

    # ── Sheet 08: Excluded Tickets ──
    s8 = wb.create_sheet("08_Excluded_Tickets")
    s8_headers = [
        "Ticket ID", "Exclusion Reason", "Found In Function", "Ticket Title",
        "Risk Tier", "Closure Reason",
    ]
    s8.append(s8_headers)
    for exclusion in sorted(excluded, key=lambda item: item["ticket_id"]):
        s8.append([
            exclusion["ticket_id"], exclusion["exclusion_reason"], exclusion["function"][:80],
            exclusion["title"][:100], exclusion["risk_tier"], exclusion["closure_reason"],
        ])
    style_header(s8, len(s8_headers))
    autosize(s8, [14, 24, 52, 60, 24, 28])
    add_filter_and_freeze(s8, len(s8_headers), "C2")

    # ── Sheet 09: After-sales Reference Trees ──
    s9 = wb.create_sheet("09_After_Sales_Reference_Trees")
    s9_headers = [
        "Source File", "Service Function", "Tree Version", "Source Asset",
        "Reference Pattern", "Cross-Tree Routes",
    ]
    s9.append(s9_headers)
    for reference_tree in DECISION_TREE_SOURCE_CATALOG:
        service_function = SERVICE_FUNCTION_BY_ID[reference_tree["service_function_id"]]
        s9.append([
            reference_tree["source_file"], service_function["service_function"],
            reference_tree["tree_version"], reference_tree["source_asset"],
            reference_tree["triage_pattern"], reference_tree["cross_tree_routes"],
        ])
        asset_cell = s9.cell(row=s9.max_row, column=4)
        asset_cell.hyperlink = reference_tree["source_asset"]
        asset_cell.style = "Hyperlink"
    style_header(s9, len(s9_headers))
    autosize(s9, [42, 32, 14, 58, 72, 46])
    add_filter_and_freeze(s9, len(s9_headers), "D2")

    # ── Sheet 10: Tree Growth Candidates ──
    s10 = wb.create_sheet("10_Tree_Growth_Candidates")
    s10_headers = [
        "Service Domain", "Service Function", "Problem Type ID", "Problem Type",
        "Scenario Count", "Ticket Count", "Customer Symptom Samples",
        "Trigger Condition Samples", "Found In Function Distribution",
        "Function Responsible Distribution", "Top Scenario IDs",
        "Ticket-recorded Solution Samples", "Growth Status",
    ]
    s10.append(s10_headers)
    for candidate in tree_growth_candidates:
        s10.append([
            candidate["service_domain"], candidate["service_function"],
            candidate["problem_type_id"], candidate["problem_type"],
            candidate["scenario_count"], candidate["ticket_count"],
            candidate["customer_symptom_samples"],
            candidate["trigger_condition_samples"], candidate["found_in_function_distribution"],
            candidate["function_responsible_distribution"], candidate["top_scenario_ids"],
            candidate["recorded_recovery_action_samples"], candidate["growth_status"],
        ])
    style_header(s10, len(s10_headers))
    autosize(s10, [34, 32, 28, 36, 14, 14, 72, 72, 58, 54, 70, 70, 50])
    add_filter_and_freeze(s10, len(s10_headers), "D2")

    # ── Sheet 11: Data Dictionary ──
    s11 = wb.create_sheet("11_Data_Dictionary")
    s11.append(["字段定义与治理规则"])
    s11.append(["Service Function 为决策树入口；Found In Function / Function Responsible 为研发追溯映射"])
    s11.append([])
    s11.append(["字段", "层级", "定义", "允许值 / 规则"])
    dict_rows = [
        ("Service Function", "Decision Tree", "售后决策树唯一入口", "客户问题首先归入此维度，不使用 Family 作为入口"),
        ("Scenario ID", "Scenario", "标准化客户失效场景标识", "同一症状+触发+功能上下文聚类"),
        ("Problem Type", "Decision Tree Node", "由客户症状和触发条件归纳的辅助问题类别", "用于 Issue Group 分流，不替代原始 Customer Symptom"),
        ("Issue Group", "Decision Tree Node", "客户问题的第一层分组", "由 Problem Type 聚合；低频类型保留为 Other observed issue groups"),
        ("Symptom Pattern", "Decision Tree Node", "客户可见现象的第二层分组", "只使用 Customer Symptom / Observed Behaviour 的已记录模式"),
        ("Trigger Pattern", "Decision Tree Node", "问题出现时机或操作条件的第三层分组", "只使用 Trigger Condition 的已记录模式"),
        ("Ticket-recorded Solution", "Decision Tree Node", "Ticket / Scenario 中已有的 Recovery Action 文本", "仅在记录存在时显示；它可能是临时恢复措施，不等于根因或批准的维修方案"),
        ("Solution Status", "Decision Tree Node", "方案叶子的记录状态", "ticket_recorded_solution 表示有原文；no_ticket_solution_recorded 表示方案留白"),
        ("Source Ticket Mentions", "Service Function", "源数据中命中该功能地图名称或别名的 Ticket 数", "扫描原始功能、标题和问题描述摘录；不是场景分类或分支级匹配"),
        ("Phase 09 Evidence Status", "Service Function", "功能地图条目的 Phase 09 数据状态", "field_scenario_data_available=有现场场景；source_ticket_evidence_without_field_scenario=源数据提及但未保留为现场场景；no_source_ticket_evidence=源数据未命中"),
        ("Found In Function", "R&D Attribution", "原始 Octane R&D 功能字段", "一个 Scenario 可以对应多个研发功能"),
        ("Function Responsible", "R&D Attribution", "原始 Function Responsible", "一个 Scenario 可以对应多个负责人"),
        ("Parent Node ID", "Decision Tree Node", "节点的上游问题路径节点", "Entry -> Issue Group -> Symptom Pattern -> Trigger Pattern -> Ticket-recorded Solution"),
        ("Node Source", "Decision Tree Node", "节点的生成来源", "当前均为 field_scenario_symptom_solution"),
        ("Source Scenario Count", "Decision Tree Node", "支撑该节点的 Scenario 数量", "用于判断分支覆盖度"),
        ("Source Ticket Count", "Decision Tree Node", "支撑该节点的 Ticket 数量", "用于判断分支频率"),
    ]
    for row_data in dict_rows:
        s11.append(list(row_data))
    style_header(s11, 4, row=4)
    s11.cell(row=1, column=1).font = Font(bold=True, size=14)
    autosize(s11, [32, 24, 56, 78])

    output_xlsx.parent.mkdir(parents=True, exist_ok=True)
    decision_tree_html.parent.mkdir(parents=True, exist_ok=True)
    knowledge_base_json.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_xlsx)
    decision_tree_html.write_text(
        render_symptom_solution_tree_catalog_html(decision_trees, decision_nodes),
        encoding="utf-8",
    )
    knowledge_base_json.write_text(
        json.dumps(
            build_symptom_solution_knowledge_base_payload(
                decision_trees,
                decision_nodes,
                found_in_function_mappings,
                tree_growth_candidates,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Saved Excel to: {output_xlsx}")
    print(f"Saved decision trees to: {decision_tree_html}")
    print(f"Saved decision-tree knowledge base to: {knowledge_base_json}")


def main() -> int:
    extractions = load_extractions()
    metadata = load_excel_metadata()

    scenarios, excluded = build_scenarios(extractions, metadata)
    families = build_families(scenarios)
    risk_records = build_risk_scores(scenarios, len(extractions))
    knowledge_cards = build_knowledge_cards(scenarios, families)
    build_excel(
        scenarios, families, risk_records, knowledge_cards,
        excluded, extractions, metadata,
    )

    # Summary
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Total tickets: {len(extractions)}")
    print(f"  Field scenario tickets: {sum(s['ticket_count'] for s in scenarios)}")
    print(f"  Excluded tickets: {len(excluded)}")
    print(f"  Total scenarios: {len(scenarios)}")
    print(f"  Total families: {len(families)}")
    band_dist = Counter(s["risk_band"] for s in scenarios)
    print(f"\n  Risk band distribution:")
    for band in ["Critical", "High", "Medium", "Low"]:
        print(f"    {band}: {band_dist.get(band, 0)}")
    print(f"\n  Top 10 families:")
    for f in families[:10]:
        print(f"    {f['family_id']}  {f['family_name']}: {f['scenario_count']} scenarios, {f['ticket_count']} tickets")
    print(f"\n  Sheets: {openpyxl.load_workbook(OUTPUT_XLSX, read_only=True).sheetnames}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
