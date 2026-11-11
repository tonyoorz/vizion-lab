from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from backend.analytics.ingest.source_store import OctaneSourceStore
from backend.analytics.test_method_evidence_report import (
    _OCCURRENCE_COUNT_PATTERN,
    _structured_field_count,
    build_test_method_evidence_payload,
    generate_test_method_evidence_report,
)


def _create_source_db(path: Path) -> None:
    store = OctaneSourceStore(path)
    store.create_tables()
    store.close()

    with sqlite3.connect(path) as conn:
        defects = [
            ("D-1", "Case execution failure", "A direct testcase execution found this failure.", "App"),
            ("D-2", "Linked testcase failure", "This defect is associated with a testcase but has no execution record.", "RSU"),
            (
                "D-3",
                "Free-test lifecycle failure",
                """
                Test case reference: Free test
                Preconditions:
                Vehicle ready.
                Actions:
                Start the vehicle and open Speech.
                Expected result:
                Speech is available.
                Observed result:
                Speech is unavailable.
                How often did the tester try to reproduce the defect? 4
                In how many lifecycles did the tester try to reproduce the defect? 2
                How often did the defect occur (overall tried attempts and lifecycles)? 3
                Observed behaviour occurs on multiple platforms: yes
                VIN: WBYTEST
                Software version: 26-11
                Logs were collected for diagnosis.
                """,
                "Speech",
            ),
            ("D-4", "No-link restart failure", "Restart the vehicle and observe the missing media state. The report has no testcase reference.", "Media"),
        ]
        conn.executemany(
            """
            INSERT INTO octane_defects(
                defect_id, name, description, comments, team, year, project,
                raw_json, fetched_at
            ) VALUES (?, ?, ?, '', 'DTSV_China', '2026', ?, '{}', '2026-10-19T00:00:00Z')
            """,
            defects,
        )
        conn.execute(
            """
            INSERT INTO octane_manual_runs(
                mr_id, defect_id, test_id, test_name, status, steps_num, raw_json, fetched_at
            ) VALUES ('MR-1', 'D-1', 'TC-1', '[App] Login happy path', 'Passed', '3', '{}', '2026-10-19T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO octane_testcases(
                test_id, scope_team, scope_release, source, test_name, test_subtype,
                run_count, defect_ids_json, feature_ids_json, story_ids_json, raw_json, fetched_at
            ) VALUES ('TC-2', 'DTSV_China', 'R-26-11', 'octane', '[RSU] Timer', 'test_manual',
                0, ?, '[]', '[]', '{}', '2026-10-19T00:00:00Z')
            """,
            (json.dumps(["D-2"]),),
        )


def test_payload_treats_unlinked_defects_as_free_test_and_exposes_ticket_deep_dive(tmp_path: Path) -> None:
    db_path = tmp_path / "source.db"
    _create_source_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE octane_defects
            SET comments = 'Trace attached. Actions/steps to recover from error: retry.'
            WHERE defect_id = 'D-4'
            """
        )

    payload = build_test_method_evidence_payload(db_path=db_path, team="DTSV_China")

    assert payload["summary"] == {
        "totalDefects": 4,
        "executedTestcaseDefects": 1,
        "testcaseLinkedNoExecutionDefects": 1,
        "noTestcaseEvidenceDefects": 2,
        "freeTestDefects": 2,
        "explicitFreeTestDefects": 1,
        "inferredFreeTestDefects": 1,
    }
    assert payload["execution"]["businessScope"] == [{"label": "App", "count": 1}]
    assert payload["execution"]["testcaseTitles"] == [{"label": "[App] Login happy path", "count": 1}]
    assert payload["execution"]["methodSignals"] == [
        {"label": "Planned functional workflow", "count": 1},
        {"label": "State or lifecycle transition", "count": 1},
        {"label": "Cross-device or cross-service workflow", "count": 1},
    ]
    assert payload["freeTest"]["businessScope"] == [
        {"label": "Media", "count": 1},
        {"label": "Speech", "count": 1},
    ]
    assert payload["freeTest"]["discoveryBasis"] == [
        {"label": "Explicit Free-test report reference", "count": 1},
        {"label": "No testcase link (Free test by policy)", "count": 1},
    ]
    scenario_signals = {row["label"]: row["count"] for row in payload["freeTest"]["scenarioSignals"]}
    assert scenario_signals["State / lifecycle variation"] == 1
    assert "Diagnostic/log artifact reference" not in scenario_signals
    ticket_rows = {row["defectId"]: row for row in payload["freeTest"]["tickets"]}
    assert ticket_rows["3"]["discoveryBasis"] == "Explicit Free-test report reference"
    assert ticket_rows["3"]["reportedOccurrenceCount"] == 3
    assert ticket_rows["3"]["repeatabilityClass"] == "High repeatability (50-99%)"
    assert ticket_rows["4"] == {
        "defectId": "4",
        "name": "No-link restart failure",
        "project": "Media",
        "discoveryBasis": "No testcase link (Free test by policy)",
        "scenarioSignals": ["State / lifecycle variation"],
        "reproductionAttempts": None,
        "lifecycleRuns": None,
        "reportedOccurrenceCount": None,
        "repeatabilityClass": "Not documented",
        "multiPlatformConfirmed": False,
        "hasEnvironmentFingerprint": False,
        "hasDiagnosticArtifactReference": True,
        "hasPreconditions": False,
        "hasActionProcedure": False,
        "hasExpectedResult": False,
        "hasObservedResult": False,
        "candidateProcedureLines": 0,
        "softwareVersion": "",
        "productAreas": [],
        "createdAt": "",
    }
    assert payload["freeTest"]["effortEvidence"] == {
        "ticketsWithReproductionAttempts": 1,
        "documentedReproductionAttempts": 4,
        "ticketsWithLifecycleCoverage": 1,
        "documentedLifecycleRuns": 2,
        "ticketsWithMultiPlatformEvidence": 1,
        "ticketsWithEnvironmentEvidence": 1,
        "ticketsWithDiagnosticEvidence": 2,
        "ticketsWithOccurrenceCount": 1,
        "documentedOccurrences": 3,
    }
    assert payload["freeTest"]["repeatabilitySummary"] == {
        "ticketsWithComparableRepeatability": 1,
        "documentedAttempts": 4,
        "documentedOccurrences": 3,
        "observedOccurrenceRate": 0.75,
        "fullyReproducibleTickets": 0,
        "highRepeatabilityTickets": 1,
        "intermittentTickets": 0,
    }
    assert ticket_rows["3"]["hasPreconditions"] is True
    assert ticket_rows["3"]["hasActionProcedure"] is True
    assert ticket_rows["3"]["hasExpectedResult"] is True
    assert ticket_rows["3"]["hasObservedResult"] is True
    assert ticket_rows["3"]["candidateProcedureLines"] == 1
    assert payload["stepEvidenceComparison"] == {
        "testcaseExecution": {
            "directDefectCount": 1,
            "linkedRunCount": 1,
            "runsWithStepCount": 1,
            "documentedStepCount": 3,
            "localScriptCount": 0,
            "scriptEvidenceStatus": "testcase_script_unavailable_locally",
        },
        "freeTestNarrative": {
            "ticketCount": 2,
            "ticketsWithPreconditions": 1,
            "ticketsWithActionProcedure": 1,
            "ticketsWithExpectedResult": 1,
            "ticketsWithObservedResult": 1,
            "ticketsWithFullNarrative": 1,
            "ticketsWithCandidateProcedureLines": 1,
            "documentedCandidateProcedureLines": 1,
            "medianCandidateProcedureLines": 1,
        },
    }
    pattern_rows = {row["pattern"]: row for row in payload["freeTest"]["patternAnalysis"]}
    assert pattern_rows["State / lifecycle variation"] == {
        "pattern": "State / lifecycle variation",
        "defectCount": 1,
        "ticketsWithReproductionAttempts": 0,
        "documentedReproductionAttempts": 0,
        "ticketsWithLifecycleCoverage": 0,
        "documentedLifecycleRuns": 0,
        "ticketsWithComparableRepeatability": 0,
        "fullyReproducibleTickets": 0,
        "highRepeatabilityTickets": 0,
        "intermittentTickets": 0,
        "observedOccurrenceRate": None,
    }


def test_generator_writes_html_and_machine_readable_evidence_payload(tmp_path: Path) -> None:
    db_path = tmp_path / "source.db"
    output_path = tmp_path / "test_method_evidence.html"
    _create_source_db(db_path)

    generated_html, generated_json = generate_test_method_evidence_report(
        db_path=db_path,
        output_path=output_path,
        team="DTSV_China",
    )

    assert generated_html == output_path
    assert generated_json == output_path.with_suffix(".json")
    report_html = output_path.read_text(encoding="utf-8")
    assert "Test Discovery Evidence" in report_html
    assert "High-level Insight" in report_html
    assert "Additional work beyond testcase execution" in report_html
    assert "Evidence basis" in report_html
    assert "not an overall test efficiency rate" in report_html
    assert "Testcase Method Signals" in report_html
    assert "Free-test Scenario Signals" in report_html
    assert "Step Evidence Comparison" in report_html
    assert "Testcase script evidence gap" in report_html
    assert "Method interpretation" in report_html
    assert "documentation improvement opportunity" in report_html
    assert "Scenario Pattern x Effort" in report_html
    assert "Free-test Ticket Deep Dive" in report_html
    assert "No testcase link (Free test by policy)" in report_html
    assert "Cross-device or cross-service workflow" in report_html
    payload = json.loads(generated_json.read_text(encoding="utf-8"))
    assert payload["summary"]["explicitFreeTestDefects"] == 1


def test_payload_does_not_treat_empty_environment_template_fields_as_evidence(tmp_path: Path) -> None:
    db_path = tmp_path / "source.db"
    _create_source_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO octane_defects(
                defect_id, name, description, comments, team, year, project,
                raw_json, fetched_at
            ) VALUES (
                'D-5', 'Blank free-test template',
                'Test case reference: Free test Platform: VIN: Software version:', '',
                'DTSV_China', '2026', 'IDCEVO', '{}', '2026-10-19T00:00:00Z'
            )
            """
        )

    payload = build_test_method_evidence_payload(db_path=db_path, team="DTSV_China")

    assert payload["summary"]["explicitFreeTestDefects"] == 2
    assert payload["freeTest"]["effortEvidence"]["ticketsWithEnvironmentEvidence"] == 1


def test_structured_occurrence_count_stops_before_recovery_metadata_without_standard_boundary() -> None:
    text = """
    How often did the defect occur (overall tried attempts and lifecycles)?
    [Please fill in natural numbers] 1 (x) Self-healing: Expected behavior was given without further interaction.
    Zeitpunkt: 31.08.2026 [19:15:00] (UTC + 8), Melder: Xu Miao; ZE-361; 18611958732;
    """

    assert _structured_field_count(text, _OCCURRENCE_COUNT_PATTERN) == 1


def test_step_comparison_uses_actual_procedures_and_excludes_template_options() -> None:
    from backend.analytics.test_step_method_comparison import extract_free_procedure, analyze_procedure

    description = '''<p>preconditions and actions (e.g. coding, route-url for navigation errors, weather/environmental circumstances):</p>
    <p>Preconditions:</p><p>Music is playing.</p><p>Actions:</p>
    <p>1. While music is playing, receive a call.</p><p>2. Switch audio source and check sound.</p>
    <p>Expected result/behaviour:</p><p>Sound resumes.</p>
    <p>Observed result/behaviour:</p><p>No sound.</p>
    <p>Easiest way of recovery:</p><p>() Factory Reset</p>'''
    procedure = extract_free_procedure(description)
    assert 'receive a call' in procedure['actions']
    assert 'Factory Reset' not in procedure['actions']
    assert 'weather/environmental' not in procedure['preconditions']
    evidence = analyze_procedure(procedure['preconditions'], procedure['actions'])
    assert 'Concurrent activity / interference' in evidence
    assert 'State / source / account transition' in evidence
    assert 'Lifecycle / power transition' not in evidence
    assert all(item['excerpt'] for item in evidence.values())


def test_step_comparison_treats_testcase_methods_as_shared_not_free_test_exclusive() -> None:
    from backend.analytics.test_step_method_comparison import analyze_procedure, build_comparison

    cases = [{'testId': '10', 'name': 'Call test', 'script': '- [PreCon] Music playing\n- While music is playing, receive a call\n- ? Audio resumes', 'functions': ['Audio'], 'defectIds': ['1']}]
    tickets = [{'defectId': '2', 'name': 'Sound missing', 'description': '<p>Preconditions:</p><p>Music playing</p><p>Actions:</p><p>While music is playing, receive a call</p><p>Expected result:</p><p>Audio resumes</p>', 'productAreas': ['Audio'], 'project': 'IDCEVO'}]
    result = build_comparison(cases, tickets)
    row = next(item for item in result['methodComparison'] if item['method'] == 'Concurrent activity / interference')
    assert row['testcases'] == 1
    assert row['freeTestTickets'] == 1
    assert row['interpretation'] == 'Shared method in observed procedures'
    assert result['coverage']['testcasesWithScripts'] == 1
    assert result['freeTestEvidence'][0]['actions']
    assert row['pairedExamples'][0]['function'] == 'Audio'
    assert row['pairedExamples'][0]['testcase']['testId'] == '10'
    assert row['pairedExamples'][0]['freeTest']['defectId'] == '2'
    assert 'Lifecycle / power transition' not in analyze_procedure('System power on', 'Open Music and check display')
    assert 'Lifecycle / power transition' not in analyze_procedure('', 'Wake up IPA and say Play music for sleep')
    assert 'Lifecycle / power transition' in analyze_procedure('', 'Wake up vehicle after bus sleep')


def test_combined_free_test_context_keeps_steps_without_promoting_setup_or_recovery() -> None:
    from backend.analytics.test_step_method_comparison import extract_free_procedure

    description = '''<p>preconditions and actions (e.g. coding, route-url for navigation errors, weather/environmental circumstances):</p>
    <p>1. Vehicle is connected to network.2. Open QQ Music.3. Switch account and check playback.</p>
    <p>expected result/behaviour:</p><p>Playback continues.</p>
    <p>observed result/behaviour:</p><p>Playback stops.</p>
    <p>actions/steps to recover from error:</p><p>Factory Reset</p>'''
    result = extract_free_procedure(description)
    assert 'Open QQ Music' in result['actions']
    assert 'Switch account' in result['actions']
    assert 'Vehicle is connected' not in result['actions']
    assert 'Vehicle is connected' in result['preconditions']
    assert 'Open QQ Music' in result['combinedContext']
    assert 'Factory Reset' not in result['combinedContext']
    assert 'Playback stops' not in result['actions']
    assert result['extractionStatus'] == 'combined_actions'


def test_full_analysis_accounts_for_every_ticket_including_unresolved_records() -> None:
    from backend.analytics.test_step_method_comparison import _render, build_comparison

    result = build_comparison([], [
        {'defectId': '1', 'name': 'Music failure', 'description': 'Actions: Open Music Expected result: Playback works'},
        {'defectId': '2', 'name': 'Setup only', 'description': 'Preconditions and actions: Network is connected. Expected result: Works'},
        {'defectId': '3', 'name': 'Empty description', 'description': ''},
    ])
    assert result['coverage']['freeTestAnalyzed'] == 3
    assert result['coverage']['freeTestWithActions'] == 1
    assert result['coverage']['freeTestWithoutActions'] == 2
    assert sum(row['count'] for row in result['extractionAudit']) == 3
    assert len(result['freeTestEvidence']) == 3
    assert result['freeTestEvidence'][1]['combinedContext']
    assert result['freeTestEvidence'][1]['extractionStatus'] == 'context_only'
    assert result['freeTestEvidence'][2]['extractionStatus'] == 'missing_description'
    html = _render(result)
    assert 'Full-population Analysis Audit' in html
    assert 'D2: Setup only' in html
    assert 'D3: Empty description' in html
    assert 'Discovery Path Pattern Library' in html
    assert result['discoveryPatternLibrary']['analyzedTicketCount'] == 3


def test_unrecognized_description_is_retained_for_review_not_inferred_as_actions() -> None:
    from backend.analytics.test_step_method_comparison import extract_free_procedure

    result = extract_free_procedure('Unexpected screen behavior documented without recognized section headings.')
    assert result['extractionStatus'] == 'unrecognized_structure'
    assert result['actions'] == ''
    assert result['descriptionExcerpt'].startswith('Unexpected screen behavior')


def test_full_analysis_can_reuse_a_frozen_cohort_without_reclassifying(monkeypatch, tmp_path: Path) -> None:
    import sys
    from backend.analytics.test_step_method_comparison import main

    db_path = tmp_path / 'source.db'
    _create_source_db(db_path)
    baseline_path = tmp_path / 'baseline.json'
    baseline_path.write_text(json.dumps({
        'scope': {'generatedAt': '2026-10-20T02:00:51Z', 'classification': {'freeTestDefects': 2}},
        'testcaseEvidence': [{'testId': 'TC-1', 'name': 'Music test', 'script': '- Play Music\n- ? Music plays', 'functions': ['Audio'], 'defectIds': ['1']}],
        'freeTestEvidence': [
            {'defectId': '3', 'name': 'Speech failure', 'project': 'Speech'},
            {'defectId': '4', 'name': 'Restart failure', 'project': 'Media'},
        ],
    }), encoding='utf-8')
    output = tmp_path / 'result'
    monkeypatch.setattr(sys, 'argv', [
        'test_step_method_comparison', '--db-path', str(db_path),
        '--baseline', str(baseline_path), '--output-directory', str(output),
    ])
    main()
    result = json.loads((output / 'step_method_comparison.json').read_text(encoding='utf-8'))
    assert {row['defectId'] for row in result['freeTestEvidence']} == {'3', '4'}
    assert result['coverage']['freeTestAnalyzed'] == 2
    assert result['scope']['cohortGeneratedAt'] == '2026-10-20T02:00:51Z'
    assert (output / 'step_method_comparison.html').is_file()


def test_combined_multiline_heading_and_action_wording_variants_are_extracted() -> None:
    from backend.analytics.test_step_method_comparison import extract_free_procedure

    description = '''Preconditions and actions (e.g. coding, route-url for navigation errors,
    weather/environmental circumstances)
    >1. Activate speech via WuW
    >2. Speak command: create a component
    >3. On the BMT side, switch media source to Ximalaya
    >4. Put the phone on the seat
    >5. Leave vehicle
    Expected result/behaviour: Command works
    Actions/steps to recover from error: Factory Reset'''
    result = extract_free_procedure(description)
    for action in ('Activate speech', 'Speak command', 'switch media source', 'Put the phone', 'Leave vehicle'):
        assert action in result['actions']
    assert 'Factory Reset' not in result['actions']


def test_combined_command_variants_are_preserved_as_input_stimuli() -> None:
    from backend.analytics.test_step_method_comparison import extract_free_procedure

    result = extract_free_procedure('''Preconditions and actions:
    Network is connected
    try to adjust media volume in different behaviors:
    1, just talk commands - change volume to percentage
    2, hotword commands - volume up, volume down
    3, normal commands via wakeup - hi BMW, decrease volume
    Expected result: Volume changes''')
    assert 'Network is connected' not in result['actions']
    assert 'just talk commands' in result['actions']
    assert 'hotword commands' in result['actions']
    assert 'normal commands' in result['actions']


def test_combined_action_chain_retains_passenger_command_and_arrow_steps() -> None:
    from backend.analytics.test_step_method_comparison import analyze_procedure, extract_free_procedure

    result = extract_free_procedure('''Preconditions and actions:
    Play QQ Music on the CID, then have the passenger say "Next song".
    login app>switch account>check car control
    Expected result: Operation works''')
    assert 'passenger say "Next song"' in result['actions']
    assert 'switch account' in result['actions']
    assert 'Input / language / command variation' in analyze_procedure('', result['actions'])
    assert 'Input / language / command variation' in analyze_procedure('', 'hotword commands - volume up')


def test_discovery_pattern_library_requires_compound_actions_and_deduplicates_effort() -> None:
    from backend.analytics.test_step_method_comparison import build_discovery_pattern_library

    rapid = {'defectId': '1', 'name': 'Playback fails', 'actions': 'Play Carlink media; mute and unmute quickly.', 'preconditions': '', 'productAreas': ['Audio'], 'reproductionAttempts': 5, 'lifecycleRuns': 1}
    ordinary = {'defectId': '2', 'name': 'Normal playback', 'actions': 'Play music. Pause playback.', 'preconditions': 'System power on', 'productAreas': ['Audio']}
    library = build_discovery_pattern_library([rapid, rapid, ordinary], [])
    family = next(row for row in library['patterns'] if row['patternId'] == 'rapid_inverse_operations')
    assert library['analyzedTicketCount'] == 2
    assert family['caseCount'] == 1
    assert family['reportedAttempts'] == 5
    assert family['casesWithAttempts'] == 1
    assert family['reportedLifecycles'] == 1
    assert family['caseStudies'][0]['evidence']
    assert family['caseStudies'][0]['discoveryPath'] == rapid['actions']
    assert '2' not in library['candidateTicketIds']
    assert library['candidateEffort']['reportedAttempts'] == 5
    assert library['candidateEffort']['casesWithAttempts'] == 1


def test_discovery_pattern_library_keeps_missing_effort_distinct_from_zero() -> None:
    from backend.analytics.test_step_method_comparison import build_discovery_pattern_library

    ticket = {'defectId': '3', 'name': 'Mute failure', 'actions': 'Mute and unmute quickly.', 'preconditions': ''}
    family = build_discovery_pattern_library([ticket], [])['patterns'][0]
    assert family['casesWithAttempts'] == 0
    assert family['casesWithoutAttempts'] == 1
    assert family['caseStudies'][0]['reproductionAttempts'] is None
    assert family['caseStudies'][0]['lifecycleRuns'] is None


def test_reconnection_title_does_not_invent_an_unrecorded_reconnection_step() -> None:
    from backend.analytics.test_step_method_comparison import build_discovery_pattern_library

    ticket = {'defectId': '4', 'name': 'CarPlay reconnects automatically', 'actions': 'Connect CarPlay. Go to device list and disconnect CarPlay.', 'preconditions': ''}
    library = build_discovery_pattern_library([ticket], [])
    assert not any(row['patternId'] == 'disconnect_reconnect_sequence' for row in library['patterns'])


def test_source_switch_pattern_retains_context_and_does_not_require_fast_timing() -> None:
    from backend.analytics.test_step_method_comparison import build_discovery_pattern_library

    ticket = {'defectId': '5', 'name': 'Audio ownership fails', 'actions': 'Play QQ Music. Switch media source to NetEase and check audio.', 'preconditions': '', 'unclassifiedContext': 'Rear seat is active.'}
    library = build_discovery_pattern_library([ticket], [])
    family = next(row for row in library['patterns'] if row['patternId'] == 'source_switch_during_playback')
    assert family['caseCount'] == 1
    assert 'Rear seat is active' in family['caseStudies'][0]['scenarioContext']


def test_case_library_effort_reads_answers_not_template_instructions() -> None:
    from backend.analytics.test_step_method_comparison import build_discovery_pattern_library

    ticket = {
        'defectId': '6', 'name': 'Timing failure', 'actions': 'Mute and unmute quickly.',
        'preconditions': '', 'reproductionAttempts': None, 'lifecycleRuns': 2,
        'descriptionExcerpt': '''How often you try to reproduce the defect: 6
        In how many lifecycles you try to reproduce the defect? Try at least in 2, better in 5 lifecycles. [8]
        How often did the defect occur: 3''',
    }
    family = build_discovery_pattern_library([ticket], [])['patterns'][0]
    assert family['reportedAttempts'] == 6
    assert family['reportedLifecycles'] == 8
    case = family['caseStudies'][0]
    assert case['confirmationEvidence']['lifecycleRuns']['source'] == 'descriptionExcerpt'
    assert case['confirmationEvidence']['lifecycleRuns']['previousSnapshotValue'] == 2


def test_case_patterns_do_not_invent_active_media_or_identity_change_from_initial_login() -> None:
    from backend.analytics.test_step_method_comparison import build_discovery_pattern_library

    ticket = {'defectId': '7', 'name': 'Voice error', 'preconditions': '', 'actions': 'Login BMW ID. Say "I want to listen rock music".'}
    patterns = build_discovery_pattern_library([ticket], [])['patterns']
    assert not any(row['patternId'] in {'voice_over_media_context', 'account_service_transition'} for row in patterns)


def test_call_aftermath_pattern_requires_the_recorded_event_order() -> None:
    from backend.analytics.test_step_method_comparison import build_discovery_pattern_library

    reversed_path = {'defectId': '8', 'name': 'Call problem', 'preconditions': '', 'actions': 'Connect CarPlay. Trigger E-call and hangup.'}
    forward_path = {'defectId': '9', 'name': 'CarPlay after call', 'preconditions': '', 'actions': 'Trigger E-call and hangup. Connect CarPlay.'}
    family = next(row for row in build_discovery_pattern_library([reversed_path, forward_path], [])['patterns'] if row['patternId'] == 'call_aftermath_connectivity')
    assert [row['defectId'] for row in family['caseStudies']] == ['9']