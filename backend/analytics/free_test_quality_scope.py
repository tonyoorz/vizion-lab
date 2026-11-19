"""Add independent quality and scenario views to frozen FreeTest evidence."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.workbook.properties import CalcProperties
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.hyperlink import Hyperlink

from .free_test_effort_breakdown import _literal
from .work_package_matrix import PACKAGES


QUALITY_LABELS = {
    'FS.CORRECT': ('Functional Suitability', 'Functional Correctness'),
    'PE.TIME': ('Performance Efficiency', 'Time Behaviour'),
    'CO.INTEROP': ('Compatibility', 'Interoperability'),
    'RE.MATURE': ('Reliability', 'Maturity'),
    'RE.AVAIL': ('Reliability', 'Availability'),
    'RE.FAULT': ('Reliability', 'Fault Tolerance'),
    'RE.RECOVER': ('Reliability', 'Recoverability'),
    'UNCLASSIFIED': ('Needs Review', 'Insufficient objective evidence'),
}

QUALITY_PRIORITY = (
    'RE.RECOVER', 'RE.FAULT', 'RE.AVAIL', 'PE.TIME',
    'CO.INTEROP', 'RE.MATURE', 'FS.CORRECT',
)

QUALITY_RULES = {
    'RE.RECOVER': (
        r'\brecover(?:y|s|ed|ing)?\b|\breconnect(?:ion|ed|ing)?\b|'
        r'after.{0,60}(?:restor(?:e|ed|ing)|reboot|restart)|'
        r'\u6062\u590d|\u91cd\u8fde|\u91cd\u65b0\u8fde\u63a5|\u91cd\u542f\u540e'
    ),
    'RE.FAULT': (
        r'\bfault toleran\w*|\bfault injection\b|'
        r'(?:network|connection|service).{0,30}(?:loss|lost|interrupt|unavailable)|'
        r'\u5bb9\u9519|\u6545\u969c\u6ce8\u5165|\u5f02\u5e38\u8f93\u5165|\u7f51\u7edc\u4e2d\u65ad'
    ),
    'RE.AVAIL': (
        r'\b(?:unavailable|unresponsive|crash(?:es|ed)?|freeze|frozen|hangs?)\b|'
        r'black screen|screen.{0,12}black|remains black|'
        r'\u9ed1\u5c4f|\u6b7b\u673a|\u5361\u6b7b|\u5d29\u6e83|\u4e0d\u53ef\u7528|\u65e0\u54cd\u5e94'
    ),
    'PE.TIME': (
        r'\b(?:slow(?:ly)?|latency|lagging|sluggish)\b|delayed response|response.{0,20}delay|'
        r'response takes \d+(?:\.\d+)? seconds?|'
        r'\u54cd\u5e94.{0,12}(?:\u6162|\u5ef6\u8fdf|\u8d85\u65f6)|'
        r'\u542f\u52a8.{0,8}\u6162|\u8017\u65f6|\u5361\u987f'
    ),
    'CO.INTEROP': (
        r'\b(?:interoperability|incompatib\w*|synchroni[sz]\w*|handoff)\b|'
        r'(?:phone|bluetooth|carplay|hicar|android auto).{0,40}(?:cannot connect|fails? to connect|not connect)|'
        r'\u4e92\u64cd\u4f5c|\u4e0d\u517c\u5bb9|\u540c\u6b65\u5931\u8d25|\u8fde\u63a5\u5931\u8d25'
    ),
    'RE.MATURE': (
        r'(?:frequent|repeated|intermittent).{0,25}(?:failure|crash|malfunction)|'
        r'\u9891\u7e41.{0,10}(?:\u6545\u969c|\u5d29\u6e83)|\u95f4\u6b47\u6027\u6545\u969c'
    ),
    'FS.CORRECT': (
        r'\b(?:wrong|incorrect|missing|unexpected|abnormal|fails?|failure|cannot|can not|unable|not working)\b|'
        r'\bno (?:audio|sound|display|response|notification|route|guidance)\b|'
        r'\b(?:not|does not|doesn.t|can.t).{0,30}(?:play|display|show|work|start|open|recogn|exit)|'
        r'\u9519\u8bef|\u4e0d\u6b63\u786e|\u5931\u8d25|\u65e0\u6cd5|\u4e0d\u80fd|\u4e0d\u5de5\u4f5c'
    ),
}

SCENARIO_RULES = (
    ('FREE-01', r'voice.input|natural speech|input.channel|command|language'),
    ('FREE-02', r'lifecycle|state|account|history|switch.*return'),
    ('FREE-03', r'occupant|role|screen|multi.device|control.object'),
    ('FREE-04', r'another audio|timing|sequence.*tim|concurr|interference'),
    ('FREE-05', r'disconnect|reconnect|recover|permission|negative|interrupt'),
    ('FREE-06', r'location|road|environment|journey|destination'),
)

PATTERN_SCENARIOS = {
    'rapid_inverse_operations': 'FREE-04',
    'audio_voice_interference': 'FREE-04',
    'disconnect_reconnect_sequence': 'FREE-05',
    'call_aftermath_connectivity': 'FREE-05',
    'dismissal_account_history': 'FREE-02',
    'noop_natural_speech': 'FREE-01',
    'input_channel_contrast': 'FREE-01',
    'occupant_screen_media': 'FREE-03',
    'lifecycle_conditioned_use': 'FREE-02',
    'configuration_contrast': 'FREE-05',
    'location_conditioned_navigation': 'FREE-06',
    'source_switch_during_playback': 'FREE-04',
    'voice_over_media_context': 'FREE-04',
    'account_service_transition': 'FREE-02',
}

METHOD_SCENARIOS = {
    'Input / language / command variation': 'FREE-01',
    'State / source / account transition': 'FREE-02',
    'Lifecycle / power transition': 'FREE-02',
    'Cross-device / cross-screen handoff': 'FREE-03',
    'Timing / repetition / sequence': 'FREE-04',
    'Concurrent activity / interference': 'FREE-04',
    'Interruption / cancellation / recovery': 'FREE-05',
    'Explicit boundary / negative setup': 'FREE-05',
}


def _signals(title: str, path: str) -> list[dict]:
    signals = []
    for field, text in (('title', title), ('path', path)):
        for code, expression in QUALITY_RULES.items():
            match = re.search(expression, text, re.IGNORECASE)
            if match:
                signals.append({
                    'quality': code, 'field': field,
                    'fragment': text[max(0, match.start() - 60):match.end() + 100],
                })
    return signals


def classify_free_test_evidence(data: dict) -> dict:
    library = data['discoveryPatternLibrary']
    grouped = {}
    cases = [(case, None, []) for case in data.get('freeTestEvidence', [])]
    for pattern in library['patterns']:
        pattern_id = str(pattern.get('patternId') or pattern['pattern'])
        scenario_tags = ([PATTERN_SCENARIOS[pattern_id]] if pattern_id in PATTERN_SCENARIOS else
                         [code for code, expression in SCENARIO_RULES
                          if re.search(expression, pattern['pattern'], re.IGNORECASE)])
        for case in pattern.get('caseStudies', []):
            cases.append((case, pattern_id, scenario_tags))
    complex_ids = {str(case.get('defectId') or '').strip()
                   for case, pattern_id, scenario_tags in cases if pattern_id is not None}
    if 'candidateTicketIds' in library:
        declared_ids = [str(ticket_id).strip() for ticket_id in library['candidateTicketIds']]
        if set(declared_ids) != complex_ids or len(declared_ids) != len(set(declared_ids)):
            raise ValueError('Complex candidate ID mismatch between declared subset and pattern evidence')
    if library.get('candidateTicketCount') is not None and library['candidateTicketCount'] != len(complex_ids):
        raise ValueError('Complex candidate count mismatch between declared subset and unique pattern evidence')
    for case, pattern_id, pattern_tags in cases:
        ticket_id = str(case.get('defectId') or '').strip()
        if not ticket_id:
            raise ValueError('Candidate evidence lacks a stable defectId')
        record = grouped.setdefault(ticket_id, {
            'ticketId': ticket_id, 'titles': [], 'paths': [], 'functions': [],
            'sourcePatterns': [], 'scenarioTags': [], 'attemptValues': [],
            'lifecycleValues': [], 'inComplexSubset': False,
        })
        record['inComplexSubset'] = record['inComplexSubset'] or pattern_id is not None
        if case.get('discoveryBasis') and record.get('discoveryBasis') and case['discoveryBasis'] != record['discoveryBasis']:
            raise ValueError(f'Conflicting discovery basis for ticket {ticket_id}')
        for key in ('discoveryBasis', 'softwareVersion', 'createdAt', 'sourceLastModified', 'sourceFetchedAt'):
            if case.get(key) and not record.get(key):
                record[key] = case[key]
        method_tags = [METHOD_SCENARIOS[method] for method in (case.get('methodEvidence') or {})
                       if method in METHOD_SCENARIOS]
        values = (
            ('titles', [case.get('name', '')]),
            ('paths', [case.get('discoveryPath') or case.get('actions', '')]),
            ('functions', case.get('functions') or case.get('productAreas') or []),
            ('sourcePatterns', [pattern_id] if pattern_id else []),
            ('scenarioTags', [*pattern_tags, *method_tags]),
            ('attemptValues', [case.get('reproductionAttempts')]),
            ('lifecycleValues', [case.get('lifecycleRuns')]),
        )
        for key, items in values:
            for value in items:
                if value is not None and value != '' and value not in record[key]:
                    record[key].append(value)
    if 'freeTestEvidence' in data:
        full_ids = {str(case.get('defectId') or '').strip() for case in data['freeTestEvidence']}
        if len(full_ids) != data['coverage']['freeTestTickets']:
            raise ValueError('Full cohort count mismatch between coverage declaration and unique source IDs')
        if not complex_ids <= full_ids:
            raise ValueError('Complex candidate ID mismatch: subset contains tickets outside the full cohort')
    records = []
    for ticket_id in sorted(grouped):
        record = grouped[ticket_id]
        record['title'] = ' | '.join(record.pop('titles'))
        record['path'] = '\n'.join(record.pop('paths'))
        signals = _signals(record['title'], record['path'])
        title_codes = {signal['quality'] for signal in signals if signal['field'] == 'title'}
        all_codes = {signal['quality'] for signal in signals}
        preferred = title_codes or all_codes
        record['qualityPrimary'] = next((code for code in QUALITY_PRIORITY if code in preferred), 'UNCLASSIFIED')
        record['qualitySecondary'] = [code for code in QUALITY_PRIORITY
                                      if code in all_codes and code != record['qualityPrimary']]
        record['qualitySignals'] = signals
        record['scenarioTags'].sort()
        record['primaryScenario'] = next(iter(record['scenarioTags']), 'UNMAPPED')
        record.setdefault('discoveryBasis', 'Complex discovery-pattern candidate')
        record['reviewStatus'] = 'candidate_needs_review'
        record['effortConflicts'] = []
        for source, target in (('attemptValues', 'attempts'), ('lifecycleValues', 'lifecycles')):
            values = record.pop(source)
            valid = [value for value in values if isinstance(value, (int, float))
                     and not isinstance(value, bool) and value >= 0]
            record[target] = valid[0] if len(valid) == 1 else None
            if len(values) > 1:
                record['effortConflicts'].append(target)
        records.append(record)
    return {
        'records': records,
        'summary': {
            'unit': 'unique_candidate_ticket',
            'uniqueTickets': len(records),
            'sourceFreeTestTickets': data['coverage']['freeTestTickets'],
            'declaredCandidateTickets': library.get('candidateTicketCount'),
            'complexCandidateTickets': sum(record['inComplexSubset'] for record in records),
            'fullCohortAvailable': 'freeTestEvidence' in data,
            'cohortBasisCounts': dict(Counter(record['discoveryBasis'] for record in records)),
            'primaryQualityCounts': dict(Counter(record['qualityPrimary'] for record in records)),
            'primaryScenarioCounts': dict(Counter(record['primaryScenario'] for record in records)),
            'qualityScenarioCounts': dict(Counter(
                record['qualityPrimary'] + '|' + record['primaryScenario'] for record in records)),
        },
    }


SHEETS = {
    'guide': 'FT\u5206\u7c7b\u5bfc\u822a',
    'packages': 'FT\u8d28\u91cf\u5de5\u4f5c\u5305',
    'taxonomy': 'ISO\u8d28\u91cf\u53e3\u5f84',
    'scenarios': 'FT\u573a\u666f\u5b57\u5178',
    'checkpoints': 'Speech\u68c0\u67e5\u70b9',
    'evidence': 'FT\u7968\u636e\u5206\u7c7b',
    'summary': 'FT\u53cc\u7ef4\u7edf\u8ba1',
    'activity': 'FT\u6d3b\u52a8\u8bb0\u5f55',
    'sources': 'FT\u6765\u6e90\u4e0e\u8fb9\u754c',
}

CHARACTERISTICS = {
    'FS': 'Functional Suitability / \u529f\u80fd\u9002\u5408\u6027',
    'PE': 'Performance Efficiency / \u6027\u80fd\u6548\u7387',
    'CO': 'Compatibility / \u517c\u5bb9\u6027',
    'US': 'Usability / \u6613\u7528\u6027',
    'RE': 'Reliability / \u53ef\u9760\u6027',
    'SE': 'Security / \u4fe1\u606f\u5b89\u5168',
    'MA': 'Maintainability / \u53ef\u7ef4\u62a4\u6027',
    'PO': 'Portability / \u53ef\u79fb\u690d\u6027',
}

TAXONOMY = (
    ('FS.COMPLETE', 'Functional Completeness'), ('FS.CORRECT', 'Functional Correctness'),
    ('FS.APPROPRIATE', 'Functional Appropriateness'), ('PE.TIME', 'Time Behaviour'),
    ('PE.RESOURCE', 'Resource Utilization'), ('PE.CAPACITY', 'Capacity'),
    ('CO.COEXIST', 'Co-existence'), ('CO.INTEROP', 'Interoperability'),
    ('US.RECOGNIZE', 'Appropriateness Recognizability'), ('US.LEARN', 'Learnability'),
    ('US.OPERATE', 'Operability'), ('US.ERROR', 'User Error Protection'),
    ('US.AESTHETIC', 'User Interface Aesthetics'), ('US.ACCESS', 'Accessibility'),
    ('RE.MATURE', 'Maturity'), ('RE.AVAIL', 'Availability'),
    ('RE.FAULT', 'Fault Tolerance'), ('RE.RECOVER', 'Recoverability'),
    ('SE.CONF', 'Confidentiality'), ('SE.INTEGRITY', 'Integrity'),
    ('SE.NONREP', 'Non-repudiation'), ('SE.AUTH', 'Authenticity'),
    ('SE.ACCOUNT', 'Accountability'), ('MA.MODULAR', 'Modularity'),
    ('MA.REUSE', 'Reusability'), ('MA.ANALYZE', 'Analysability'),
    ('MA.MODIFY', 'Modifiability'), ('MA.TEST', 'Testability'),
    ('PO.ADAPT', 'Adaptability'), ('PO.INSTALL', 'Installability'),
    ('PO.REPLACE', 'Replaceability'),
)

QUALITY_LABELS.update({code: (CHARACTERISTICS[code.split('.')[0]], name)
                       for code, name in TAXONOMY})

QUALITY_PACKAGES = (
    ('FT-Q01', 'FS.CORRECT', 'Functional result and interaction correctness',
     'Validate intended customer results against a confirmed requirement or expected behaviour.',
     'Expected versus actual result; requirement/oracle reference; positive and negative variants.',
     'Executed checkpoint instances; pass/fail/no-finding; unique reviewed findings.',
     'Not a claim of complete requirements coverage; recognition accuracy needs an utterance denominator.'),
    ('FT-Q02', 'PE.TIME', 'Response and startup time behaviour',
     'Measure wake-up, app startup, speech response, route calculation or UI response time.',
     'Defined start/end events, elapsed value and unit, threshold source, version and conditions.',
     'Timed samples and distribution only when actual timing values exist.',
     'Rapid tapping is a trigger, not performance measurement; general stability belongs under Reliability.'),
    ('FT-Q03', 'CO.INTEROP', 'Cross-system and cross-device interoperability',
     'Validate exchanged commands, data and state across devices, ECUs, apps and ecosystems.',
     'Endpoint/version/configuration pair, exchanged information, expected consistency and actual result.',
     'Executed interface/configuration pairs; confirmed exchange/consistency outcomes.',
     'A phone or another app in the setup does not by itself establish an interoperability test.'),
    ('FT-Q04', 'RE.MATURE', 'Normal-use stability and maturity',
     'Repeat representative normal-use tasks and observe failure-free operation.',
     'Declared workload, duration/cycles, observation window and failure events.',
     'Observed cycles and failures; reliability rates require the complete denominator.',
     'Not inferred from defect totals or lifecycle answers; coupled correctness checks remain separately identified.'),
    ('FT-Q05', 'RE.AVAIL', 'Service and function availability',
     'Check whether a required function is executable at a defined observation point.',
     'Availability probes; unavailable intervals; black/yellow screen, hang or crash evidence.',
     'Executed availability probes and unavailable observations; uptime only with monitored time.',
     'A wrong functional result alone is not measured downtime or system unavailability.'),
    ('FT-Q06', 'RE.FAULT', 'Fault tolerance and degraded-condition behaviour',
     'Introduce a controlled faulty input, disrupted dependency or degraded resource condition.',
     'Injected condition, expected bounded/degraded behaviour, unaffected functions and exit conditions.',
     'Executed fault/condition combinations with observed containment results.',
     'Permission toggles are not automatically cybersecurity; do not claim system fault injection without evidence.'),
    ('FT-Q07', 'RE.RECOVER', 'Interruption, re-entry and state recovery',
     'Observe return to the expected state after an interruption, dependency restoration or restart.',
     'Pre-fault state, interruption, restoration, recovered state and recovery time if measured.',
     'Executed recovery paths; successful/failed recovery; timed recovery samples when available.',
     'Routine startup or a restart used only as setup is not a recovery test; distinguish automatic from manual recovery.'),
)

SCENARIO_PACKAGES = tuple(package for package in PACKAGES if package.code.startswith('FREE-'))
SCENARIO_CODES = tuple(package.code for package in SCENARIO_PACKAGES) + ('UNMAPPED',)
QUALITY_CODES = tuple(code for code, name in TAXONOMY) + ('UNCLASSIFIED',)
LOG_LAST_ROW = 1005

SPEECH_CHECKPOINTS = (
    ('Market', 'HK/MC right-hand driving activation and answer behaviour', 'FS.CORRECT', 'FREE-06'),
    ('Market', 'Market-specific supported app, e.g. Spotify', 'CO.INTEROP', 'FREE-06'),
    ('Dynamic Roadtest / dialogue', 'Recognition quality in the driving context', 'FS.CORRECT', 'FREE-06'),
    ('Dynamic Roadtest / dialogue', 'Activation rate with a declared trial denominator', 'FS.CORRECT', 'FREE-06'),
    ('Dynamic Roadtest / dialogue', 'Speech response performance with timed start/end events', 'PE.TIME', 'FREE-06'),
    ('Dynamic Roadtest / backend', 'Recovery from a bad network', 'RE.RECOVER', 'FREE-06'),
    ('Dynamic Roadtest / backend', 'Backend connection response performance', 'PE.TIME', 'FREE-06'),
    ('Special test on demand', 'Requested market-specific checkpoint', 'FS.CORRECT', 'FREE-06'),
    ('Special test on demand', 'New-feature checkpoint', 'FS.CORRECT', 'FREE-01'),
    ('Special test on demand', 'Existing feature risk-based checkpoint', 'FS.CORRECT', 'FREE-01'),
    ('Special test on demand', 'Speech KPI with declared metric and denominator', 'FS.CORRECT', 'FREE-01'),
    ('Compatibility / Cross Platform', 'Function in different UI versions', 'CO.INTEROP', 'FREE-03'),
    ('Compatibility / Cross Platform', 'Feedback across different devices', 'CO.INTEROP', 'FREE-03'),
    ('Compatibility / Cross Platform', 'Cross-platform car-control difference, including MINI', 'CO.INTEROP', 'FREE-03'),
    ('Cross Function / Telephony', 'SMS message operation', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / Telephony', 'Dial contact or telephone number', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / Telephony', 'Contact, mail and call-history selection', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / Climate control', 'Function on/off', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / Climate control', 'Temperature, fan, work mode and heating/cooling settings', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / Navigation', 'Map zoom and view change', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / Navigation', 'Guidance preferences and route query', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / Navigation', 'Home, office, POI, search and waypoint destination', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / Entertainment', 'Play media through an app', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / Entertainment', 'Play a channel or playlist', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / Entertainment', 'Radio by frequency, station name and favourites', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / Entertainment', 'Volume, mute/unmute and source volume', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / Entertainment', 'Skip, next/previous, seek and playback position', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / Vehicle control', 'Window and roof opening/closing', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / Vehicle control', 'Driving-mode and ADAS settings', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / Vehicle control', 'Interior and ambient light', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / Vehicle info', 'Fuel/battery, energy consumption and other vehicle information', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / Vehicle info', 'Tyre pressure, maintenance and CCM status', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / Weather', 'Weather by location, broadcast and day', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / Launcher', 'Open desktop', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / CN Store', 'Search and install app', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / FA', 'Festival greeting and open FA app', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / MPP-WeChat', 'Open and close MPP services', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / MPP-WeChat', 'Open or log in to WeChat', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / MPP-WeChat', 'Make a WeChat call', 'FS.CORRECT', 'FREE-01'),
    ('Cross Function / MPP-WeChat', 'Send and read a WeChat message', 'FS.CORRECT', 'FREE-01'),
    ('Cross System / basic component', 'Wave basic data connectivity', 'CO.INTEROP', 'FREE-03'),
    ('Cross System / Pad', 'Fold/unfold and app opening', 'CO.INTEROP', 'FREE-03'),
    ('Cross System / MFL', 'Steering-wheel PTT activation and speech volume', 'CO.INTEROP', 'FREE-03'),
    ('Cross System / CID', 'System popup and touch interaction', 'CO.INTEROP', 'FREE-04'),
    ('Cross System / audio interaction', 'Speech-dialog audio channel use', 'CO.INTEROP', 'FREE-04'),
    ('Cross System / audio interaction', 'TTS from navigation, weather and other apps', 'CO.INTEROP', 'FREE-04'),
    ('Cross System / audio interaction', 'Other voice assistants: WeChat and CarPlay Siri', 'CO.INTEROP', 'FREE-04'),
    ('Cross System / audio interaction', 'Call/service call, media, phone call and PDC/Gong', 'CO.INTEROP', 'FREE-04'),
    ('Cross System / car functions', 'CCM popup overlay during speech', 'FS.CORRECT', 'FREE-04'),
    ('Cross System / car functions', 'Reverse-camera overlay during speech', 'FS.CORRECT', 'FREE-04'),
    ('Functional Regression / basic framework', 'PTT and wake-up-word activation', 'FS.CORRECT', 'FREE-01'),
    ('Functional Regression / basic framework', 'Hotword commands', 'FS.CORRECT', 'FREE-01'),
    ('Functional Regression / error handling', 'Padding response', 'FS.CORRECT', 'FREE-01'),
    ('Functional Regression / error handling', 'Automatic timeout', 'FS.CORRECT', 'FREE-04'),
    ('Functional Regression / cancellation', 'PTT and cancel commands', 'FS.CORRECT', 'FREE-04'),
    ('Functional Regression / TTS', 'TTS quality and feedback', 'FS.CORRECT', 'FREE-01'),
    ('Functional Regression / STT', 'Text and app appearance', 'FS.CORRECT', 'FREE-01'),
    ('Functional Regression / STT', 'STT display speed with timed samples', 'PE.TIME', 'FREE-01'),
    ('Functional Regression / STT', 'Text accuracy against the spoken utterance', 'FS.CORRECT', 'FREE-01'),
    ('Functional Regression / Say-What-You-See', 'Open page in local or third-party app; Mandarin only', 'FS.CORRECT', 'FREE-01'),
    ('Functional Regression / Say-What-You-See', 'Menu selection in local or third-party app; Mandarin only', 'FS.CORRECT', 'FREE-01'),
    ('Functional Regression / Say-What-You-See', 'Button confirmation in local or third-party app; Mandarin only', 'FS.CORRECT', 'FREE-01'),
    ('Functional Regression / snoozing', 'Continuous input and timeout; Mandarin only', 'FS.CORRECT', 'FREE-04'),
    ('Functional Regression / language switching', 'TTS feedback and command recognition', 'FS.CORRECT', 'FREE-02'),
    ('Functional Regression / language switching', 'STT display and wake-up-word change', 'FS.CORRECT', 'FREE-02'),
    ('Functional Regression / online functions', 'Calculation, jokes and other supported online services', 'FS.CORRECT', 'FREE-01'),
    ('Functional Regression / online functions', 'Information query, e.g. stocks and flights', 'FS.CORRECT', 'FREE-01'),
    ('Functional Regression / dialogue ability', 'Seat distinction: front/rear and left/right', 'FS.CORRECT', 'FREE-03'),
    ('Functional Regression / dialogue ability', 'Barge-in', 'FS.CORRECT', 'FREE-04'),
    ('Functional Regression / dialogue ability', 'NLU and multiple-function understanding', 'FS.CORRECT', 'FREE-01'),
    ('Functional Regression / dialogue ability', 'Context understanding and chitchat', 'FS.CORRECT', 'FREE-02'),
    ('IPA App / speech options', 'Speech language, function toggle and BMW brand voice', 'FS.CORRECT', 'FREE-02'),
)


def _location(sheet: str, address: str = 'A1') -> str:
    return "'" + sheet.replace("'", "''") + "'!" + address


def _table(workbook, key: str, headers: list[str], rows: list, note: str):
    sheet = workbook.create_sheet(SHEETS[key], 0 if key == 'guide' else None)
    last_column = max(4, len(headers))
    for row, text in ((1, SHEETS[key]), (2, note)):
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=last_column)
        sheet.cell(row, 1, text)
        sheet.cell(row, 1).font = Font(name='Calibri', size=16 if row == 1 else 11, bold=row == 1,
                                      color='FFFFFF' if row == 1 else '172B3A')
        sheet.cell(row, 1).fill = PatternFill('solid', fgColor='1F4E78' if row == 1 else 'E8F0F8')
        sheet.cell(row, 1).alignment = Alignment(wrap_text=True, vertical='center')
    sheet.row_dimensions[1].height = 30
    sheet.row_dimensions[2].height = 66
    sheet['A4'] = 'Back to quality navigator'
    sheet['A4'].hyperlink = Hyperlink(ref='A4', location=_location(SHEETS['guide']))
    sheet['A4'].font = Font(name='Calibri', size=10, color='0563C1', underline='single')
    for column, header in enumerate(headers, 1):
        cell = sheet.cell(5, column, header)
        cell.font = Font(name='Calibri', size=10, bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='1F4E78')
        cell.alignment = Alignment(wrap_text=True, vertical='center')
        sheet.column_dimensions[get_column_letter(column)].width = 28 if column != 1 else 20
    sheet.row_dimensions[5].height = 44
    for row_number, values in enumerate(rows, 6):
        for column, value in enumerate(values, 1):
            cell = sheet.cell(row_number, column, _literal(value))
            cell.font = Font(name='Calibri', size=10, color='172B3A')
            cell.alignment = Alignment(wrap_text=True, vertical='top')
            cell.fill = PatternFill('solid', fgColor='F1F6FA' if row_number % 2 == 0 else 'FFFFFF')
        sheet.row_dimensions[row_number].height = 54 if key != 'evidence' else 42
    sheet.freeze_panes = 'C6'
    sheet.sheet_view.showGridLines = False
    sheet.sheet_view.zoomScale = 85
    sheet.auto_filter.ref = f'A5:{get_column_letter(len(headers))}{max(5, sheet.max_row)}'
    sheet.print_title_rows = '1:5'
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    return sheet


def _named_list(workbook, name: str, sheet, column: str, first: int, last: int):
    if name in workbook.defined_names:
        raise ValueError(f'Existing workbook already defines {name}; refusing to replace it')
    workbook.defined_names.add(DefinedName(name, attr_text=_location(sheet.title, f'${column}${first}:${column}${last}')))


def _dropdown(sheet, address: str, formula: str):
    rule = DataValidation(type='list', formula1=formula, allow_blank=True)
    rule.errorTitle = 'Select a defined value'
    rule.error = 'Use a value from the classification dictionary.'
    rule.showErrorMessage = True
    rule.errorStyle = 'stop'
    sheet.add_data_validation(rule)
    rule.add(address)


def _activity_log(workbook):
    headers = ['Activity ID', 'Session ID', 'Date (UTC+8)', 'Primary work type', 'Z stage',
               'Quality WP', 'Quality code (auto)', 'Primary scenario', 'Function', 'Speech checkpoint ID',
               'Market / platform / conditions', 'Result', 'Checkpoint executions', 'Active minutes',
               'Waiting minutes', 'Linked ticket ID', 'Evidence / oracle / timing value and unit',
               'Row validation (auto)', 'Executed session credit (auto)', 'Z execution units (auto)']
    sheet = _table(workbook, 'activity', headers, [],
                   'One row = one non-overlapping activity. Reuse a Session ID only for the same quality/scenario/work-type target; use a new session when the target changes. Z.02 is discovery execution. PASS/FAIL/NO_FINDING are executed outcomes; BLOCKED/NOT_RUN are not. Active and waiting minutes stay separate. No historical hours or sessions are prefilled.')
    _dropdown(sheet, f'D6:D{LOG_LAST_ROW}', '"Z,Y,V,W,AA,AB,AI"')
    _dropdown(sheet, f'E6:E{LOG_LAST_ROW}', '"Z.01,Z.02,Z.03,Z.04,Z.05"')
    _dropdown(sheet, f'F6:F{LOG_LAST_ROW}', '=FTQ_WorkPackages')
    _dropdown(sheet, f'H6:H{LOG_LAST_ROW}', '=FTQ_Scenarios')
    _dropdown(sheet, f'J6:J{LOG_LAST_ROW}', '=FTQ_Checkpoints')
    _dropdown(sheet, f'L6:L{LOG_LAST_ROW}', '"PASS,FAIL,NO_FINDING,BLOCKED,NOT_RUN"')
    for column in ('M', 'N', 'O'):
        rule = DataValidation(type='whole' if column == 'M' else 'decimal', operator='greaterThanOrEqual',
                              formula1=0, allow_blank=True)
        rule.showErrorMessage = True
        rule.errorStyle = 'stop'
        sheet.add_data_validation(rule)
        rule.add(f'{column}6:{column}{LOG_LAST_ROW}')
    for row in range(6, LOG_LAST_ROW + 1):
        sheet.cell(row, 7, f'=IF(F{row}="","",IFERROR(VLOOKUP(F{row},FTQ_PackageLookup,2,FALSE),"UNCLASSIFIED"))')
        conflict = ','.join(
            f'COUNTIFS($B$6:$B${LOG_LAST_ROW},B{row},${column}$6:${column}${LOG_LAST_ROW},"<>"&{column}{row})>0'
            for column in ('D', 'F', 'H'))
        executed = f'OR(L{row}="PASS",L{row}="FAIL",L{row}="NO_FINDING")'
        invalid_work = ','.join(f'D{row}<>"{code}"' for code in ('Z', 'Y', 'V', 'W', 'AA', 'AB', 'AI'))
        allowed_stages = ','.join(f'E{row}="Z.0{index}"' for index in range(1, 6))
        bad_numbers = ','.join(f'OR(AND({column}{row}<>"",COUNT({column}{row})<>1),{column}{row}<0)'
                               for column in ('M', 'N', 'O'))
        checks = [
            (f'OR(A{row}="",B{row}="",C{row}="",D{row}="",F{row}="",H{row}="",I{row}="")', 'INCOMPLETE'),
            (f'OR(AND({invalid_work}),COUNTIF(FTQ_WorkPackages,F{row})<>1,COUNTIF(FTQ_Scenarios,H{row})<>1,'
             f'COUNT(C{row})<>1,C{row}<1,AND(J{row}<>"",COUNTIF(FTQ_Checkpoints,J{row})<>1),'
             f'AND(L{row}<>"",NOT(OR({executed},L{row}="BLOCKED",L{row}="NOT_RUN"))))', 'INVALID_VALUE'),
            (f'OR({bad_numbers})', 'NEGATIVE_OR_NONNUMERIC'),
            (f'IF(ISNUMBER(M{row}),MOD(M{row},1)<>0,FALSE)', 'FRACTIONAL_EXECUTION_UNITS'),
            (f'COUNTIF($A$6:$A${LOG_LAST_ROW},A{row})<>1', 'DUPLICATE_ACTIVITY'),
            (f'OR({conflict})', 'SESSION_TARGET_CONFLICT'),
            (f'OR(AND(D{row}="Z",NOT(OR({allowed_stages}))),AND(D{row}<>"Z",E{row}<>""))', 'CHECK_STAGE'),
            (f'AND(D{row}="Z",E{row}="Z.02",L{row}="")', 'NEEDS_OUTCOME'),
            (f'AND(D{row}="Z",E{row}="Z.02",{executed},OR(M{row}="",M{row}<=0))', 'NEEDS_EXECUTION_UNITS'),
            (f'AND(M{row}>0,NOT(AND(D{row}="Z",E{row}="Z.02",{executed})))', 'CHECK_EXECUTION_UNITS'),
        ]
        validation = '"OK"'
        for condition, status in reversed(checks):
            validation = f'IF({condition},"{status}",{validation})'
        sheet.cell(row, 18, f'=IF(COUNTA(A{row}:F{row},H{row}:Q{row})=0,"",{validation})')
        session_counts = '+'.join(
            f'COUNTIFS($B$6:B{row},B{row},$D$6:D{row},"Z",$E$6:E{row},"Z.02",'
            f'$R$6:R{row},"OK",$L$6:L{row},"{outcome}")'
            for outcome in ('PASS', 'FAIL', 'NO_FINDING'))
        sheet.cell(row, 19,
                   f'=IF(AND(R{row}="OK",D{row}="Z",E{row}="Z.02",{executed}),'
                   f'IF({session_counts}=1,1,0),"")')
        sheet.cell(row, 20, f'=IF(AND(R{row}="OK",D{row}="Z",E{row}="Z.02",{executed}),M{row},"")')
        sheet.cell(row, 3).number_format = 'yyyy-mm-dd'
        for column in range(1, 21):
            cell = sheet.cell(row, column)
            cell.fill = PatternFill('solid', fgColor='EDF2F5' if column in (7, 18, 19, 20) else 'FFF2CC')
            cell.font = Font(name='Calibri', size=10)
    sheet.auto_filter.ref = f'A5:T{LOG_LAST_ROW}'
    sheet.freeze_panes = 'D6'
    return sheet


def _statistics(workbook, result: dict, evidence_last_row: int):
    summary = result['summary']
    headers = ['Quality code', 'Quality characteristic', 'Sub-characteristic', *SCENARIO_CODES, 'Unique tickets']
    rows = [[code, *QUALITY_LABELS[code],
             *[summary['qualityScenarioCounts'].get(code + '|' + scenario, 0) for scenario in SCENARIO_CODES],
             summary['primaryQualityCounts'].get(code, 0)] for code in QUALITY_CODES]
    sheet = _table(workbook, 'summary', headers, rows,
                   'TOP GRID = frozen, deduplicated ticket-candidate snapshot, NOT execution volume or hours. LOWER GRID = editable-review classification with cohort/function filters in D4/G4. Zero means no matching evidence signal, not zero testing. Multi-label tags are non-additive. Future activity grids remain blank until valid FreeTest activities exist.')
    sheet['C4'], sheet['D4'], sheet['F4'], sheet['G4'] = 'Review grid cohort', 'ALL', 'Function contains', 'ALL'
    _dropdown(sheet, 'D4', '"ALL,EXPLICIT,INFERRED,COMPLEX"')
    total_row = 6 + len(QUALITY_CODES)
    sheet.cell(total_row, 1, 'TOTAL UNIQUE CANDIDATES')
    for column in range(4, len(headers) + 1):
        sheet.cell(total_row, column, sum(row[column - 1] for row in rows))
    current = total_row + 3
    sheet.cell(current, 1, 'REVIEW / FILTER GRID: one effective primary quality and scenario per unique ticket')
    current += 1
    for column, header in enumerate(headers, 1):
        sheet.cell(current, column, header)
    details = _location(SHEETS['evidence']).split('!')[0]
    end = max(6, evidence_last_row)
    common = (
        f'{details}!$D$6:$D${end},IF($D$4="EXPLICIT","Explicit Free-test report reference",'
        f'IF($D$4="INFERRED","No testcase link (Free test by policy)","*")),'
        f'{details}!$E$6:$E${end},IF($D$4="COMPLEX","YES","*")')
    for code in QUALITY_CODES:
        current += 1
        for column, value in enumerate((code, *QUALITY_LABELS[code]), 1):
            sheet.cell(current, column, value)
        for column, scenario in enumerate(SCENARIO_CODES, 4):
            criteria = (f'{details}!$H$6:$H${end},$A{current},'
                        f'{details}!$L$6:$L${end},"{scenario}",{common}')
            sheet.cell(current, column,
                       f'=IF(OR($G$4="ALL",$G$4=""),COUNTIFS({criteria}),'
                       f'COUNTIFS({criteria},{details}!$C$6:$C${end},"*"&$G$4&"*"))')
        sheet.cell(current, len(headers), f'=SUM(D{current}:J{current})')
    current += 1
    sheet.cell(current, 1, 'TOTAL FILTERED CANDIDATES')
    for column in range(4, len(headers) + 1):
        letter = get_column_letter(column)
        sheet.cell(current, column, f'=SUM({letter}{current - len(QUALITY_CODES)}:{letter}{current - 1})')
    log = _location(SHEETS['activity']).split('!')[0]
    has_executions = (f'COUNTIFS({log}!$R$6:$R${LOG_LAST_ROW},"OK",'
                      f'{log}!$D$6:$D${LOG_LAST_ROW},"Z",{log}!$T$6:$T${LOG_LAST_ROW},">0")')
    package_codes = [package[1] for package in QUALITY_PACKAGES] + ['UNCLASSIFIED']
    for metric, value_column in (('FUTURE: executed FreeTest checkpoint instances', 'T'),
                                 ('FUTURE: unique executed FreeTest sessions', 'S'),
                                 ('FUTURE: active FreeTest minutes, recorded values only; waiting excluded', 'N'),
                                 ('FUTURE: waiting FreeTest minutes, recorded values only; never added to active', 'O')):
        current += 3
        sheet.cell(current, 1, metric)
        current += 1
        for column, header in enumerate(headers, 1):
            sheet.cell(current, column, header)
        for code in package_codes:
            current += 1
            for column, value in enumerate((code, *QUALITY_LABELS[code]), 1):
                sheet.cell(current, column, value)
            for column, scenario in enumerate(SCENARIO_CODES, 4):
                criteria = (f'{log}!$R$6:$R${LOG_LAST_ROW},"OK",{log}!$D$6:$D${LOG_LAST_ROW},"Z",'
                            f'{log}!$G$6:$G${LOG_LAST_ROW},$A{current},{log}!$H$6:$H${LOG_LAST_ROW},"{scenario}"')
                available = (f'COUNTIFS({criteria},{log}!${value_column}$6:${value_column}${LOG_LAST_ROW},"<>")'
                             if value_column in ('N', 'O') else has_executions)
                sheet.cell(current, column,
                           f'=IF({available}=0,"",SUMIFS({log}!${value_column}$6:${value_column}${LOG_LAST_ROW},{criteria}))')
            sheet.cell(current, len(headers), f'=IF(COUNT(D{current}:J{current})=0,"",SUM(D{current}:J{current}))')
    for row in sheet.iter_rows(min_row=total_row, max_row=current, max_col=len(headers)):
        for cell in row:
            cell.font = Font(name='Calibri', size=10, bold=cell.column == 1)
            cell.alignment = Alignment(wrap_text=True, vertical='top')
    sheet.column_dimensions['B'].width = 37
    sheet.column_dimensions['C'].width = 30
    for column in range(4, len(headers) + 1):
        sheet.column_dimensions[get_column_letter(column)].width = 17
    return sheet


def build_quality_scope_workbook(source_path, evidence_path, output_path) -> dict:
    source_path, evidence_path, output_path = map(Path, (source_path, evidence_path, output_path))
    if output_path.resolve() in {source_path.resolve(), evidence_path.resolve()}:
        raise ValueError('Refusing to overwrite an input file')
    if output_path.exists():
        raise FileExistsError(f'Output already exists: {output_path}')
    source_hash = sha256(source_path.read_bytes()).hexdigest()
    evidence_hash = sha256(evidence_path.read_bytes()).hexdigest()
    data = json.loads(evidence_path.read_text(encoding='utf-8'))
    result = classify_free_test_evidence(data)
    workbook = load_workbook(source_path)
    if any(title in workbook.sheetnames for title in SHEETS.values()):
        workbook.close()
        raise ValueError('Quality views already exist; refusing to replace user classifications')
    if not any(cell.value == 'FREE-01' for sheet in workbook for row in sheet for cell in row):
        workbook.close()
        raise ValueError('The source must contain the existing FreeTest work-package matrix')
    original_sheets = list(workbook.sheetnames)
    in_scope = {package[1]: package[0] for package in QUALITY_PACKAGES}
    taxonomy_rows = [[code, *QUALITY_LABELS[code], in_scope.get(code, ''),
                      'CN screenshot: designed test direction; package mapping proposed' if code in in_scope else
                      'Not individually confirmed by the supplied CN scope; review before claiming coverage']
                     for code in QUALITY_CODES]
    taxonomy = _table(workbook, 'taxonomy',
                      ['Quality code', 'Characteristic', 'Sub-characteristic', 'Proposed quality WP', 'Scope status'],
                      taxonomy_rows,
                      'Historical eight-characteristic ISO/IEC 25010:2011 product-quality model in the ISO/IEC 25000 SQuaRE family, matching the supplied figure. This is not a 2023-model migration or certification. Blank CN scope checkboxes are not evidence that no testing ever occurred. Security means information security, not emergency-call/CCM safety checks.')
    taxonomy.column_dimensions['E'].width = 68
    _named_list(workbook, 'FTQ_QualityCodes', taxonomy, 'A', 6, 37)
    package_rows = [[wp_id, code, QUALITY_LABELS[code][0], QUALITY_LABELS[code][1], title, objective, oracle,
                     'Z.01 -> Z.02 -> Z.03 -> Z.04; Z.05 only when tooling actually occurs', unit, boundary]
                    for wp_id, code, title, objective, oracle, unit, boundary in QUALITY_PACKAGES]
    packages = _table(workbook, 'packages',
                      ['Quality WP ID', 'Quality code', 'Characteristic', 'Sub-characteristic', 'Quality-led package',
                       'Objective', 'Reviewable evidence / oracle', 'Activity stages (independent axis)', 'Measurement unit', 'Boundary'],
                      package_rows,
                      'Seven proposed quality-led work packages based on the supplied CN scope. FREE-01..06 remain reusable scenario/method tags; Z.01..05 remain effort stages. A quality WP is a target, not an extra parallel allocation. Choose one primary quality/scenario per activity; secondary labels never add hours. All proposed package mappings require scope-owner review.')
    packages.append(['FT-REVIEW', 'UNCLASSIFIED', 'Needs Review', 'Unconfirmed quality target',
                     'Record the activity without inventing a quality objective'])
    packages.auto_filter.ref = 'A5:J13'
    _named_list(workbook, 'FTQ_WorkPackages', packages, 'A', 6, 13)
    workbook.defined_names.add(DefinedName('FTQ_PackageLookup', attr_text=_location(packages.title, '$A$6:$B$13')))
    scenario_rows = [[package.code, package.name, package.output, package.boundary,
                      'Use with any quality objective when the checkpoint supports that objective'] for package in SCENARIO_PACKAGES]
    scenario_rows.append(['UNMAPPED', 'Needs scenario review', 'Insufficient scenario evidence',
                          'Do not infer a road trip, execution event or recovery test from a ticket title', 'Review required'])
    scenarios = _table(workbook, 'scenarios',
                       ['Scenario ID', 'Existing scenario/method family', 'Expected evidence', 'Existing boundary', 'Relationship to quality'],
                       scenario_rows,
                       'Existing FREE identifiers and meanings are preserved. Historical primary scenario is a deterministic review proposal from pattern/method evidence: lowest FREE code wins when multiple tags exist. All other tags remain visible and non-additive. This algorithmic primary is not the original tester\'s declared objective. Market/platform/function are separate context fields.')
    _named_list(workbook, 'FTQ_Scenarios', scenarios, 'A', 6, 12)
    checkpoint_rows = [[f'SP-{index:03d}', branch, checkpoint, quality, in_scope[quality], scenario,
                        'Source-image checkpoint; ISO and scenario mapping proposed, not execution evidence',
                        'Record preconditions, input/trigger, expected/actual result and source oracle; add timing/denominator if required']
                       for index, (branch, checkpoint, quality, scenario) in enumerate(SPEECH_CHECKPOINTS, 1)]
    checkpoints = _table(workbook, 'checkpoints',
                         ['Checkpoint ID', 'Original branch', 'Checkpoint / stimulus', 'Proposed primary quality',
                          'Proposed quality WP', 'Suggested scenario', 'Review status', 'Proposed evidence requirement'],
                         checkpoint_rows,
                         'Transcribed/grouped from user-supplied image 3 (DTSV CN Speech Testing Scope); retain the original branch as context. Each row is a checkpoint template, not an executed test or an exhaustive certified leaf count. Other quality objectives may reuse the same checkpoint with a different oracle. The Confluence XMind download returned HTML, so it was NOT used as a parsed source.')
    _named_list(workbook, 'FTQ_Checkpoints', checkpoints, 'A', 6, checkpoints.max_row)
    evidence_rows = []
    for record in result['records']:
        evidence_rows.append([
            record['ticketId'], record['title'], ' | '.join(record['functions']), record['discoveryBasis'],
            'YES' if record['inComplexSubset'] else 'NO', record['qualityPrimary'], None, None, None,
            record['primaryScenario'], None, None, ' | '.join(record['qualitySecondary']),
            ' | '.join(record['scenarioTags']), ' | '.join(record['sourcePatterns']), record['path'][:12000],
            '\n'.join(signal['quality'] + ' / ' + signal['field'] + ': ' + signal['fragment']
                      for signal in record['qualitySignals']),
            record['attempts'], record['lifecycles'], record.get('softwareVersion', ''),
            record.get('createdAt', ''), record.get('sourceLastModified', ''), record.get('sourceFetchedAt', ''),
            None, None, ' | '.join(record['effortConflicts']),
        ])
    details = _table(workbook, 'evidence',
                     ['Ticket ID', 'Original title', 'Functions / product areas', 'Original discovery basis', 'Complex subset',
                      'Suggested quality', 'Reviewed quality (input)', 'Effective quality (auto)', 'Characteristic (auto)',
                      'Suggested primary scenario', 'Reviewed scenario (input)', 'Effective scenario (auto)',
                      'Secondary quality tags', 'Scenario tags (non-additive)', 'Discovery patterns', 'Original action / discovery path',
                      'Matched evidence fragments', 'Reported attempts (not events)', 'Reported LC answers (not time)',
                      'Software version', 'Ticket created at (not execution date)', 'Source last modified', 'Source fetched at',
                      'Classification review (auto)', 'Reviewer / decision note (input)', 'Effort field conflicts'],
                     evidence_rows,
                     'One row per unique frozen candidate ticket. Suggested quality is a conservative symptom/action signal, NOT proof of the original test objective, completed coverage or an ISO-compliant test. Title evidence takes precedence over action-only signals. Yellow fields allow owner review; primary totals use effective classifications. Explicit FreeTest and policy-inferred records remain separate. No LLM, source synchronization or effort measurement was performed.')
    _dropdown(details, f'G6:G{max(6, details.max_row)}', '=FTQ_QualityCodes')
    _dropdown(details, f'K6:K{max(6, details.max_row)}', '=FTQ_Scenarios')
    for row in range(6, details.max_row + 1):
        details.cell(row, 8, f'=IF(G{row}="",F{row},IF(COUNTIF(FTQ_QualityCodes,G{row})=1,G{row},"UNCLASSIFIED"))')
        details.cell(row, 9, f'=IFERROR(VLOOKUP(H{row},{_location(taxonomy.title, "$A$6:$C$37")},2,FALSE),"Needs Review")')
        details.cell(row, 12, f'=IF(K{row}="",J{row},IF(COUNTIF(FTQ_Scenarios,K{row})=1,K{row},"UNMAPPED"))')
        details.cell(row, 24,
                     f'=IF(OR(AND(G{row}<>"",COUNTIF(FTQ_QualityCodes,G{row})<>1),'
                     f'AND(K{row}<>"",COUNTIF(FTQ_Scenarios,K{row})<>1)),"INVALID_REVIEW",'
                     f'IF(AND(G{row}<>"",K{row}<>"",Y{row}<>""),"Reviewed","Candidate: needs review"))')
        for column in (7, 11, 25):
            details.cell(row, column).fill = PatternFill('solid', fgColor='FFF2CC')
    details.column_dimensions['B'].width = 64
    details.column_dimensions['C'].width = 46
    details.column_dimensions['P'].width = 76
    details.column_dimensions['Q'].width = 76
    details.freeze_panes = 'F6'
    _activity_log(workbook)
    _statistics(workbook, result, details.max_row)
    scope = data.get('scope', {})
    sources = _table(workbook, 'sources', ['Source / rule', 'Version / observation', 'Location / value', 'Use / limitation'], [
        ['Original navigator', 'Unmodified bytes', str(source_path), source_hash],
        ['Frozen ticket evidence', scope.get('generatedAt', ''), str(evidence_path), evidence_hash],
        ['Frozen original cohort', scope.get('cohortGeneratedAt', ''), str(scope.get('classification', {})), 'No new source synchronization'],
        ['CN scope mindmap', 'User-supplied image 1, 2026-11-19', 'DTSV CN Test Scope', 'Source scope branches, not measured execution coverage'],
        ['CN quality table', 'User-supplied image 2, 2026-11-19', 'FS, PE, CO and RE checked', 'Seven listed sub-characteristics form proposed quality packages'],
        ['Speech scope', 'User-supplied image 3, 2026-11-19', 'DTSV CN Speech Testing Scope', 'Manual transcription/grouping; ISO mappings are proposals'],
        ['DTSV CN Test Strategy - Validation Beyond', 'v89; modified 2025-03-13; read 2026-11-19',
         'https://atc.bmwgroup.net/confluence/pages/viewpage.action?pageId=4447601857',
         'Body still says Drating. DE-61 table marks interoperability n; do not substitute that German scope for the supplied CN screenshot'],
        ['CN SPEECH Check Point', 'v11; modified 2024-10-15; read 2026-11-19',
         'https://atc.bmwgroup.net/confluence/pages/viewpage.action?pageId=4897320981',
         'Lists old scope XMind attachment 4897321810 v2 and a newer 2024 checklist; download content was HTML, not valid XMind'],
        ['Speech Checklist', 'v5; modified 2026-05-13; read 2026-11-19',
         'https://atc.bmwgroup.net/confluence/pages/viewpage.action?pageId=8247353020',
         'Separate RC Approval workbook exists; its contents were not read or merged'],
        ['Historical classification', 'Rules v1: candidate_needs_review', 'Title first; then action evidence; unknown retained',
         'One primary quality and scenario; overlapping secondary labels are never added to primary totals'],
        ['Standard version', 'ISO/IEC 25010:2011', 'ISO/IEC 25000 SQuaRE family',
         'Eight characteristics and 31 sub-characteristics; 2023 model intentionally not substituted for the supplied historical figure'],
        ['Counting units', 'Independent denominators', 'Ticket / session / checkpoint execution / active minutes / waiting minutes',
         'No defect-per-hour, coverage percentage or reliability rate without a compatible measured denominator'],
        ['Safety versus security', 'Terminology normalization', 'Emergency call / CCM != cybersecurity; general stability != elapsed performance',
         'Formal functional safety, penetration testing and certification are not established by these figures'],
        ['Z.01', 'Activity stage', 'Exploration goals and scenario design', 'Do not duplicate M/N/P planning effort'],
        ['Z.02', 'Activity stage', 'Exploration setup and discovery execution', 'Execution units counted only for explicit executed outcomes'],
        ['Z.03', 'Activity stage', 'Pre-ticket reproduction and trigger isolation', 'Post-ticket work remains AB; reported attempts/LC are not hours'],
        ['Z.04', 'Activity stage', 'Exploration evidence and regression suggestions', 'Formal ticket creation remains AA; testcase authoring remains N'],
        ['Z.05', 'Activity stage', 'FreeTest-specific tooling, only if actually performed', 'Use either Z or AI for the same tool-development time, never both'],
    ], 'Sources and evidence boundaries. No original workbook, SQLite record, Octane record or Confluence page was changed. Current page observation dates do not refresh the frozen 2026-10-28 ticket evidence.')
    for row in sources.iter_rows(min_row=6):
        if isinstance(row[2].value, str) and row[2].value.startswith('https://'):
            row[2].hyperlink = row[2].value
    summary = result['summary']
    guide_rows = [
        ['1. Quality / \u8d28\u91cf\u7c7b\u578b', 'What quality outcome is being tested?', SHEETS['packages'], 'Seven proposed quality-led execution packages; one primary target'],
        ['2. Scenario / \u6d4b\u8bd5\u573a\u666f', 'Under which input/state/device/timing/recovery/customer condition?', SHEETS['scenarios'], 'Keep FREE-01..06 as reusable scenario/method labels, not competing quality categories'],
        ['3. Activity / \u5de5\u4f5c\u9636\u6bb5', 'Which work was actually performed?', SHEETS['activity'], 'Z.01..05 describe effort stages, not quality types; no allocation is auto-invented'],
        ['Speech checkpoints', f'{len(SPEECH_CHECKPOINTS)} transcribed/grouped checkpoint templates', SHEETS['checkpoints'], 'Original branch, proposed quality target, scenario and required observation'],
        ['Historical ticket candidates', f'{summary["uniqueTickets"]} unique rows; {summary["complexCandidateTickets"]} complex-path subset', SHEETS['evidence'], 'Review suggested primary classifications; retain explicit versus policy-inferred cohort basis'],
        ['Quality x Scenario', 'Ticket candidates now; actual sessions/executions/minutes only after recording', SHEETS['summary'], 'Frozen grid plus live review filters; future log summaries remain blank when unmeasured'],
        ['ISO dictionary', '8 characteristics / 31 sub-characteristics + UNCLASSIFIED', SHEETS['taxonomy'], 'The eight-characteristic figure is ISO/IEC 25010:2011, not a generic ISO 25000 coverage certificate'],
        ['Sources and boundaries', 'Frozen evidence and observed Confluence versions', SHEETS['sources'], 'CN screenshot versus DE-61 draft-scope difference is preserved; attachment HTML was not used'],
        ['Original workbook', 'Original 55-package matrix and navigation remain unchanged', original_sheets[0], 'Legacy matrix is the activity/method view; the new quality packages are an orthogonal view, not extra effort'],
    ]
    guide = _table(workbook, 'guide', ['Dimension / entry', 'Meaning / available evidence', 'Open sheet', 'Counting rule'], guide_rows,
                   '\u63a8\u8350\uff1a\u8d28\u91cf\u7c7b\u578b \u00d7 \u6d4b\u8bd5\u573a\u666f \u00d7 \u5de5\u4f5c\u9636\u6bb5\u3002 Keep the three dimensions independent. Existing FREE-01..06 are scenario/method families; new FT-Q01..07 are quality objectives; Z.01..05 are effort stages. Never add these views together as separate workload.')
    for row in range(6, guide.max_row + 1):
        target = guide.cell(row, 3).value
        guide.cell(row, 3).hyperlink = Hyperlink(ref=f'C{row}', location=_location(target))
        guide.cell(row, 3).font = Font(name='Calibri', size=11, color='0563C1', underline='single')
    guide.column_dimensions['A'].width = 34
    guide.column_dimensions['B'].width = 60
    guide.column_dimensions['C'].width = 28
    guide.column_dimensions['D'].width = 84
    workbook.calculation = CalcProperties(calcMode='auto', fullCalcOnLoad=True)
    workbook.active = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output_path.open('xb') as handle:
            workbook.save(handle)
    finally:
        workbook.close()
    if sha256(source_path.read_bytes()).hexdigest() != source_hash or sha256(evidence_path.read_bytes()).hexdigest() != evidence_hash:
        raise RuntimeError('A source changed during generation; output must be reviewed')
    return {'outputPath': str(output_path), 'sourceSha256': source_hash, 'evidenceSha256': evidence_hash,
            'qualityPackageCount': len(QUALITY_PACKAGES), 'qualityCharacteristicCount': len(CHARACTERISTICS),
            'checkpointTemplateCount': len(SPEECH_CHECKPOINTS), 'summary': summary,
            'generatedAt': datetime.now(timezone.utc).isoformat()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--evidence', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(build_quality_scope_workbook(args.source, args.evidence, args.output), ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()