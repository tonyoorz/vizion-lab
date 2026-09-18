from backend.analytics.phase09_idcevo_scenario_build import (
    DECISION_TREE_SOURCE_CATALOG,
    SERVICE_FUNCTION_CATALOG,
    build_families,
    build_evidence_gate_trees,
    build_guided_diagnostic_trees,
    build_symptom_solution_trees,
    build_workflow_template_trees,
    build_knowledge_cards,
    build_scenario_found_in_function_mappings,
    build_scenarios,
    build_service_decision_trees,
    enrich_service_function_source_evidence,
    build_service_function_tree_growth_candidates,
    canonical_function_domain,
    classify_engineering_family,
    classify_service_function,
    render_decision_tree_catalog_html,
    render_guided_diagnostic_tree_catalog_html,
    render_evidence_gate_tree_catalog_html,
    render_symptom_solution_tree_catalog_html,
    render_workflow_template_tree_catalog_html,
    _recorded_evidence_text,
)
import json


def test_decision_tree_catalog_maps_carplay_connection_scenario() -> None:
    scenarios = [
        {
            "scenario_id": "FS-0001",
            "affected_function": "CarPlay / HiCar",
            "affected_function_raw": "CarPlay",
            "rep_function": "CarPlay",
            "customer_symptom": "CarPlay cannot connect to the phone",
            "trigger_condition": "Connecting the iPhone through USB",
            "observed_behaviour": "Connection fails before projection starts",
            "ticket_count": 3,
            "risk_band": "High",
            "risk_index": 3.4,
            "recovery_action": "Reconnect the cable",
        }
    ]
    families = [
        {
            "family_id": "FAM-01",
            "family_name": "CarPlay / HiCar",
            "scenarios": scenarios,
        }
    ]

    trees, nodes, mappings = build_service_decision_trees(scenarios, families)

    carplay_tree = next(tree for tree in trees if tree["service_function_id"] == "CARPLAY")
    assert carplay_tree["tree_id"] == "DT-CARPLAY"
    assert carplay_tree["scenario_count"] == 1
    assert carplay_tree["ticket_count"] == 3
    assert carplay_tree["problem_type_ids"] == ["connection_pairing"]
    assert carplay_tree["tree_generation"] == "field_scenario_data"
    assert carplay_tree["node_count"] == 6

    assert scenarios[0]["service_function_id"] == "CARPLAY"
    assert scenarios[0]["problem_type_id"] == "connection_pairing"
    assert mappings == [
        {
            "scenario_id": "FS-0001",
            "tree_id": "DT-CARPLAY",
            "family_id": "FAM-01",
            "service_function_id": "CARPLAY",
            "service_function": "CarPlay",
            "problem_type_id": "connection_pairing",
            "problem_type": "Connection / Pairing (连接/配对)",
            "mapping_role": "Primary triage entry",
        }
    ]

    carplay_nodes = [node for node in nodes if node["tree_id"] == "DT-CARPLAY"]
    assert {node["node_type"] for node in carplay_nodes} == {
        "Entry",
        "Customer Symptom",
        "Trigger Condition",
        "Diagnostic Context",
        "Technical Evidence",
        "Resolution",
    }
    assert not {"C01", "C05", "E01"}.intersection(
        {node["node_id"] for node in carplay_nodes}
    )
    root = next(node for node in carplay_nodes if node["node_type"] == "Entry")
    symptom = next(
        node for node in carplay_nodes if node["node_type"] == "Customer Symptom"
    )
    trigger = next(
        node for node in carplay_nodes if node["node_type"] == "Trigger Condition"
    )
    diagnostic_context = next(
        node for node in carplay_nodes if node["node_type"] == "Diagnostic Context"
    )
    technical_evidence = next(
        node for node in carplay_nodes if node["node_type"] == "Technical Evidence"
    )
    resolution = next(
        node for node in carplay_nodes if node["node_type"] == "Resolution"
    )
    assert root["question_or_action"] == "CarPlay"
    assert symptom["parent_node_id"] == root["node_id"]
    assert symptom["question_or_action"] == "CarPlay cannot connect to the phone"
    assert trigger["parent_node_id"] == symptom["node_id"]
    assert trigger["question_or_action"] == "Connecting the iPhone through USB"
    assert diagnostic_context["parent_node_id"] == trigger["node_id"]
    assert diagnostic_context["question_or_action"] == "Device: Apple / iPhone; Connection: USB"
    assert technical_evidence["parent_node_id"] == diagnostic_context["node_id"]
    assert technical_evidence["question_or_action"] == "No recorded ECU, software version, or RCA root cause"
    assert resolution["parent_node_id"] == technical_evidence["node_id"]
    assert resolution["question_or_action"] == "Reconnect the cable"
    assert resolution["source_scenario_count"] == 1
    assert resolution["source_ticket_count"] == 3


def test_decision_tree_catalog_covers_every_configured_service_function() -> None:
    trees, nodes, _ = build_service_decision_trees([], [])

    assert len(trees) == len(SERVICE_FUNCTION_CATALOG)
    assert {"DT-BAT", "DT-CARPLAY", "DT-CDUNSPECIFIED", "DT-TTS"}.issubset(
        {tree["tree_id"] for tree in trees}
    )

    html = render_decision_tree_catalog_html(trees, nodes)
    assert "DT-CARPLAY" in html
    assert "CarPlay" in html
    assert "<svg" in html
    assert "carplay.webp" in html
    assert "After-sales reference trees" in html
    assert "Customer Symptom branch" in html
    assert "Trigger Condition branch" in html
    assert "parent_node_id" in html
    assert "layoutDataTree" in html
    assert "defaultSymptomNode" in html
    assert "defaultTriggerNode" in html
    assert "C01:" not in html
    assert "Data-derived decision tree" in html


def test_service_function_is_distinct_from_engineering_family() -> None:
    third_party_gaming = {
        "rep_function": "Provide 3rd party gaming enablement [01.04.01.02.03]",
        "affected_function": "Provide 3rd party gaming enablement",
        "rep_title": "Migu game user agreement cannot be confirmed",
    }
    carplay_audio = {
        "rep_function": "Audio Infrastructure",
        "affected_function": "CarPlay / HiCar",
        "rep_title": "CarPlay music has no sound after a phone call",
    }
    ad_apps_navigation = {
        "rep_function": "AD_Apps",
        "affected_function": "Navigation",
        "rep_title": "Navigation CCP drifts away from the route",
    }
    accident_call = {
        "rep_function": "Onl/xCall",
        "affected_function": "Accident Call / Online Services",
        "rep_title": "BMW accident assistance is unavailable",
    }
    remote_service = {
        "rep_function": "Remote_in_Car",
        "affected_function": "Remote Services",
        "rep_title": "Remote service fails while vehicle is asleep",
    }

    assert classify_engineering_family(third_party_gaming)["engineering_family_id"] == "ENTERTAINMENT_APPS"
    assert classify_service_function(third_party_gaming)["service_function_id"] == "MIGU_GAMING"

    assert classify_engineering_family(carplay_audio)["engineering_family_id"] == "AUDIO_MEDIA_PLATFORM"
    assert classify_service_function(carplay_audio)["service_function_id"] == "CARPLAY"

    assert classify_engineering_family(ad_apps_navigation)["engineering_family_id"] == "DRIVER_ASSISTANCE"
    navigation_service = classify_service_function(ad_apps_navigation)
    assert navigation_service["service_function_id"] == "NAVIGATION"
    assert "DTREF-NAVIGATION" in navigation_service["reference_tree_ids"]

    assert classify_engineering_family(accident_call)["engineering_family_id"] == "ASSISTANCE_EMERGENCY"
    assert classify_service_function(accident_call)["service_function_id"] == "ACALL"

    assert classify_engineering_family(remote_service)["engineering_family_id"] == "REMOTE_MOBILE"
    assert classify_service_function(remote_service)["service_function_id"] == "REMOTE_SERVICES"


def test_idcevo_new_features_have_specific_service_function_entries() -> None:
    my_modes = {
        "rep_function": "Experiences and Modes",
        "affected_function": "My Modes",
        "rep_title": "My Modes setting does not take effect",
    }
    phud = {
        "rep_function": "Instrument Cluster",
        "affected_function": "PHUD Experience",
        "rep_title": "PHUD content is not displayed",
    }
    wireless_charging = {
        "rep_function": "Charge CE Device Wireless [01.04.01.03.01.03]",
        "affected_function": "Wireless Charging",
        "rep_title": "Wireless charging setting cannot be saved",
    }
    my_bmw_app = {
        "rep_function": "Remote_in_Car",
        "affected_function": "My BMW App",
        "rep_title": "My BMW App cannot refresh vehicle status",
    }
    e_route = {
        "rep_function": "eRoute [01.04.03.01.07]",
        "affected_function": "Navigation E-Route",
        "rep_title": "E-Route guidance cannot start",
    }
    carplay_with_my_modes = {
        "rep_function": "CarPlay [01.04.01.07.02]",
        "affected_function": "CarPlay / HiCar",
        "rep_title": "CarPlay cannot connect after My Modes changes",
    }

    assert classify_service_function(my_modes)["service_function_id"] == "MY_MODES"
    assert classify_service_function(phud)["service_function_id"] == "PHUD"
    assert classify_service_function(wireless_charging)["service_function_id"] == (
        "WIRELESS_CHARGING"
    )
    assert classify_service_function(my_bmw_app)["service_function_id"] == "MYBMW_APP"
    assert classify_service_function(e_route)["service_function_id"] == "E_ROUTE"
    assert classify_service_function(carplay_with_my_modes)["service_function_id"] == "CARPLAY"

    service_functions_by_id = {
        item["service_function_id"]: item for item in SERVICE_FUNCTION_CATALOG
    }
    assert {
        "MY_MODES",
        "MY_MOMENTS",
        "PHUD",
        "WIRELESS_CHARGING",
        "KARAOKE",
        "DOLBY_ATMOS",
        "E_ROUTE",
        "AI_TRAVEL_COMPANION",
        "WELCOME_ANIMATION",
        "MYBMW_APP",
        "REMOTE_3D_VIEW",
        "SMART_ACCESS",
        "DLNA_CASTING",
    }.issubset(service_functions_by_id)

    trees, nodes, _ = build_symptom_solution_trees([], [])
    html = render_symptom_solution_tree_catalog_html(trees, nodes)

    assert 'id="catalog-status"' in html
    assert 'document.getElementById("catalog-status").textContent = tree.catalog_status;' in html
    assert "My Modes - 功能地图功能" in html
    assert "idcevo_function_map_feature" in html
    assert "grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));" in html


def test_function_map_features_are_independent_service_functions() -> None:
    feature_cases = {
        "Navigation Amap": "NAVIGATION_AMAP",
        "AI Empowered Travel Companion": "AI_TRAVEL_COMPANION",
        "Pothole Warning": "POTHOLE_WARNING",
        "Hazard Preview": "HAZARD_PREVIEW",
        "Map Seamless": "MAP_SEAMLESS",
        "Premium Parking Experience": "PREMIUM_PARKING",
        "AI Empowered IPA": "AI_EMPOWERED_IPA",
        "Front Seat Just Talk": "IPA_DIRECT_COMMANDS",
        "Speech for Passenger Entertainment": "IPA_PASSENGER_ENTERTAINMENT",
        "Extended IPA Car Control": "IPA_CAR_CONTROL",
        "Camera Recognition for Forgotten Phone Reminder": "FORGOTTEN_PHONE_REMINDER",
        "QQ Music": "QQ_MUSIC",
        "Ximalaya": "XIMALAYA",
        "NetEase Cloud Music": "NETEASE_CLOUD_MUSIC",
        "Soda Music": "SODA_MUSIC",
        "Kuwo Music": "KUWO_MUSIC",
        "Sing-along Lyrics": "SING_ALONG_LYRICS",
        "WeSing": "WESING",
        "Immersive Music": "IMMERSIVE_MUSIC",
        "iQIYI": "IQIYI",
        "Tencent Video": "TENCENT_VIDEO",
        "Bilibili": "BILIBILI",
        "Migu Gaming": "MIGU_GAMING",
        "Huawei HiCar": "HICAR",
        "China DLNA Casting": "DLNA_CASTING",
        "Connect Hotspot via My BMW App Scan": "MYBMW_HOTSPOT_SCAN",
        "PHUD Experience": "PHUD",
        "Five-finger Pinch/Spread to All Apps Menu": "ALL_APPS_GESTURE",
        "Show Room Experience": "SHOWROOM_EXPERIENCE",
        "ADAS Experience with Momenta Integration": "MOMENTA_ADAS",
        "My Moments": "MY_MOMENTS",
        "ETC China": "ETC_CHINA",
        "Real Time Monitoring": "REAL_TIME_MONITORING",
        "MNO Connectivity China": "MNO_CONNECTIVITY_CHINA",
        "Unicom": "UNICOM_CONNECTIVITY",
    }

    catalog_by_id = {
        item["service_function_id"]: item for item in SERVICE_FUNCTION_CATALOG
    }
    assert set(feature_cases.values()).issubset(catalog_by_id)
    assert all(
        catalog_by_id[service_function_id]["catalog_status"]
        == "idcevo_function_map_feature"
        for service_function_id in feature_cases.values()
        if service_function_id != "HICAR"
    )
    for feature_name, service_function_id in feature_cases.items():
        scenario = {
            "rep_function": "",
            "affected_function": feature_name,
            "rep_title": f"{feature_name} function fails",
        }
        assert classify_service_function(scenario)["service_function_id"] == (
            service_function_id
        )

    amap_seamless = {
        "rep_function": "Navigation CN",
        "affected_function": "Navigation",
        "rep_title": "Amap Seamless login keeps loading",
    }
    standalone_amap = {
        "rep_function": "Navigation CN",
        "affected_function": "Navigation Amap",
        "rep_title": "Amap route search fails",
    }
    assert classify_service_function(amap_seamless)["service_function_id"] == (
        "MAP_SEAMLESS"
    )
    assert classify_service_function(standalone_amap)["service_function_id"] == (
        "NAVIGATION_AMAP"
    )


def test_function_map_catalog_distinguishes_source_mentions_from_field_scenarios() -> None:
    scenarios = [
        {
            "scenario_id": "FS-0001",
            "rep_function": "Experiences and Modes",
            "affected_function": "My Modes",
            "rep_title": "My Modes cannot be opened",
            "customer_symptom": "My Modes cannot be opened",
            "trigger_condition": "Opening My Modes",
            "ticket_count": 2,
        }
    ]
    metadata = {
        "T-1": {
            "function": "Navigation CN",
            "title": "Map Seamless update",
            "description_excerpt": "Map Seamless is not available",
        },
        "T-2": {
            "function": "Experiences and Modes",
            "title": "My Modes cannot be opened",
            "description_excerpt": "",
        },
    }
    trees, _, _ = build_symptom_solution_trees(scenarios, [])

    enrich_service_function_source_evidence(trees, metadata)

    trees_by_id = {tree["service_function_id"]: tree for tree in trees}
    assert trees_by_id["MAP_SEAMLESS"]["source_ticket_mention_count"] == 1
    assert trees_by_id["MAP_SEAMLESS"]["phase09_evidence_status"] == (
        "source_ticket_evidence_without_field_scenario"
    )
    assert trees_by_id["MY_MODES"]["source_ticket_mention_count"] == 1
    assert trees_by_id["MY_MODES"]["phase09_evidence_status"] == (
        "field_scenario_data_available"
    )
    assert trees_by_id["POTHOLE_WARNING"]["source_ticket_mention_count"] == 0
    assert trees_by_id["POTHOLE_WARNING"]["phase09_evidence_status"] == (
        "no_source_ticket_evidence"
    )


def test_carplay_and_hicar_have_separate_clusters_and_service_trees() -> None:
    carplay = {
        "rep_function": "CarPlay [01.04.01.07.02]",
        "affected_function": "CarPlay / HiCar",
        "rep_title": "Apple CarPlay cannot connect",
        "customer_symptom": "Phone projection cannot connect",
        "trigger_condition": "After connecting the phone",
    }
    hicar = {
        "rep_function": "CN_HUAWEI_HiCar",
        "affected_function": "CarPlay / HiCar",
        "rep_title": "Huawei HiCar cannot connect",
        "customer_symptom": "Phone projection cannot connect",
        "trigger_condition": "After connecting the phone",
    }
    unspecified = {
        "rep_function": "SDI",
        "affected_function": "CarPlay / HiCar",
        "rep_title": "Phone projection connection fails",
        "customer_symptom": "Phone projection cannot connect",
        "trigger_condition": "After connecting the phone",
    }

    assert canonical_function_domain(
        carplay["rep_function"], carplay["affected_function"]
    ) == "CarPlay"
    assert canonical_function_domain(
        hicar["rep_function"], hicar["affected_function"]
    ) == "HiCar"
    assert canonical_function_domain(
        unspecified["rep_function"], unspecified["affected_function"]
    ) == "Phone Projection / Unspecified"
    assert classify_service_function(carplay)["service_function_id"] == "CARPLAY"
    assert classify_service_function(hicar)["service_function_id"] == "HICAR"
    assert classify_service_function(unspecified)["service_function_id"] == "CDUNSPECIFIED"

    extractions = [
        {
            "ticket_id": "D-1",
            "extraction": {
                "is_field_scenario": True,
                "customer_symptom": carplay["customer_symptom"],
                "trigger_condition": carplay["trigger_condition"],
                "affected_function": carplay["affected_function"],
            },
        },
        {
            "ticket_id": "D-2",
            "extraction": {
                "is_field_scenario": True,
                "customer_symptom": hicar["customer_symptom"],
                "trigger_condition": hicar["trigger_condition"],
                "affected_function": hicar["affected_function"],
            },
        },
    ]
    metadata = {
        "D-1": {
            "function": carplay["rep_function"],
            "risk_score": "5",
            "title": carplay["rep_title"],
        },
        "D-2": {
            "function": hicar["rep_function"],
            "risk_score": "4",
            "title": hicar["rep_title"],
        },
    }
    scenarios, _ = build_scenarios(extractions, metadata)
    trees, _, _ = build_service_decision_trees(scenarios, [])

    assert len(scenarios) == 2
    assert {scenario["affected_function"] for scenario in scenarios} == {
        "CarPlay",
        "HiCar",
    }
    assert next(tree for tree in trees if tree["tree_id"] == "DT-CARPLAY")[
        "scenario_count"
    ] == 1
    assert next(tree for tree in trees if tree["tree_id"] == "DT-HICAR")[
        "scenario_count"
    ] == 1


def test_after_sales_reference_tree_catalog_covers_all_supplied_pngs() -> None:
    assert len(DECISION_TREE_SOURCE_CATALOG) == 39
    assert {
        ("DTREF-CARPLAY", "carplay.png", "CARPLAY"),
        ("DTREF-ADAS", "ConnectedDrive - ADAS.png", "DRIVER_ASSISTANCE"),
        ("DTREF-REMOTE_SERVICE", "Remote Service.png", "REMOTE_SERVICES"),
        ("DTREF-FOD", "FOD - Decision Tree V1.png", "ENTERTAINMENT"),
        ("DTREF-FOD", "FOD - Decision Tree V2.png", "ENTERTAINMENT"),
    }.issubset(
        {
            (item["reference_tree_id"], item["source_file"], item["service_function_id"])
            for item in DECISION_TREE_SOURCE_CATALOG
        }
    )
    assert {
        "after_sales_reference",
        "rd_derived_candidate",
    }.issubset({item["catalog_status"] for item in SERVICE_FUNCTION_CATALOG})


def test_scenarios_keep_affected_function_detail_but_family_by_engineering_ownership() -> None:
    extractions = [
        {
            "ticket_id": "D-1",
            "extraction": {
                "is_field_scenario": True,
                "customer_symptom": "CarPlay music has no sound",
                "trigger_condition": "After ending a phone call",
                "affected_function": "CarPlay / HiCar",
                "observed_behaviour": "No audio output",
                "expected_behaviour": "CarPlay audio continues",
                "recovery_action": "Reconnect the phone",
                "closure_decision": "Fix planned",
                "evidence_confidence": "high",
            },
        },
        {
            "ticket_id": "D-2",
            "extraction": {
                "is_field_scenario": True,
                "customer_symptom": "Bluetooth music has no sound",
                "trigger_condition": "After pairing a phone",
                "affected_function": "Bluetooth Audio",
                "observed_behaviour": "No audio output",
                "expected_behaviour": "Bluetooth audio continues",
                "recovery_action": "Reconnect the phone",
                "closure_decision": "Fix planned",
                "evidence_confidence": "high",
            },
        },
    ]
    metadata = {
        "D-1": {"function": "Audio Infrastructure", "risk_score": "5", "title": "CarPlay music has no sound"},
        "D-2": {"function": "Audio Infrastructure", "risk_score": "4", "title": "Bluetooth music has no sound"},
    }

    scenarios, _ = build_scenarios(extractions, metadata)
    families = build_families(scenarios)

    assert {scenario["affected_function"] for scenario in scenarios} == {
        "Phone Projection / Unspecified",
        "Bluetooth Audio",
    }
    assert {scenario["engineering_family_id"] for scenario in scenarios} == {
        "AUDIO_MEDIA_PLATFORM"
    }
    assert len(families) == 1
    assert families[0]["family_id"] == "FAM-AUDIO_MEDIA_PLATFORM"
    assert families[0]["family_name"] == "Audio / Media Platform"


def test_scenario_found_in_function_mapping_preserves_every_cluster_member() -> None:
    extractions = [
        {
            "ticket_id": "D-1",
            "extraction": {
                "is_field_scenario": True,
                "customer_symptom": "CarPlay cannot connect",
                "trigger_condition": "After connecting the phone",
                "affected_function": "CarPlay / HiCar",
            },
        },
        {
            "ticket_id": "D-2",
            "extraction": {
                "is_field_scenario": True,
                "customer_symptom": "CarPlay cannot connect",
                "trigger_condition": "After connecting the phone",
                "affected_function": "CarPlay / HiCar",
            },
        },
    ]
    metadata = {
        "D-1": {
            "function": "Audio Infrastructure",
            "function_responsible": "Audio Owner",
            "risk_score": "5",
            "title": "CarPlay connection fails",
        },
        "D-2": {
            "function": "Wireless Services",
            "function_responsible": "Connectivity Owner",
            "risk_score": "4",
            "title": "CarPlay connection fails again",
        },
    }

    scenarios, _ = build_scenarios(extractions, metadata)

    assert len(scenarios) == 1
    assert build_scenario_found_in_function_mappings(scenarios) == [
        {
            "scenario_id": "FS-0001",
            "found_in_function": "Audio Infrastructure",
            "function_responsible": "Audio Owner",
            "ticket_count": 1,
            "ticket_ids": "D-1",
            "mapping_role": "R&D attribution",
        },
        {
            "scenario_id": "FS-0001",
            "found_in_function": "Wireless Services",
            "function_responsible": "Connectivity Owner",
            "ticket_count": 1,
            "ticket_ids": "D-2",
            "mapping_role": "R&D attribution",
        },
    ]


def test_decision_tree_skeleton_leaves_recovery_and_escalation_blank_without_evidence() -> None:
    scenarios = [
        {
            "scenario_id": "FS-0001",
            "affected_function": "CarPlay / HiCar",
            "affected_function_raw": "CarPlay",
            "rep_function": "CarPlay",
            "customer_symptom": "CarPlay cannot connect",
            "trigger_condition": "After connecting the phone",
            "observed_behaviour": "Connection does not start",
            "recovery_action": "无恢复措施",
            "rca_root_cause": "",
            "rep_ecu": "",
            "evidence_confidence": "medium",
            "risk_band": "Critical",
            "risk_index": 4.1,
            "prod_exposure_count": 2,
            "ticket_count": 1,
            "rep_ticket_id": "D-1",
            "top_sw_versions": [],
        }
    ]
    families = [
        {
            "family_id": "FAM-01",
            "family_name": "Phone Projection",
            "scenarios": scenarios,
        }
    ]

    _, nodes, _ = build_service_decision_trees(scenarios, families)
    carplay_nodes = {
        node["node_id"]: node
        for node in nodes
        if node["tree_id"] == "DT-CARPLAY"
    }
    cards = build_knowledge_cards(scenarios, families)

    resolution = next(
        node for node in carplay_nodes.values() if node["node_type"] == "Resolution"
    )
    assert resolution["question_or_action"] == ""
    assert resolution["evidence_required"] == ""
    assert resolution["source_scenario_count"] == 1
    assert cards[0]["diagnostic_entry"] == ""
    assert cards[0]["recovery_action"] == ""
    assert cards[0]["escalation_criteria"] == ""


def test_recovery_evidence_filter_excludes_explicitly_unrecoverable_text() -> None:
    assert _recorded_evidence_text("无恢复措施") == ""
    assert _recorded_evidence_text("无法恢复") == ""
    assert _recorded_evidence_text("无操作") == ""
    assert _recorded_evidence_text("Reconnect the cable") == "Reconnect the cable"


def test_decision_tree_uses_recorded_technical_values_without_inference() -> None:
    scenario = {
        "scenario_id": "FS-0001",
        "affected_function": "CarPlay",
        "affected_function_raw": "CarPlay",
        "rep_function": "CarPlay",
        "customer_symptom": "CarPlay music has no sound",
        "trigger_condition": "After connecting the iPhone through USB",
        "observed_behaviour": "No audio output",
        "top_ecus": [("HU-H", 1)],
        "top_sw_versions": [("24-03", 1)],
        "rca_root_cause": "Audio routing state not restored",
        "recovery_action": "Reconnect the cable",
        "ticket_count": 2,
    }

    _, nodes, _ = build_service_decision_trees([scenario], [])
    technical_evidence = next(
        node
        for node in nodes
        if node["tree_id"] == "DT-CARPLAY"
        and node["node_type"] == "Technical Evidence"
    )

    assert technical_evidence["question_or_action"] == (
        "ECU: HU-H; Software Version: 24-03; "
        "Recorded RCA Root Cause: Audio routing state not restored"
    )
    assert technical_evidence["evidence_required"] == (
        "Assigned ECU; Software Version; RCA Root Cause"
    )


def test_guided_diagnostic_tree_aggregates_one_deep_path_per_issue_group() -> None:
    scenarios = [
        {
            "scenario_id": "FS-0001",
            "rep_function": "CarPlay",
            "affected_function": "CarPlay",
            "affected_function_raw": "CarPlay",
            "customer_symptom": "Apple CarPlay cannot connect",
            "trigger_condition": "Connecting the iPhone through USB",
            "observed_behaviour": "Connection does not start",
            "top_ecus": [("HU-H", 3)],
            "top_sw_versions": [("24-03", 3)],
            "rca_root_cause": "Pairing state was not restored",
            "recovery_action": "Reconnect the cable",
            "ticket_count": 3,
        },
        {
            "scenario_id": "FS-0002",
            "rep_function": "CarPlay",
            "affected_function": "CarPlay",
            "affected_function_raw": "CarPlay",
            "customer_symptom": "Projection connection fails",
            "trigger_condition": "Connecting an iPhone through USB",
            "observed_behaviour": "Connection does not start",
            "top_ecus": [("HU-H", 2)],
            "top_sw_versions": [("24-03", 2)],
            "rca_root_cause": "Pairing state was not restored",
            "recovery_action": "无恢复措施",
            "ticket_count": 2,
        },
    ]

    trees, nodes, _ = build_guided_diagnostic_trees(scenarios, [])

    tree = next(tree for tree in trees if tree["service_function_id"] == "CARPLAY")
    assert tree["tree_generation"] == "field_scenario_guided_triage_v1"
    assert tree["guided_branch_count"] == 1
    assert tree["scenario_count"] == 2
    assert tree["ticket_count"] == 5

    tree_nodes = [node for node in nodes if node["tree_id"] == "DT-CARPLAY"]
    assert [node["node_type"] for node in tree_nodes] == [
        "Entry",
        "Issue Group",
        "Symptom Pattern",
        "Trigger Pattern",
        "Diagnostic Context",
        "Technical Evidence",
        "Resolution",
        "Resolution",
    ]
    issue_group = tree_nodes[1]
    symptom_pattern = tree_nodes[2]
    trigger_pattern = tree_nodes[3]
    context = tree_nodes[4]
    technical = tree_nodes[5]
    observed_recovery = tree_nodes[6]
    missing_recovery = tree_nodes[7]

    assert issue_group["question_or_action"] == "Connection / Pairing (连接/配对)"
    assert issue_group["source_ticket_count"] == 5
    assert symptom_pattern["parent_node_id"] == issue_group["node_id"]
    assert "Cannot connect" in symptom_pattern["question_or_action"]
    assert trigger_pattern["parent_node_id"] == symptom_pattern["node_id"]
    assert "During connection" in trigger_pattern["question_or_action"]
    assert context["parent_node_id"] == trigger_pattern["node_id"]
    assert "Device: Apple / iPhone" in context["question_or_action"]
    assert "Connection: USB" in context["question_or_action"]
    assert technical["parent_node_id"] == context["node_id"]
    assert "ECU evidence: 5/5 tickets" in technical["question_or_action"]
    assert "Software version evidence: 5/5 tickets" in technical["question_or_action"]
    assert "Recorded RCA text: 5/5 tickets" in technical["question_or_action"]
    assert observed_recovery["parent_node_id"] == technical["node_id"]
    assert observed_recovery["question_or_action"] == "Reconnect the cable (3 tickets)"
    assert missing_recovery["question_or_action"] == ""
    assert missing_recovery["source_ticket_count"] == 2
    assert all(node["node_source"] == "field_scenario_guided_triage" for node in tree_nodes)


def test_symptom_solution_tree_uses_problem_path_and_leaves_ticket_solution_blank() -> None:
    scenarios = [
        {
            "scenario_id": "FS-0001",
            "rep_function": "CarPlay",
            "affected_function": "CarPlay",
            "affected_function_raw": "CarPlay",
            "customer_symptom": "Apple CarPlay cannot connect",
            "trigger_condition": "Connecting the iPhone through USB",
            "observed_behaviour": "Connection does not start",
            "top_ecus": [("HU-H", 3)],
            "top_sw_versions": [("24-03", 3)],
            "rca_root_cause": "Pairing state was not restored",
            "recovery_action": "Reconnect the cable",
            "ticket_count": 3,
        },
        {
            "scenario_id": "FS-0002",
            "rep_function": "CarPlay",
            "affected_function": "CarPlay",
            "affected_function_raw": "CarPlay",
            "customer_symptom": "Projection connection fails",
            "trigger_condition": "Connecting an iPhone through USB",
            "observed_behaviour": "Connection does not start",
            "top_ecus": [("HU-H", 2)],
            "top_sw_versions": [("24-03", 2)],
            "rca_root_cause": "Pairing state was not restored",
            "recovery_action": "",
            "ticket_count": 2,
        },
    ]

    trees, nodes, _ = build_symptom_solution_trees(scenarios, [])

    tree = next(tree for tree in trees if tree["service_function_id"] == "CARPLAY")
    tree_nodes = [node for node in nodes if node["tree_id"] == "DT-CARPLAY"]
    ticket_solutions = [
        node for node in tree_nodes if node["node_type"] == "Ticket Solution"
    ]

    assert tree["tree_generation"] == "field_scenario_symptom_solution_v1"
    assert [node["node_type"] for node in tree_nodes] == [
        "Entry",
        "Issue Group",
        "Symptom Pattern",
        "Trigger Pattern",
        "Ticket Solution",
        "Ticket Solution",
    ]
    assert tree_nodes[1]["question_or_action"] == "Connection / Pairing (连接/配对)"
    assert "Cannot connect" in tree_nodes[2]["question_or_action"]
    assert "During connection" in tree_nodes[3]["question_or_action"]
    assert all(
        node["node_type"]
        not in {
            "Precondition Gate",
            "Reproduction Gate",
            "Technical Evidence",
            "Technical Evidence Gate",
            "Recorded Outcome Gate",
        }
        for node in tree_nodes
    )
    assert [node["question_or_action"] for node in ticket_solutions] == [
        "Reconnect the cable (3 tickets)",
        "",
    ]
    assert ticket_solutions[0]["evidence_required"] == "Ticket / Scenario Recovery Action"
    assert ticket_solutions[1]["evidence_required"] == ""
    assert all(
        "ECU" not in node["question_or_action"]
        and "RCA" not in node["question_or_action"]
        for node in tree_nodes
    )


def test_guided_diagnostic_renderer_shows_one_large_service_function_tree() -> None:
    scenarios = [
        {
            "scenario_id": "FS-0001",
            "rep_function": "CarPlay",
            "affected_function": "CarPlay",
            "affected_function_raw": "CarPlay",
            "customer_symptom": "Apple CarPlay cannot connect",
            "trigger_condition": "Connecting the iPhone through USB",
            "top_ecus": [("HU-H", 2)],
            "top_sw_versions": [("24-03", 2)],
            "rca_root_cause": "Pairing state was not restored",
            "recovery_action": "Reconnect the cable",
            "ticket_count": 2,
        },
        {
            "scenario_id": "FS-0002",
            "rep_function": "CarPlay",
            "affected_function": "CarPlay",
            "affected_function_raw": "CarPlay",
            "customer_symptom": "Apple CarPlay audio has no sound",
            "trigger_condition": "During media playback",
            "top_ecus": [("HU-H", 1)],
            "top_sw_versions": [("24-03", 1)],
            "rca_root_cause": "",
            "recovery_action": "",
            "ticket_count": 1,
        },
    ]
    trees, nodes, _ = build_guided_diagnostic_trees(scenarios, [])

    html = render_guided_diagnostic_tree_catalog_html(trees, nodes)

    assert "Guided Diagnostic Tree" in html
    assert "Issue Group" in html
    assert "Observed symptom patterns" in html
    assert "Technical Evidence" in html
    assert "layoutGuidedTree" in html
    assert "displayNodeText" in html
    assert "${node.node_id} | ${node.node_type}" not in html
    assert "Customer Symptom branch" not in html
    assert "Trigger Condition branch" not in html
    assert "DT-CARPLAY" in html


def test_workflow_template_tree_uses_data_to_fill_evidence_slots_not_decisions() -> None:
    scenarios = [
        {
            "scenario_id": "FS-0001",
            "rep_function": "CarPlay",
            "affected_function": "CarPlay",
            "affected_function_raw": "CarPlay",
            "customer_symptom": "Apple CarPlay cannot connect",
            "trigger_condition": "Connecting the iPhone through USB",
            "observed_behaviour": "Connection does not start",
            "top_ecus": [("HU-H", 3)],
            "top_sw_versions": [("24-03", 3)],
            "rca_root_cause": "Pairing state was not restored",
            "recovery_action": "Reconnect the cable",
            "ticket_count": 3,
        },
        {
            "scenario_id": "FS-0002",
            "rep_function": "CarPlay",
            "affected_function": "CarPlay",
            "affected_function_raw": "CarPlay",
            "customer_symptom": "Apple CarPlay connection fails",
            "trigger_condition": "After vehicle wake-up",
            "observed_behaviour": "Connection does not start",
            "top_ecus": [],
            "top_sw_versions": [],
            "rca_root_cause": "",
            "recovery_action": "",
            "ticket_count": 2,
        },
    ]

    trees, nodes, _ = build_workflow_template_trees(scenarios, [])
    tree = next(tree for tree in trees if tree["service_function_id"] == "CARPLAY")
    tree_nodes = [node for node in nodes if node["tree_id"] == "DT-CARPLAY"]

    assert tree["tree_generation"] == "field_scenario_workflow_template_v1"
    assert tree["workflow_template"] == "after_sales_evidence_workflow_v1"
    assert [node["node_type"] for node in tree_nodes] == [
        "Entry",
        "Customer Complaint",
        "Precondition Evidence",
        "Reproduction Context",
        "Technical Evidence",
        "Recorded Outcome",
        "Escalation Package",
    ]
    assert tree_nodes[1]["question_or_action"] == "Connection / Pairing (连接/配对)"
    assert tree_nodes[2]["question_or_action"] == "Precondition evidence: not recorded"
    assert "Observed reproduction context:" in tree_nodes[3]["question_or_action"]
    assert "Device: Apple / iPhone" in tree_nodes[3]["question_or_action"]
    assert "Technical evidence coverage:" in tree_nodes[4]["question_or_action"]
    assert "ECU: 3/5 tickets" in tree_nodes[4]["question_or_action"]
    assert "Recorded recovery outcome: Reconnect the cable (3 tickets)" == tree_nodes[5]["question_or_action"]
    assert tree_nodes[6]["question_or_action"] == "Escalation evidence package: 2/5 tickets"
    assert all(node["decision_status"] == "evidence_collection_only" for node in tree_nodes)
    assert all(node["node_source"] == "field_scenario_workflow_template" for node in tree_nodes)


def test_workflow_template_renderer_has_no_raw_symptom_selector() -> None:
    scenarios = [
        {
            "scenario_id": "FS-0001",
            "rep_function": "CarPlay",
            "affected_function": "CarPlay",
            "affected_function_raw": "CarPlay",
            "customer_symptom": "Apple CarPlay cannot connect",
            "trigger_condition": "Connecting the iPhone through USB",
            "ticket_count": 1,
        }
    ]
    trees, nodes, _ = build_workflow_template_trees(scenarios, [])

    html = render_workflow_template_tree_catalog_html(trees, nodes)

    assert "After-Sales Evidence Workflow" in html
    assert "Precondition Evidence" in html
    assert "Escalation Package" in html
    assert "layoutWorkflowTree" in html
    assert "Customer Symptom branch" not in html
    assert "Trigger Condition branch" not in html


def test_workflow_nodes_keep_full_evidence_but_expose_short_troubleshooting_prompts() -> None:
    scenarios = [
        {
            "scenario_id": "FS-0001",
            "rep_function": "CarPlay",
            "affected_function": "CarPlay",
            "affected_function_raw": "CarPlay",
            "customer_symptom": "Apple CarPlay cannot connect",
            "trigger_condition": "Activation fails while connecting the iPhone through USB",
            "observed_behaviour": "Connection does not start",
            "top_ecus": [("HU-H", 3)],
            "top_sw_versions": [("24-03", 3)],
            "rca_root_cause": "Pairing state was not restored",
            "recovery_action": "Reconnect the cable",
            "ticket_count": 3,
        },
        {
            "scenario_id": "FS-0002",
            "rep_function": "CarPlay",
            "affected_function": "CarPlay",
            "affected_function_raw": "CarPlay",
            "customer_symptom": "Apple CarPlay connection fails",
            "trigger_condition": "Activation fails after vehicle wake-up",
            "observed_behaviour": "Connection does not start",
            "top_ecus": [],
            "top_sw_versions": [],
            "rca_root_cause": "",
            "recovery_action": "",
            "ticket_count": 2,
        },
    ]
    trees, nodes, _ = build_workflow_template_trees(scenarios, [])
    tree = next(tree for tree in trees if tree["service_function_id"] == "CARPLAY")
    tree_nodes = [node for node in nodes if node["tree_id"] == tree["tree_id"]]

    complaint = next(
        node for node in tree_nodes if node["node_type"] == "Customer Complaint"
    )
    precondition = next(
        node for node in tree_nodes if node["node_type"] == "Precondition Evidence"
    )
    context = next(
        node for node in tree_nodes if node["node_type"] == "Reproduction Context"
    )
    technical = next(
        node for node in tree_nodes if node["node_type"] == "Technical Evidence"
    )
    outcome = next(
        node for node in tree_nodes if node["node_type"] == "Recorded Outcome"
    )
    escalation = next(
        node for node in tree_nodes if node["node_type"] == "Escalation Package"
    )

    assert complaint["visual_prompt"] == "Customer complaint?"
    assert complaint["visual_detail"] == "Connection / Pairing (连接/配对)"
    assert precondition["visual_prompt"] == "Check prerequisites"
    assert precondition["visual_detail"] == "Activation"
    assert len(precondition["visual_detail"]) <= 20
    assert precondition["visual_evidence"] == "5 tickets"
    assert context["visual_prompt"] == "Reproduce reported context"
    assert "iPhone" in context["visual_detail"]
    assert "USB" in context["visual_detail"]
    assert technical["visual_prompt"] == "Collect technical evidence"
    assert technical["visual_detail"] == "ECU / software / RCA"
    assert technical["visual_evidence"] == "5 tickets"
    assert outcome["visual_prompt"] == "Review recorded recovery"
    assert outcome["visual_detail"] == "Reconnect the cable"
    assert outcome["visual_evidence"] == "3 tickets"
    assert escalation["visual_prompt"] == "Prepare evidence package"
    assert escalation["visual_detail"] == "No recorded recovery"
    assert escalation["visual_evidence"] == "2 tickets"
    assert "Technical evidence coverage:" in technical["question_or_action"]

    html = render_workflow_template_tree_catalog_html(trees, nodes)
    assert "displayWorkflowNodeText" in html
    assert "visual_prompt" in html
    assert "visual_evidence" in html
    assert "stage.textContent = node.visual_prompt || node.node_type;" in html
    assert "function displayWorkflowNodeText(node) { return node.visual_detail || \"\"; }" in html


def test_evidence_gate_tree_uses_data_supported_yes_no_routes() -> None:
    scenarios = [
        {
            "scenario_id": "FS-0001",
            "rep_function": "CarPlay",
            "affected_function": "CarPlay",
            "affected_function_raw": "CarPlay",
            "customer_symptom": "Apple CarPlay cannot connect",
            "trigger_condition": "Activation fails while connecting the iPhone through USB",
            "observed_behaviour": "Connection does not start",
            "top_ecus": [("HU-H", 3)],
            "top_sw_versions": [("24-03", 3)],
            "rca_root_cause": "Pairing state was not restored",
            "recovery_action": "Reconnect the cable",
            "ticket_count": 3,
        },
        {
            "scenario_id": "FS-0002",
            "rep_function": "CarPlay",
            "affected_function": "CarPlay",
            "affected_function_raw": "CarPlay",
            "customer_symptom": "Apple CarPlay connection fails",
            "trigger_condition": "Connection attempt fails",
            "observed_behaviour": "Connection does not start",
            "top_ecus": [],
            "top_sw_versions": [],
            "rca_root_cause": "",
            "recovery_action": "",
            "ticket_count": 2,
        },
    ]

    trees, nodes, _ = build_evidence_gate_trees(scenarios, [])

    tree = next(tree for tree in trees if tree["service_function_id"] == "CARPLAY")
    assert tree["tree_generation"] == "field_scenario_evidence_gate_v1"
    assert tree["gate_template"] == "after_sales_evidence_gate_template_v1"
    assert tree["scenario_count"] == 2
    assert tree["ticket_count"] == 5

    node_by_id = {node["node_id"]: node for node in nodes if node["tree_id"] == "DT-CARPLAY"}
    complaint = node_by_id["COM-01"]
    precondition = node_by_id["PRE-01"]
    collect_precondition = node_by_id["ACT-PRE-01"]
    reproduction = node_by_id["REP-01"]
    technical = node_by_id["TEC-01"]
    outcome = node_by_id["OUT-01"]
    recorded_outcome = node_by_id["OUTCOME-01"]
    evidence_package = node_by_id["ESC-01"]

    assert complaint["question_or_action"] == (
        "Does the complaint match Connection / Pairing (连接/配对)?"
    )
    assert complaint["true_next_node"] == "PRE-01"
    assert complaint["false_next_node"] == ""
    assert precondition["question_or_action"] == "Is prerequisite evidence recorded?"
    assert precondition["yes_ticket_count"] == 3
    assert precondition["no_ticket_count"] == 2
    assert precondition["true_next_node"] == "REP-01"
    assert precondition["false_next_node"] == "ACT-PRE-01"
    assert collect_precondition["question_or_action"] == "Collect prerequisite evidence"
    assert collect_precondition["next_node"] == "REP-01"
    assert reproduction["question_or_action"] == "Is reproduction context recorded?"
    assert reproduction["yes_ticket_count"] == 3
    assert reproduction["no_ticket_count"] == 2
    assert technical["question_or_action"] == "Is ECU, software, or RCA evidence recorded?"
    assert technical["yes_ticket_count"] == 3
    assert technical["no_ticket_count"] == 2
    assert outcome["question_or_action"] == "Is a recovery outcome recorded?"
    assert outcome["yes_ticket_count"] == 3
    assert outcome["no_ticket_count"] == 2
    assert outcome["true_next_node"] == "OUTCOME-01"
    assert outcome["false_next_node"] == "ESC-01"
    assert recorded_outcome["question_or_action"] == "Reconnect the cable"
    assert recorded_outcome["source_ticket_count"] == 3
    assert evidence_package["question_or_action"] == "Prepare evidence package"
    assert evidence_package["source_ticket_count"] == 2
    assert all(
        node["node_source"] == "field_scenario_evidence_gate_template"
        for node in node_by_id.values()
    )


def test_evidence_gate_renderer_uses_yes_no_questions_and_actions() -> None:
    scenarios = [
        {
            "scenario_id": "FS-0001",
            "rep_function": "CarPlay",
            "affected_function": "CarPlay",
            "affected_function_raw": "CarPlay",
            "customer_symptom": "Apple CarPlay cannot connect",
            "trigger_condition": "Activation fails while connecting the iPhone through USB",
            "top_ecus": [("HU-H", 1)],
            "top_sw_versions": [("24-03", 1)],
            "rca_root_cause": "",
            "recovery_action": "Reconnect the cable",
            "ticket_count": 1,
        },
        {
            "scenario_id": "FS-0002",
            "rep_function": "CarPlay",
            "affected_function": "CarPlay",
            "affected_function_raw": "CarPlay",
            "customer_symptom": "Apple CarPlay connection fails",
            "trigger_condition": "Connection attempt fails",
            "observed_behaviour": "Connection does not start",
            "top_ecus": [],
            "top_sw_versions": [],
            "rca_root_cause": "",
            "recovery_action": "",
            "ticket_count": 1,
        }
    ]
    trees, nodes, _ = build_evidence_gate_trees(scenarios, [])

    html = render_evidence_gate_tree_catalog_html(trees, nodes)

    assert "Evidence-Gate Triage Tree" in html
    assert "layoutEvidenceGateTree" in html
    assert "YES" in html
    assert "NO" in html
    assert "Does the complaint match" in html
    assert "Is prerequisite evidence recorded?" in html
    assert "Collect prerequisite evidence" in html
    assert "data-yes-ticket-count" in html
    assert "Customer Symptom branch" not in html


def test_evidence_gate_tree_omits_unobserved_no_branches() -> None:
    scenario = {
        "scenario_id": "FS-0001",
        "rep_function": "CarPlay",
        "affected_function": "CarPlay",
        "affected_function_raw": "CarPlay",
        "customer_symptom": "Apple CarPlay cannot connect",
        "trigger_condition": "Activation fails while connecting the iPhone through USB",
        "top_ecus": [("HU-H", 1)],
        "top_sw_versions": [("24-03", 1)],
        "rca_root_cause": "Pairing state was not restored",
        "recovery_action": "Reconnect the cable",
        "ticket_count": 1,
    }

    _, nodes, _ = build_evidence_gate_trees([scenario], [])
    node_by_id = {node["node_id"]: node for node in nodes if node["tree_id"] == "DT-CARPLAY"}

    assert node_by_id["COM-01"]["false_next_node"] == ""
    assert node_by_id["PRE-01"]["false_next_node"] == ""
    assert node_by_id["REP-01"]["false_next_node"] == ""
    assert node_by_id["TEC-01"]["false_next_node"] == ""
    assert node_by_id["OUT-01"]["false_next_node"] == ""
    assert {"COM-OTHER", "ACT-PRE-01", "ACT-REP-01", "ACT-TEC-01", "ESC-01"}.isdisjoint(node_by_id)


def test_tree_growth_context_ignores_apple_carplay_product_wording() -> None:
    scenario = {
        "scenario_id": "FS-0001",
        "rep_function": "CarPlay",
        "affected_function": "CarPlay",
        "affected_function_raw": "CarPlay",
        "customer_symptom": "Apple CarPlay connection fails",
        "rep_title": "Apple CarPlay connection fails",
        "trigger_condition": "Connection attempt fails",
        "observed_behaviour": "Connection does not start",
        "expected_behaviour": "",
        "ticket_count": 2,
        "top_ecus": [],
        "top_sw_versions": [],
        "rca_root_cause": "",
        "recovery_action": "",
    }

    build_evidence_gate_trees([scenario], [])
    candidates = build_service_function_tree_growth_candidates([scenario])

    carplay_candidate = next(
        candidate
        for candidate in candidates
        if candidate["service_function_id"] == "CARPLAY"
    )
    assert carplay_candidate["diagnostic_context_samples"] == (
        "No additional context recorded(1)"
    )


def test_build_excel_exports_symptom_solution_tree_sheets_and_html(tmp_path) -> None:
    from openpyxl import load_workbook

    from backend.analytics import phase09_idcevo_scenario_build as scenario_builder

    scenario = {
        "scenario_id": "FS-0001",
        "engineering_family_id": "AUDIO_MEDIA_PLATFORM",
        "engineering_family": "Audio / Media Platform",
        "affected_function": "CarPlay / HiCar",
        "affected_function_raw": "CarPlay",
        "rep_function": "Audio Infrastructure",
        "found_in_function_members": [
            {
                "found_in_function": "Audio Infrastructure",
                "function_responsible": "Audio Owner",
                "ticket_ids": ["D-1"],
            }
        ],
        "customer_symptom": "CarPlay cannot connect to the phone",
        "trigger_condition": "Connecting the iPhone through USB",
        "observed_behaviour": "Connection fails before projection starts",
        "expected_behaviour": "CarPlay projection starts",
        "recovery_action": "Reconnect the cable",
        "closure_decision": "Fix planned",
        "evidence_confidence": "high",
        "ticket_count": 1,
        "ticket_ids": ["D-1"],
        "risk_band": "High",
        "risk_index": 3.4,
        "max_risk_score": 9,
        "prod_exposure_count": 1,
        "rep_ticket_id": "D-1",
        "rep_title": "CarPlay connection fails",
        "owners": [],
        "severity": 4,
        "production_applicability": 3.0,
        "exposure_frequency": 1.0,
        "perceptibility": 5,
        "diagnosability": 3.0,
        "recoverability": 3.0,
    }
    families = [
        {
            "family_id": "FAM-01",
            "family_name": "CarPlay / HiCar",
            "scenario_count": 1,
            "ticket_count": 1,
            "max_risk_score": 9,
            "scenarios": [scenario],
        }
    ]
    knowledge_cards = [
        {
            "scenario_id": "FS-0001",
            "family_id": "FAM-01",
            "customer_symptom": scenario["customer_symptom"],
            "trigger_condition": scenario["trigger_condition"],
            "affected_function": scenario["affected_function"],
            "risk_band": "High",
            "risk_index": 3.4,
            "diagnostic_entry": "ECU: HU-H",
            "recovery_action": scenario["recovery_action"],
            "escalation_criteria": "Repeat occurrence",
            "evidence_gaps": "无显著证据缺口",
            "vehicle_versions": "24-03(1)",
            "rep_ticket_id": "D-1",
            "ticket_count": 1,
        }
    ]
    output_xlsx = tmp_path / "scenario-library.xlsx"
    output_html = tmp_path / "scenario-decision-tree.html"

    scenario_builder.build_excel(
        [scenario],
        families,
        [],
        knowledge_cards,
        [],
        [{"ticket_id": "D-1", "extraction": {"is_field_scenario": True}}],
        {"D-1": {"risk_tier": "production-exposure", "risk_score": "9", "closure_reason": "Fix planned", "title": "CarPlay connection fails"}},
        output_xlsx=output_xlsx,
        decision_tree_html=output_html,
    )

    assert scenario_builder.OUTPUT_XLSX.name == (
        "IDCEVO_Phase09_Field_Escape_Scenario_Library_V12_Symptom_to_Solution_Trees.xlsx"
    )

    workbook = load_workbook(output_xlsx, data_only=True)
    try:
        assert workbook.sheetnames == [
            "00_总览",
            "01_Scenario_Library",
            "02_Scenario_FoundIn_Function",
            "03_Service_Function_Catalog",
            "04_Decision_Tree_Catalog",
            "05_Decision_Tree_Nodes",
            "06_Service_Knowledge_Cards",
            "07_Ticket_Mapping",
            "08_Excluded_Tickets",
            "09_After_Sales_Reference_Trees",
            "10_Tree_Growth_Candidates",
            "11_Data_Dictionary",
        ]
        assert {
            "Service Function",
            "Scenario ID",
            "Problem Type",
            "Customer Symptom",
            "Trigger Condition",
            "Found In Functions",
            "Function Responsible",
            "Found In Function Count",
        }.issubset(
            next(workbook["01_Scenario_Library"].iter_rows(min_row=1, max_row=1, values_only=True))
        )
        scenario_headers = next(
            workbook["01_Scenario_Library"].iter_rows(
                min_row=1, max_row=1, values_only=True
            )
        )
        assert "Family ID" not in scenario_headers
        assert scenario_headers.index("Problem Type") < scenario_headers.index(
            "Customer Symptom"
        )
        assert not {
            "Service Function ID",
            "Tree ID",
            "Reference Tree IDs",
        }.intersection(scenario_headers)
        tree_headers = list(
            next(
                workbook["04_Decision_Tree_Catalog"].iter_rows(
                    min_row=1, max_row=1, values_only=True
                )
            )
        )
        tree_column = {header: index for index, header in enumerate(tree_headers)}
        carplay_tree = next(
            row
            for row in workbook["04_Decision_Tree_Catalog"].iter_rows(min_row=2, values_only=True)
            if row[tree_column["Service Function"]] == "CarPlay"
        )
        assert carplay_tree[tree_column["Scenario Count"]] == 1
        assert carplay_tree[tree_column["Ticket Count"]] == 1
        assert carplay_tree[tree_column["Tree Generation"]] == (
            "field_scenario_symptom_solution_v1"
        )
        assert carplay_tree[tree_column["Diagnostic Layers"]] == (
            "Issue Group; Symptom Pattern; Trigger Pattern; Ticket Solution"
        )
        assert carplay_tree[tree_column["Source Ticket Mentions"]] == 1
        assert carplay_tree[tree_column["Phase 09 Evidence Status"]] == (
            "field_scenario_data_available"
        )
        assert {
            "Source Ticket Mentions",
            "Phase 09 Evidence Status",
        }.issubset(tree_headers)
        assert "Evidence-Gate Template" not in tree_headers
        assert "Gate Stages" not in tree_headers
        node_headers = list(
            next(
                workbook["05_Decision_Tree_Nodes"].iter_rows(
                    min_row=1, max_row=1, values_only=True
                )
            )
        )
        node_column = {header: index for index, header in enumerate(node_headers)}
        assert {
            "Node ID",
            "Parent Node ID",
            "Question / Solution",
            "Solution Status",
            "Source Scenario Count",
            "Source Ticket Count",
        }.issubset(node_headers)
        assert not {
            "YES Next Node",
            "NO Next Node",
            "Next Node",
            "YES Scenario Count",
            "YES Ticket Count",
            "NO Scenario Count",
            "NO Ticket Count",
        }.intersection(node_headers)
        solution_node = next(
            row
            for row in workbook["05_Decision_Tree_Nodes"].iter_rows(
                min_row=2, values_only=True
            )
            if row[node_column["Service Function"]] == "CarPlay"
            and row[node_column["Node Type"]] == "Ticket Solution"
        )
        assert solution_node[node_column["Question / Solution"]] == (
            "Reconnect the cable (1 tickets)"
        )
        assert solution_node[node_column["Parent Node ID"]].startswith("TRG-")
        assert solution_node[node_column["Node Source"]] == (
            "field_scenario_symptom_solution"
        )
        assert solution_node[node_column["Solution Status"]] == "ticket_recorded_solution"
        found_in_function_headers = list(
            next(
                workbook["02_Scenario_FoundIn_Function"].iter_rows(
                    min_row=1, max_row=1, values_only=True
                )
            )
        )
        found_in_function_column = {
            header: index for index, header in enumerate(found_in_function_headers)
        }
        found_in_function_mapping = next(
            row
            for row in workbook["02_Scenario_FoundIn_Function"].iter_rows(
                min_row=2, values_only=True
            )
            if row[found_in_function_column["Scenario ID"]] == "FS-0001"
        )
        assert found_in_function_mapping[
            found_in_function_column["Found In Function"]
        ] == "Audio Infrastructure"
        assert found_in_function_mapping[
            found_in_function_column["Function Responsible"]
        ] == "Audio Owner"
        reference_tree = next(
            row
            for row in workbook["09_After_Sales_Reference_Trees"].iter_rows(min_row=2, values_only=True)
            if row[0] == "carplay.png"
        )
        assert reference_tree[1] == "CarPlay"
        for worksheet in workbook.worksheets:
            headers = next(
                worksheet.iter_rows(min_row=1, max_row=1, values_only=True)
            )
            assert not {
                "Service Function ID",
                "Tree ID",
                "Reference Tree IDs",
                "Reference Tree ID",
            }.intersection(headers)
        growth_candidate = next(
            row
            for row in workbook["10_Tree_Growth_Candidates"].iter_rows(min_row=2, values_only=True)
            if row[1] == "CarPlay"
        )
        assert growth_candidate[0] == "Phone Projection"
        assert growth_candidate[2] == "connection_pairing"
        dictionary_fields = {
            row[0]
            for row in workbook["11_Data_Dictionary"].iter_rows(min_row=5, values_only=True)
            if row[0]
        }
        assert {
            "Service Function",
            "Problem Type",
            "Found In Function",
            "Function Responsible",
            "Parent Node ID",
            "Issue Group",
            "Symptom Pattern",
            "Trigger Pattern",
            "Ticket-recorded Solution",
            "Solution Status",
            "Node Source",
        }.issubset(dictionary_fields)
    finally:
        workbook.close()

    assert output_html.exists()
    html = output_html.read_text(encoding="utf-8")
    assert "IDCEVO Phase 09 Symptom-to-Solution Tree Catalog" in html
    assert "DT-CARPLAY" in html
    assert 'id="source-ticket-mentions"' in html
    assert 'id="phase09-evidence-status"' in html
    assert 'document.getElementById("source-ticket-mentions").textContent = String(tree.source_ticket_mention_count);' in html
    assert 'document.getElementById("phase09-evidence-status").textContent = tree.phase09_evidence_status;' in html
    assert "Prerequisites recorded?" not in html
    assert "ECU / SW / RCA recorded?" not in html

    knowledge_base_path = output_xlsx.with_name(
        "scenario-library_Decision_Tree_Knowledge_Base.json"
    )
    knowledge_base = json.loads(knowledge_base_path.read_text(encoding="utf-8"))
    assert knowledge_base["schema_version"] == "phase09-decision-tree-kb-v7"
    assert knowledge_base["tree_generation"].startswith("Symptom-driven problem navigation")
    assert knowledge_base["solution_source_policy"].startswith(
        "Ticket-recorded solution text"
    )
    carplay_knowledge_tree = next(
        tree
        for tree in knowledge_base["decision_trees"]
        if tree["service_function_id"] == "CARPLAY"
    )
    assert carplay_knowledge_tree["source_ticket_mention_count"] == 1
    assert carplay_knowledge_tree["phase09_evidence_status"] == (
        "field_scenario_data_available"
    )
    assert "workflow_template" not in knowledge_base
    assert not {
        "Precondition Gate",
        "Reproduction Gate",
        "Technical Evidence Gate",
        "Recorded Outcome Gate",
    }.intersection(
        node["node_type"] for node in knowledge_base["decision_tree_nodes"]
    )
    assert len(knowledge_base["after_sales_reference_trees"]) == 39
    assert knowledge_base["scenario_found_in_function_mappings"] == [
        {
            "scenario_id": "FS-0001",
            "found_in_function": "Audio Infrastructure",
            "function_responsible": "Audio Owner",
            "ticket_count": 1,
            "ticket_ids": "D-1",
            "mapping_role": "R&D attribution",
        }
    ]
    assert "engineering_families" not in knowledge_base
    assert any(
        candidate["service_function_id"] == "CARPLAY"
        for candidate in knowledge_base["tree_growth_candidates"]
    )