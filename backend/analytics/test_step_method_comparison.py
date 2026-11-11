"""Read-only comparison of actual testcase scripts and defect reproduction actions."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sqlite3
import threading

from backend.analytics.test_method_evidence_report import (
    _canonical_defect_id,
    _split_values,
    build_test_method_evidence_payload,
)


METHODS = {
    'Concurrent activity / interference': r'\b(?:while|during|simultaneous\w*|at the same time|background|foreground)\b',
    'State / source / account transition': r'\b(?:switch\w*|log\s*(?:in|out)|login|logout|mute|unmute|pause|resume|change\s+(?:account|profile|source|mode))\b',
    'Lifecycle / power transition': r'\b(?:reboot|restart|cold\s*boot|ignition|bus\s*sleep|new\s+lc|power\s*(?:on|off|cycle)|wake\s*up\s+(?:the\s+)?(?:vehicle|system|car|hu)|(?:vehicle|system|car|hu)\s+(?:sleep|wakeup))\b',
    'Cross-device / cross-screen handoff': r'\b(?:pair\w*|unpair\w*|bluetooth|carplay|hicar|casting|cast|share\w*|sync\w*|send\s+to|handover)\b',
    'Timing / repetition / sequence': r'\b(?:repeat\w*|rapid\w*|immediately|quick\w*|twice|multiple\s+times|long\s*press|short\s*press|before|after)\b',
    'Interruption / cancellation / recovery': r'\b(?:interrupt\w*|cancel\w*|disconnect\w*|reconnect\w*|retry|recover\w*|abort\w*|rejoin)\b',
    'Input / language / command variation': r'\b(?:commands?|utterances?|say|speak|speech|language|keyboard|speller|input|special\s+character)\b',
    'Explicit boundary / negative setup': r'\b(?:invalid|maximum|minimum|out\s+of\s+range|boundary|empty|without\s+(?:network|permission|subscription)|deny|denied)\b',
}

STRATEGY_SECTIONS = {
    'Concurrent activity / interference': '2; 7.3',
    'State / source / account transition': '7.3',
    'Lifecycle / power transition': '7.2; 7.3',
    'Cross-device / cross-screen handoff': '7.3; 7.4',
    'Timing / repetition / sequence': '7.3',
    'Interruption / cancellation / recovery': '3 Reliability; 7.3',
    'Input / language / command variation': '2; 7.3; 7.6',
    'Explicit boundary / negative setup': '3 Reliability; 7.3',
}

REVIEWED_DISCOVERY_PATHS = {
    '2812911': ('Input-channel contrast under HiCar integration', 'The combined setup/action block varies natural commands, hotwords and normal wake-word commands under HiCar navigation and audio. CW34 also references this defect; this is a direct strategy-to-procedure evidence chain.'),
    '2820755': ('Passenger role and cross-screen voice control', 'The passenger invokes voice control while music plays on CID. CW36 references this defect, but the ticket alone does not establish completion of every planned startup-stage combination.'),
    '2825176': ('Real-location navigation and lifecycle confirmation', 'The ticket describes an indoor parking floor-recognition failure at a real destination. CW36 adds route-recalculation and recovery context; these remain distinct sources rather than invented ticket steps.'),
    '2878043': ('Rapid inverse operations', 'The reproduction combines ongoing Carlink playback with rapid mute/unmute. This is a timing and state-combination hypothesis, not just a normal play/pause assertion.'),
    '2875260': ('History-dependent account transition', 'The sequence dismisses a notification, changes account A to B, then returns to A. It probes whether prior interaction changes later notification behavior.'),
    '2858005': ('Audio-resource contention and release', 'The sequence starts SMS playback, invokes the voice assistant during the broadcast, exits the assistant, then checks audio. It probes resource handover and release across functions.'),
    '2802292': ('Disconnect/reconnect ordering', 'The sequence connects a gamepad, disconnects it, requests reconnection from the vehicle list, and only then powers on the controller. It exercises a specific reconnection order.'),
    '2876101': ('Emergency-call aftermath and lifecycle contrast', 'The sequence connects CarPlay after an E-call is hung up and also records a new-lifecycle scenario. It probes residual state after another function, not simply first connection.'),
}


class _Lines(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {'br', 'p', 'li', 'div', 'tr'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'p', 'li', 'div', 'tr'}:
            self.parts.append('\n')

    def handle_data(self, data):
        self.parts.append(data)


def _plain(value):
    parser = _Lines()
    parser.feed(str(value or ''))
    return '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())


def _safe(value):
    text = re.sub(r'(?im)(password|passwd|token|secret)\s*[:=]\s*\S+', r'\1: [redacted]', str(value or ''))
    return re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[email]', text)


_ACTION_LINE_PATTERN = re.compile(
    r'^(?:on\s+(?:the\s+)?[\w -]{1,35},\s*)?'
    r'(?:(?:the\s+)?(?:user|tester|driver|passenger|EP|CID|CDE|RSE|FPD|PADI)\s+)?'
    r'(?:(?:when|while|after|before|then|once|during|if)\b.{0,180}?\b)?'
    r'(?:open|go|navigate|click|tap|press|play(?:ing)?|start|stop|switch|change|select|trigger|'
    r'connect|disconnect|reconnect|unplug|plug|pair|unpair|send|receive|wait|check(?:ed)?|observe|'
    r'verify|try|turn|wake|unfold|fold|ask|say|use|enter|exit|scroll|adjust|repeat|set|enable|'
    r'disable|delete|remove|login|logout|log\s+in|log\s+out|drive|listen|call|unlock|lock|'
    r'reboot|reset|restart|long\s+press|short\s+press|keep\s+talking|activate|speak|put|leave|'
    r'perform|launch|cancel|close|swipe|record|measure|monitor|reproduce|say|'
    r'just\s+talk|hotword\s+commands?|normal\s+commands?|free\s*talk\s+commands?)\b', re.I,
)
_SETUP_LINE_PATTERN = re.compile(
    r'^(?:system|network|vehicle|car|hu|bmw\s*-?id|music|phone|device|navigation|account|'
    r'platform|vin|software|sw\b|app\s+version|version|preconditions?)\b|'
    r'\b(?:is|are|has\s+been)\s+(?:connected|started|logged|playing|enabled|available)\b', re.I,
)


def _procedure_lines(content):
    text = re.sub(r'(?<!\d)(\d{1,2}[.),])\s*(?=[A-Za-z])', r'\n\1 ', content)
    text = re.sub(r'(?<=\S)>\s*(?=[A-Za-z])', '\n', text)
    lines = []
    for raw_line in text.splitlines():
        value = re.sub(r'^\s*[-*#>]*\s*(?:\d{1,2}[.),]\s*)?', '', raw_line).strip()
        if value and value.lower() not in {'n/a', 'na', 'none', '-', 'no preconditions'}:
            lines.append(value)
    return lines


def extract_free_procedure(description):
    text = _plain(description)
    labels = re.compile(
        r'pre[- ]?conditions?\s+and\s+actions?\s*(?:\([^)]{0,500}\))?\s*:?|'
        r'pre[- ]?conditions?\s*:|actions?(?:/steps)?\s+to\s+\(?(?:re-?)?\)?\s*produce\s+(?:the\s+)?(?:problem|error)\s*:|'
        r'steps?\s+to\s+reproduce(?:\s+(?:the\s+)?(?:error|problem))?\s*:|'
        r'(?:test\s+steps?|actions?|procedure)\s*:|'
        r'(?:expected|observed)\s+(?:result|behavio[u]?r)\s*(?:/behavio[u]?r)?\s*:|'
        r'possible\s+impact\s*:|customer\s+impact\s*:|actions?/steps?\s+to\s+recover[^:\n]*:|'
        r'test\s+case\s+reference\s*:|how\s+often\b|easiest\s+way\s+of\s+recovery\b|'
        r'===\s*end\s+of\s+error\s+description', re.I,
    )
    matches = list(labels.finditer(text))
    sections = {'preconditions': [], 'actions': [], 'combinedContext': [], 'unclassifiedContext': []}
    explicit_actions = False
    combined_actions = False
    for index, match in enumerate(matches):
        heading = match.group().lower()
        content = text[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(text)].strip()
        if 'recover' in heading or heading.startswith(('expected', 'observed', 'possible', 'customer', 'test case', 'how often', '===')):
            continue
        if content and content.lower() not in {'n/a', 'na', 'none', '-', 'no preconditions'}:
            if heading.startswith('pre') and re.search(r'\band\s+actions?', heading):
                sections['combinedContext'].append(content)
                for line in _procedure_lines(content):
                    if _ACTION_LINE_PATTERN.search(line):
                        sections['actions'].append(line)
                        combined_actions = True
                    elif _SETUP_LINE_PATTERN.search(line):
                        sections['preconditions'].append(line)
                    else:
                        sections['unclassifiedContext'].append(line)
            elif heading.startswith('pre'):
                sections['preconditions'].append(content)
            else:
                sections['actions'].append(content)
                explicit_actions = True
    result = {key: _safe('\n'.join(dict.fromkeys(values))) for key, values in sections.items()}
    result['extractionStatus'] = (
        'explicit_and_combined_actions' if explicit_actions and combined_actions else
        'explicit_actions' if explicit_actions else
        'combined_actions' if combined_actions else
        'context_only' if result['combinedContext'] or result['preconditions'] else
        'unrecognized_structure' if text else 'missing_description'
    )
    core_text = re.split(r'\b(?:Zeitpunkt|Melder|Problemmanager|Fahrzeug|Traces)\s*:|===\s*end\s+of\s+error\s+description', text, maxsplit=1, flags=re.I)[0]
    result['descriptionExcerpt'] = _safe(core_text[:6000])
    result['descriptionExcerptTruncated'] = len(core_text) > 6000
    return result


def _script_procedure(script):
    sections = {'preconditions': [], 'actions': []}
    kind = 'actions'
    for line in str(script or '').splitlines():
        value = line.strip().lstrip('- ').strip()
        if not value:
            continue
        if value.startswith('?'):
            kind = 'checkpoint'
        elif re.match(r'\[?precon(?:dition)?s?\]?\s*:?', value, re.I):
            kind = 'preconditions'
        elif line.strip().startswith('-'):
            kind = 'actions'
        if kind in sections:
            sections[kind].append(value)
    return {key: _safe('\n'.join(values)) for key, values in sections.items()}


def analyze_procedure(preconditions, actions):
    evidence = {}
    for method, expression in METHODS.items():
        sources = [('actions', actions)]
        if method == 'Explicit boundary / negative setup':
            sources.append(('preconditions', preconditions))
        for source, text in sources:
            match = re.search(expression, text, re.I)
            if match:
                evidence[method] = {'source': source, 'excerpt': text[max(0, match.start() - 90):match.end() + 180]}
                break
    return evidence


def _function_name(value):
    return re.sub(r'^FL\s+', '', str(value).strip(), flags=re.I)


DISCOVERY_PATTERNS = (
    {
        'id': 'rapid_inverse_operations', 'name': 'Rapid inverse operations', 'strategy': '7.3',
        'signals': (
            ('rapid timing', 'actions', r'\b(?:quick\w*|rapid\w*|frequently)\b'),
            ('inverse operation pair', 'actions', r'\b(?:mute\b[\s\S]{0,180}\bunmute|unmute\b[\s\S]{0,180}\bmute|pause\b[\s\S]{0,180}\bresume|open\b[\s\S]{0,180}\bclose)\b'),
        ),
        'interpretation': 'Timing is deliberately combined with opposing state changes rather than one normal operation.',
        'value': 'Provides a reproducible timing/state-combination counterexample for review; does not establish a race-condition root cause.',
    },
    {
        'id': 'audio_voice_interference', 'name': 'Voice invocation during another audio activity', 'strategy': '2; 7.3',
        'signals': (
            ('activity overlap', 'actions', r'\b(?:while|during|at the same time)\b'),
            ('other audio activity', 'actions', r'\b(?:sms|broadcast|tts|call)\b'),
            ('voice-assistant activity', 'actions', r'\b(?:speech|voice|elli|eli|ipa)\b'),
        ),
        'interpretation': 'The path overlaps voice interaction with call/message audio, testing handover and what remains after interruption.',
        'value': 'Extends a single-function assertion into a cross-function resource-ownership scenario; ownership defects still require diagnosis.',
    },
    {
        'id': 'disconnect_reconnect_sequence', 'name': 'Disconnect/reconnect sequence exploration', 'strategy': '7.3; 7.4',
        'orderedSignals': ('disconnect step', 'reconnection intent'),
        'signals': (
            ('disconnect step', 'actions', r'\b(?:disconnect\w*|unpair\w*)\b'),
            ('connection step', 'actions', r'\b(?:connect\w*|reconnect\w*|pair\w*)\b'),
            ('reconnection intent', 'actions', r'\b(?:reconnect\w*|connect\w*\s+again|build\s+connection)\b'),
        ),
        'interpretation': 'Disconnection and a further connection attempt occur in the same recorded path.',
        'value': 'Tests re-entry and residual connection state, with exact ordering preserved; not every matched sequence is non-standard.',
    },
    {
        'id': 'call_aftermath_connectivity', 'name': 'Emergency-call aftermath and connectivity', 'strategy': '2; 7.3',
        'orderedSignals': ('call context', 'call termination', 'projection/connectivity operation'),
        'signals': (
            ('call context', 'actions', r'\b(?:e[ -]?call|emergency\s+call|a[ -]?call)\b'),
            ('call termination', 'actions', r'\b(?:hang\s*up|hangup|end\w*\s+(?:the\s+)?call)\b'),
            ('projection/connectivity operation', 'actions', r'\b(?:carplay|hicar|carlink|bluetooth)\b'),
        ),
        'interpretation': 'A call ends before projection/connectivity is exercised in the same path.',
        'value': 'Exposes dependency on preceding function history instead of testing connectivity only from a clean starting state.',
    },
    {
        'id': 'dismissal_account_history', 'name': 'Notification dismissal and account-history transition', 'strategy': '7.3',
        'orderedSignals': ('notification dismissal', 'multiple transitions'),
        'signals': (
            ('notification dismissal', 'actions', r'\b(?:delete|dismiss\w*|close|remove)\b[^\n]{0,90}\bnotification\b'),
            ('account context', 'actions', r'\b(?:bmw\s*-?id|account|profile)\b'),
            ('multiple transitions', 'actions', r'\b(?:switch|login|log\s+in)\b[\s\S]{0,400}\b(?:switch|login|log\s+in)\b'),
        ),
        'interpretation': 'Prior notification interaction is combined with more than one account transition.',
        'value': 'Probes persistence of interaction history across identities; account-isolation failure is a hypothesis, not a confirmed root cause.',
    },
    {
        'id': 'noop_natural_speech', 'name': 'Natural speech and unintended activation', 'strategy': '2; 7.3; 7.6',
        'signals': (
            ('unintended-activation finding', 'title', r'\b(?:noop|wrongly\s+trigger\w*|mis[ -]?trigger\w*)\b'),
            ('spoken stimulus', 'actions', r'\b(?:talk\w*|say|ask|speak|conversation)\b'),
        ),
        'interpretation': 'The finding explicitly reports unintended activation and retains the spoken/discussion stimulus.',
        'value': 'Supports exploration of non-command/customer-conversation boundaries, not only positive command execution.',
    },
    {
        'id': 'input_channel_contrast', 'name': 'Voice-input channel and mode boundaries', 'strategy': '2; 7.3; 7.6',
        'signals': (
            ('hotword channel', 'actions', r'\bhot\s*words?\b'),
            ('alternative input channel', 'actions', r'\b(?:free\s*talk|normal\s+commands?|just\s+talk|wake\s*word)\b'),
        ),
        'interpretation': 'More than one named voice-input mechanism or its mode condition occurs in the procedure; both channels are not necessarily executed.',
        'value': 'Provides evidence for reviewing command acceptance against the enabled input mode rather than merely reporting a function as unavailable.',
    },
    {
        'id': 'occupant_screen_media', 'name': 'Occupant-role and screen-conditioned media control', 'strategy': '7.3; 7.6',
        'signals': (
            ('occupant role', 'context', r'\b(?:passenger|rear\s+seat|front\s+seat|co[ -]?driver)\b'),
            ('screen context', 'context', r'\b(?:cid|cde|rse|padi|fpd|phud)\b'),
            ('media/control operation', 'actions', r'\b(?:play\w*|music|song|volume|video|cast\w*|speech)\b'),
        ),
        'interpretation': 'An occupant role and screen context qualify a concrete media or control operation.',
        'value': 'Tests role/location/context combinations rather than assuming driver-side behavior generalizes to every seat and screen.',
    },
    {
        'id': 'lifecycle_conditioned_use', 'name': 'Lifecycle-conditioned customer operation', 'strategy': '7.2; 7.3',
        'signals': (
            ('explicit lifecycle condition', 'context', r'\b(?:bus\s*sleep|cold\s*boot|new\s+lc|new\s+life\s*cycle|after\s+(?:a\s+)?reboot)\b'),
            ('subsequent customer operation', 'actions', r'\b(?:open|play\w*|connect\w*|activate|check|observe|start|speech|navigate)\b'),
        ),
        'interpretation': 'A documented lifecycle condition qualifies the customer operation; this is not inferred from ordinary power-on setup.',
        'value': 'Makes state-at-entry part of the test hypothesis. Reported lifecycle run counts alone are not used to invent discovery steps.',
    },
    {
        'id': 'configuration_contrast', 'name': 'Configuration on/off contrast', 'strategy': '7.3',
        'signals': (
            ('configurable subject', 'actions', r'\b(?:llm|privacy|permission|toggle)\b'),
            ('enabled branch', 'actions', r'\b(?:on|enable\w*)\b'),
            ('disabled branch', 'actions', r'\b(?:off|disable\w*)\b'),
        ),
        'interpretation': 'The procedure includes a configurable subject and both enabled and disabled branches.',
        'value': 'Supplies a configuration contrast for reviewing conditional behavior; it does not prove a complete configuration matrix.',
    },
    {
        'id': 'location_conditioned_navigation', 'name': 'Location/road-conditioned navigation', 'strategy': '2; 7.3',
        'signals': (
            ('location or road condition', 'titleAndContext', r'\b(?:parking|floor|tunnel|highway|intersection|road|mall)\b'),
            ('navigation operation', 'actions', r'\b(?:navigation|navigate|guidance|route|destination)\b'),
        ),
        'interpretation': 'A location/road condition is associated with a recorded navigation operation.',
        'value': 'Preserves environmental dependencies for investigation. The matching text alone does not certify a physical road test or location coverage.',
    },
    {
        'id': 'source_switch_during_playback', 'name': 'Media-source transition during playback', 'strategy': '2; 7.3',
        'signals': (
            ('active playback', 'actions', r'\b(?:play\w*|listen\w*)\b'),
            ('media source context', 'context', r'\b(?:music|media|audio|radio|qq|netease|ximalaya|himalaya|kugou|carlink)\b'),
            ('source transition', 'actions', r'\bswitch\w*\s+(?:(?:the\s+)?(?:media|audio)\s+source|(?:from|to)\s+(?:qq|netease|ximalaya|himalaya|kugou|radio|carlink|other\s+media))\b'),
        ),
        'interpretation': 'Playback and an explicit media-source transition appear in the same recorded path.',
        'value': 'Checks continuity and source ownership across applications rather than only testing each media app in isolation.',
    },
    {
        'id': 'voice_over_media_context', 'name': 'Voice control in an active-media context', 'strategy': '2; 7.3; 7.6',
        'signals': (
            ('media activity', 'context', r'\b(?:is|was)\s+playing\b|(?:^|[\n.;])\s*(?:\d+[.),]\s*)?(?:(?:idcevo|cid|cde|rse|vehicle)\s+)?(?:play(?:ing)?|listen(?:ing)?\s+to)\b[^\n]{0,100}\b(?:music|media|audio|radio|song|video|qq)\b'),
            ('voice operation', 'actions', r'\b(?:say|speak|speech|voice\s+(?:command|control)|activate\s+(?:ipa|elli))\b'),
        ),
        'interpretation': 'A real voice operation is qualified by media already being active or played in the procedure.',
        'value': 'Tests the interaction between media state and voice control. This is shared with formal test techniques, not an exclusive Free-test method.',
    },
    {
        'id': 'account_service_transition', 'name': 'Account transition with a customer service', 'strategy': '7.3',
        'signals': (
            ('identity transition', 'actions', r'\b(?:switch\w*\s+(?:account|profile|bmw)|log\s*out|logout)\b'),
            ('dependent customer service', 'context', r'\b(?:music|navigation|charging|carplay|hicar|casting|wifi|wi-fi|subscription)\b'),
        ),
        'interpretation': 'The path changes identity while a named customer service is in scope.',
        'value': 'Tests whether service state and user-specific state stay consistent across account transitions; isolation or privacy defects are not assumed.',
    },
)


def _discovery_pattern_evidence(ticket, definition):
    actions = ticket.get('actions', '')
    if not actions.strip():
        return []
    context = '\n'.join(str(ticket.get(field) or '') for field in ('preconditions', 'actions', 'unclassifiedContext'))
    sources = {'actions': actions, 'context': context, 'title': ticket.get('name', ''),
               'titleAndActions': ticket.get('name', '') + '\n' + actions,
               'titleAndContext': ticket.get('name', '') + '\n' + context}
    expressions = {label: (source, expression) for label, source, expression in definition['signals']}
    ordered_matches = {}
    cursor = 0
    for label in definition.get('orderedSignals', ()):
        source, expression = expressions[label]
        match = re.compile(expression, re.I).search(sources[source], cursor)
        if match is None:
            return []
        ordered_matches[label] = match
        cursor = match.end()
    evidence = []
    for label, source, expression in definition['signals']:
        text = sources[source]
        match = ordered_matches.get(label) or re.search(expression, text, re.I)
        if match is None:
            return []
        evidence.append({'signal': label, 'source': source, 'excerpt': text[max(0, match.start() - 70):match.end() + 150]})
    return evidence


def _case_confirmation(ticket):
    text = ticket.get('descriptionExcerpt', '')
    fields = {
        'reproductionAttempts': r'how\s+often(?:\s+did)?(?:\s+the)?\s+(?:tester|you)\s+try\s+to\s+reproduce(?:\s+the)?\s+defect',
        'lifecycleRuns': r'in\s+how\s+many\s+lifecycles(?:\s+did)?(?:\s+the)?\s+(?:tester|you)\s+try\s+to\s+reproduce(?:\s+the)?\s+defect',
    }
    result = {'evidence': {}}
    number_words = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10}
    for field, expression in fields.items():
        previous = ticket.get(field)
        heading = re.search(expression, text, re.I)
        if heading is None:
            result[field] = previous if isinstance(previous, int) else None
            result['evidence'][field] = {'source': 'snapshot_field', 'previousSnapshotValue': previous, 'excerpt': ''}
            continue
        segment = text[heading.end():]
        boundary = re.search(r'\b(?:in\s+how\s+many\s+lifecycles|how\s+often|easiest\s+way|Zeitpunkt|Melder)\b', segment, re.I)
        if boundary:
            segment = segment[:boundary.start()]
        segment = segment[:400]
        cleaned = re.sub(r'\[\s*please\s+fill\s+in\s+natural\s+numbers\s*\]|\(?\s*whole\s+numbers\s+starting\s+from\s+1\s*\)?|\(\s*over\s*all[^)]*\)|try\s+at\s+least\s+in\s+\d+\s*,?\s*better\s+in\s+\d+\s+lifecycles?\.?', ' ', segment, flags=re.I)
        answer = re.match(r'^[\s:?*#\[\](){}.-]*(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b', cleaned, re.I)
        value = None
        if answer and not re.match(r'\s*/\s*\d', cleaned[answer.end():]):
            raw_value = answer.group(1).lower()
            value = int(raw_value) if raw_value.isdigit() else number_words[raw_value]
        result[field] = value
        result['evidence'][field] = {'source': 'descriptionExcerpt' if value is not None else 'descriptionExcerpt_unparsed',
                                   'previousSnapshotValue': previous, 'excerpt': _safe(text[heading.start():heading.end()] + segment)}
    return result


def build_discovery_pattern_library(tickets, cases):
    unique_tickets = {ticket['defectId']: ticket for ticket in tickets}
    patterns = []
    candidate_ids = set()
    for definition in DISCOVERY_PATTERNS:
        case_studies = []
        for ticket in unique_tickets.values():
            evidence = _discovery_pattern_evidence(ticket, definition)
            if not evidence:
                continue
            candidate_ids.add(ticket['defectId'])
            comparators = [
                {'testId': case['testId'], 'name': case['name'], 'actions': case.get('actions', ''),
                 'samePatternEvidence': _discovery_pattern_evidence(case, definition)}
                for case in cases if set(case.get('functions', [])) & set(ticket.get('productAreas', []))
            ]
            comparators.sort(key=lambda case: (not bool(case['samePatternEvidence']), case['testId']))
            confirmation = _case_confirmation(ticket)
            case_studies.append({
                'defectId': ticket['defectId'], 'name': ticket.get('name', ''),
                'functions': ticket.get('productAreas', []), 'discoveryPath': ticket['actions'],
                'scenarioContext': '\n'.join(str(ticket.get(field) or '') for field in ('preconditions', 'actions', 'unclassifiedContext')),
                'preconditions': ticket.get('preconditions', ''), 'evidence': evidence,
                'methodInterpretation': definition['interpretation'], 'valueHypothesis': definition['value'],
                'reproductionAttempts': confirmation['reproductionAttempts'], 'lifecycleRuns': confirmation['lifecycleRuns'],
                'confirmationEvidence': confirmation['evidence'],
                'reviewStatus': 'previously_reviewed_source_case' if ticket['defectId'] in REVIEWED_DISCOVERY_PATHS else 'rule_candidate_requires_review',
                'testcaseComparators': comparators[:2],
            })
        if not case_studies:
            continue
        attempts = [case['reproductionAttempts'] for case in case_studies if isinstance(case['reproductionAttempts'], int)]
        lifecycles = [case['lifecycleRuns'] for case in case_studies if isinstance(case['lifecycleRuns'], int)]
        patterns.append({
            'patternId': definition['id'], 'pattern': definition['name'], 'strategySections': definition['strategy'],
            'testcaseSupportCount': sum(bool(_discovery_pattern_evidence(case, definition)) for case in cases),
            'methodInterpretation': definition['interpretation'], 'valueHypothesis': definition['value'],
            'caseCount': len(case_studies), 'casesWithAttempts': len(attempts), 'casesWithoutAttempts': len(case_studies) - len(attempts),
            'reportedAttempts': sum(attempts), 'casesWithLifecycles': len(lifecycles),
            'casesWithoutLifecycles': len(case_studies) - len(lifecycles), 'reportedLifecycles': sum(lifecycles),
            'caseStudies': case_studies,
        })
    unique_candidates = [unique_tickets[ticket_id] for ticket_id in sorted(candidate_ids)]
    unique_confirmations = [_case_confirmation(ticket) for ticket in unique_candidates]
    candidate_attempts = [confirmation['reproductionAttempts'] for confirmation in unique_confirmations if isinstance(confirmation['reproductionAttempts'], int)]
    candidate_lifecycles = [confirmation['lifecycleRuns'] for confirmation in unique_confirmations if isinstance(confirmation['lifecycleRuns'], int)]
    return {'policyVersion': 'compound-discovery-path-v1',
            'analyzedTicketCount': len(unique_tickets), 'candidateTicketCount': len(candidate_ids),
            'candidateTicketIds': sorted(candidate_ids), 'patterns': patterns,
            'candidateEffort': {'reportedAttempts': sum(candidate_attempts), 'casesWithAttempts': len(candidate_attempts),
                    'reportedLifecycles': sum(candidate_lifecycles), 'casesWithLifecycles': len(candidate_lifecycles)},
            'interpretation': 'Compound-signal candidate families with original procedures, not certified independent defects or proof of Free-test-exclusive methods. Pattern families overlap; missing effort is not zero effort.'}


def build_comparison(cases, tickets):
    case_evidence = []
    free_evidence = []
    for case in cases:
        procedure = _script_procedure(case.get('script'))
        case_evidence.append({**case, 'functions': sorted({_function_name(value) for value in case.get('functions', [])}), **procedure, 'methodEvidence': analyze_procedure(**procedure)})
    for ticket in tickets:
        procedure = extract_free_procedure(ticket.get('description'))
        free_evidence.append({key: value for key, value in {**ticket, 'productAreas': sorted({_function_name(value) for value in ticket.get('productAreas', [])}), **procedure, 'methodEvidence': analyze_procedure(procedure['preconditions'], procedure['actions'])}.items() if key != 'description'})
    comparison = []
    scripted = [case for case in case_evidence if str(case.get('script') or '').strip()]
    action_tickets = [ticket for ticket in free_evidence if ticket['actions']]
    for method in METHODS:
        baseline = [case for case in scripted if method in case['methodEvidence']]
        extra = [ticket for ticket in action_tickets if method in ticket['methodEvidence']]
        pairs = []
        for ticket in extra:
            for case in baseline:
                shared = set(ticket.get('productAreas', [])) & set(case.get('functions', []))
                if shared:
                    pairs.append({
                        'function': sorted(shared)[0],
                        'testcase': {'testId': case['testId'], 'name': case['name'], **case['methodEvidence'][method]},
                        'freeTest': {'defectId': ticket['defectId'], 'name': ticket['name'], **ticket['methodEvidence'][method]},
                    })
                    break
            if len(pairs) == 3:
                break
        comparison.append({
            'method': method, 'testcases': len(baseline), 'freeTestTickets': len(extra),
            'strategySections': STRATEGY_SECTIONS[method],
            'testcaseShare': round(len(baseline) / len(scripted), 3) if scripted else None,
            'freeTestShare': round(len(extra) / len(action_tickets), 3) if action_tickets else None,
            'interpretation': 'Shared method in observed procedures' if baseline and extra else 'Observed only in Free-test sample; not proof of exclusive coverage' if extra else 'Not observed in Free-test extracted procedures',
            'freeTestAttempts': sum(ticket.get('reproductionAttempts') or 0 for ticket in extra),
            'freeTestLifecycles': sum(ticket.get('lifecycleRuns') or 0 for ticket in extra),
            'pairedExamples': pairs,
            'testcaseExamples': [{'testId': case['testId'], 'name': case['name'], **case['methodEvidence'][method]} for case in baseline[:3]],
            'freeTestExamples': [{'defectId': ticket['defectId'], 'name': ticket['name'], **ticket['methodEvidence'][method]} for ticket in extra[:3]],
        })
    functions = defaultdict(lambda: {'testIds': set(), 'defectIds': set(), 'attempts': 0, 'lifecycles': 0})
    for case in scripted:
        for function in set(case.get('functions', [])):
            functions[function]['testIds'].add(case['testId'])
    for ticket in free_evidence:
        for function in set(ticket.get('productAreas', [])):
            functions[function]['defectIds'].add(ticket['defectId'])
            functions[function]['attempts'] += ticket.get('reproductionAttempts') or 0
            functions[function]['lifecycles'] += ticket.get('lifecycleRuns') or 0
    function_rows = [{'function': name, 'testcases': len(values['testIds']), 'freeTestTickets': len(values['defectIds']), 'attempts': values['attempts'], 'lifecycles': values['lifecycles']} for name, values in functions.items()]
    return {
        'coverage': {'testcaseCount': len(cases), 'testcasesWithScripts': len(scripted), 'freeTestTickets': len(tickets), 'freeTestAnalyzed': len(free_evidence), 'freeTestWithActions': len(action_tickets), 'freeTestWithoutActions': len(free_evidence) - len(action_tickets), 'freeTestWithMethodEvidence': sum(bool(ticket['methodEvidence']) for ticket in action_tickets), 'freeTestActionsWithoutMethodMatch': sum(not ticket['methodEvidence'] for ticket in action_tickets)},
        'extractionAudit': [{'status': status, 'count': count} for status, count in sorted(Counter(ticket['extractionStatus'] for ticket in free_evidence).items())],
        'limitations': [
            'Current testcase scripts are not guaranteed to be the historical version executed when the defect was found.',
            'Method counts are overlapping lexical evidence in extracted procedures, not an LLM audit or proof of exclusivity.',
            'Units differ: unique testcases versus defect tickets; shares are descriptive, not a controlled effectiveness comparison.',
            'Reproduction and lifecycle counts measure post-discovery confirmation, not discovery hours or defect-finding efficiency.',
            'Missing section extraction does not mean the ticket has no steps. No complete test execution or effort-hour denominator exists.',
        ],
        'methodComparison': comparison,
        'discoveryPatternLibrary': build_discovery_pattern_library(free_evidence, case_evidence),
        'reviewedDiscoveryPaths': [
            {**ticket, 'pattern': REVIEWED_DISCOVERY_PATHS[ticket['defectId']][0], 'interpretation': REVIEWED_DISCOVERY_PATHS[ticket['defectId']][1],
             'sameFunctionComparators': [
                 {'testId': case['testId'], 'name': case['name'], 'actions': case['actions']}
                 for case in scripted if set(case.get('functions', [])) & set(ticket.get('productAreas', []))
             ][:2]}
            for ticket in free_evidence if ticket['defectId'] in REVIEWED_DISCOVERY_PATHS and ticket['actions']
        ],
        'functionEffort': sorted(function_rows, key=lambda row: (-row['freeTestTickets'], row['function'])),
        'testcaseEvidence': case_evidence, 'freeTestEvidence': free_evidence,
    }


def _render_discovery_pattern_library(library):
    effort = library['candidateEffort']
    parts = [
        '<h2 id="discovery-patterns">Discovery Path Pattern Library</h2>',
        f'<p>{library["candidateTicketCount"]} unique candidate tickets from {library["analyzedTicketCount"]} analyzed Free-test records support {len(library["patterns"])} compound-evidence families. The original source procedure, required signals, method interpretation, value hypothesis and recorded confirmation effort are retained for every candidate.</p>',
        f'<p>Deduplicated candidate confirmation: {effort["reportedAttempts"]} extracted attempts in {effort["casesWithAttempts"]} tickets; {effort["reportedLifecycles"]} extracted lifecycle runs in {effort["casesWithLifecycles"]} tickets. These are not work hours, total executions or a causal efficiency measure.</p>',
        f'<p class="note">{escape(library["interpretation"])}</p>',
        '<div class="scroll"><table class="matrix"><tr><th>Compound discovery pattern</th><th>Candidate tickets</th><th>Attempts / field coverage</th><th>Lifecycles / field coverage</th><th>Testcase pattern support</th><th>Strategy sections</th></tr>',
    ]
    for family in library['patterns']:
        attempts = str(family['reportedAttempts']) if family['casesWithAttempts'] else 'Not extracted'
        lifecycles = str(family['reportedLifecycles']) if family['casesWithLifecycles'] else 'Not extracted'
        parts.append(f'<tr><td><a href="#pattern-{family["patternId"]}">{escape(family["pattern"])}</a></td><td>{family["caseCount"]}</td><td>{attempts}; {family["casesWithAttempts"]}/{family["caseCount"]} tickets</td><td>{lifecycles}; {family["casesWithLifecycles"]}/{family["caseCount"]} tickets</td><td>{family["testcaseSupportCount"]}</td><td>{escape(family["strategySections"])}</td></tr>')
    parts.append('</table></div><p>Testcase pattern support is measured in the selected script sample, not the whole library. A same-function comparator is context for review, not proof that the exact discovery path was already covered or was entirely new.</p>')
    for family in library['patterns']:
        parts.append(f'<details id="pattern-{family["patternId"]}" class="pattern-family"><summary>{escape(family["pattern"])}: {family["caseCount"]} candidate tickets</summary><p>{escape(family["methodInterpretation"])}</p><p><strong>Potential engineering value:</strong> {escape(family["valueHypothesis"])}</p>')
        for case in family['caseStudies']:
            attempts = case['reproductionAttempts'] if case['reproductionAttempts'] is not None else 'Not extracted'
            lifecycles = case['lifecycleRuns'] if case['lifecycleRuns'] is not None else 'Not extracted'
            parts.append(f'<details class="pattern-case"><summary>D{escape(case["defectId"])}: {escape(case["name"])}</summary><p>{escape("; ".join(case["functions"]))}</p><p>Status: {escape(case["reviewStatus"])}. Confirmation: {attempts} attempts; {lifecycles} lifecycle runs.</p><h3>Recorded discovery path</h3><pre>{escape(case["discoveryPath"])}</pre><h3>Scenario context</h3><pre>{escape(case["scenarioContext"])}</pre><h3>Required pattern evidence</h3>')
            for item in case['evidence']:
                parts.append(f'<p>{escape(item["signal"])} ({escape(item["source"])})</p><pre>{escape(item["excerpt"])}</pre>')
            parts.append('<h3>Confirmation answer provenance</h3>')
            for field, item in case['confirmationEvidence'].items():
                parts.append(f'<p>{escape(field)}: {escape(item["source"])}; previous snapshot value: {escape(str(item["previousSnapshotValue"]))}</p><pre>{escape(item["excerpt"])}</pre>')
            for comparator in case['testcaseComparators']:
                support = 'Also matches this compound pattern' if comparator['samePatternEvidence'] else 'Same-function context only'
                parts.append(f'<h3>Testcase {escape(comparator["testId"])}</h3><p>{escape(comparator["name"])}. {support}.</p><pre>{escape(comparator["actions"][:3000])}</pre>')
            if not case['testcaseComparators']:
                parts.append('<p>No same-function comparator in the selected defect-linked script sample; not proof that no suitable testcase exists.</p>')
            parts.append('</details>')
        parts.append('</details>')
    return ''.join(parts)


def _render(result):
    coverage = result['coverage']
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Actual-step Test Method Comparison</title>',
             '<style>body{margin:0;color:#183f39;background:#f4f7f6;font:15px/1.5 Georgia,serif}main{max-width:1240px;margin:auto;padding:24px}h1,h2,h3{font-family:"Trebuchet MS",sans-serif;letter-spacing:0}h1{font-size:30px}h2{font-size:22px;border-top:3px solid #168778;padding-top:18px}table{border-collapse:collapse;width:100%;background:white}td,th{text-align:left;border-bottom:1px solid #cbd9d4;padding:10px;vertical-align:top;overflow-wrap:anywhere}th{background:#dfece6}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.5 Consolas,monospace}details{padding:12px 0;border-bottom:1px solid #cbd9d4}summary{cursor:pointer;font-weight:bold}.scroll{overflow:auto}.matrix{min-width:850px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}.note{border-left:4px solid #b85430;padding:12px;background:#fff1e9}@media(max-width:760px){main{padding:14px}.pair{grid-template-columns:1fr}}</style><main>',
             '<h1>Actual-step Test Method Comparison</h1>',
             f'<p>{coverage["testcasesWithScripts"]}/{coverage["testcaseCount"]} actual testcase scripts; {coverage["freeTestWithActions"]}/{coverage["freeTestTickets"]} Free-test tickets with extracted actions.</p>',
             f'<h2>Full-population Analysis Audit</h2><p>All {coverage["freeTestAnalyzed"]} Free-test records were processed. {coverage["freeTestWithActions"]} contain identified action text; {coverage["freeTestWithoutActions"]} remain without identified actions. {coverage["freeTestWithMethodEvidence"]} match a method rule; {coverage["freeTestActionsWithoutMethodMatch"]} have actions but no method-rule match. Processing every record is not a claim that every method was understood or manually audited.</p>',
             '<table><tr><th>Extraction status</th><th>Tickets</th></tr>' + ''.join(f'<tr><td>{escape(row["status"])}</td><td>{row["count"]}</td></tr>' for row in result['extractionAudit']) + '</table>',
             '<p class="note">Formal testcase execution already includes complex methods. Free-test adds independently recorded discovery paths and confirmation work, not necessarily exclusive techniques. Compare the actual excerpts below before claiming additional coverage.</p>',
             '<h2>High-level Insight: What Is Additional?</h2><p>The distinction is not scripted testing versus random testing. Both lanes exercise complex methods. The additional Free-test work is independent scenario construction, extending the sequence or context of an operation, documenting the unexpected failure, and repeatedly confirming it across lifecycles. The procedure evidence below supports method hypotheses; it does not prove an existing testcase could never detect the issue.</p>',
             '<p>Ordinary powered-on setup is excluded from lifecycle-method counts. Methods use action text; only explicit negative setup may use preconditions. Function names with an FL prefix are consolidated for the same-function comparison.</p>',
             '<p>Combined precondition/action text is retained verbatim; identified actions are separated with explicit rules. Ordinary setup is not promoted into action evidence. Unclassified context and unresolved descriptions remain visible in the full index. Input/speech keywords are related-operation signals, not proof that every match is a designed input-variation experiment.</p>',
             '<h2>Strategy Basis</h2><p><a href="https://atc.bmwgroup.net/confluence/pages/viewpage.action?pageId=4447601857">DTSV CN Test Strategy - Validation Beyond, v89</a>: sections 2, 7.3 and 7.6 support active customer scenarios, exploratory/corner/cross-function tests and CarFunction/IuK interactions. Section 7.1 supports feeding discoveries into regression. The page body says Drating; alignment is not formal compliance certification. <a href="https://atc.bmwgroup.net/confluence/pages/viewpage.action?pageId=8681681592">CW34 2026</a> and <a href="https://atc.bmwgroup.net/confluence/pages/viewpage.action?pageId=8735186775">CW36 2026</a> provide operational examples.</p>',
             '<h2>Methods x Discovery Evidence x Confirmation Effort</h2><p>Method categories overlap and cannot be summed as unique defects. Attempt/lifecycle figures are retained reported confirmation counts, not work hours or test efficiency. The testcase denominator is a defect-linked sample, not the whole library.</p><div class="scroll"><table class="matrix"><tr><th>Observed method</th><th>Testcases</th><th>Free-test tickets</th><th>Recorded attempts</th><th>Lifecycle runs</th><th>Strategy sections</th><th>Interpretation</th></tr>']
    for row in result['methodComparison']:
        parts.append('<tr>' + ''.join(f'<td>{escape(str(row[key]))}</td>' for key in ('method', 'testcases', 'freeTestTickets', 'freeTestAttempts', 'freeTestLifecycles', 'strategySections', 'interpretation')) + '</tr>')
    parts.append('</table></div>')
    parts.append(_render_discovery_pattern_library(result['discoveryPatternLibrary']))
    parts.append('<h2>Reviewed Discovery Paths</h2><p>These are method interpretations grounded in the displayed action sequences, not proof of a root cause or a gap in the entire testcase library.</p>')
    for ticket in result['reviewedDiscoveryPaths']:
        parts.append(f'<details open><summary>{escape(ticket["pattern"])} | D{escape(ticket["defectId"])}</summary><p>{escape(ticket["name"])}</p><p>{escape(ticket["interpretation"])}</p><pre>{escape(ticket["actions"])}</pre><p>Previous-snapshot confirmation: {escape(str(ticket.get("reproductionAttempts")))} attempts; {escape(str(ticket.get("lifecycleRuns")))} lifecycle runs. Missing values mean not extracted, not no effort. The compound-pattern library above rechecks original answer fields and records corrections.</p>')
        if ticket['sameFunctionComparators']:
            parts.append('<h3>Same-function testcase context</h3>')
            for case in ticket['sameFunctionComparators']:
                parts.append(f'<p>Test {escape(case["testId"])}: {escape(case["name"])}</p><pre>{escape(case["actions"][:2400])}</pre>')
        else:
            parts.append('<p>No same-function comparator among the defect-linked testcase sample. This does not mean no testcase exists for the function.</p>')
        parts.append('</details>')
    parts.append('<h2>Procedure Evidence Side by Side</h2>')
    for row in result['methodComparison']:
        parts.append(f'<details><summary>{escape(row["method"])}: {row["testcases"]} testcases / {row["freeTestTickets"]} Free-test tickets</summary><div class="pair">')
        for field, title in (('testcaseExamples', 'Actual testcase script'), ('freeTestExamples', 'Free-test discovery actions')):
            parts.append(f'<div><h3>{title}</h3>')
            for example in row[field]:
                identifier = example.get('testId') or example.get('defectId')
                parts.append(f'<p><strong>{escape(identifier)}: {escape(example["name"])}</strong> ({escape(example["source"])})</p><pre>{escape(example["excerpt"])}</pre>')
            parts.append('</div>')
        parts.append('</div></details>')
    parts.append('<h2>Same-function Procedure Comparison</h2>')
    for row in result['methodComparison']:
        for pair in row['pairedExamples']:
            parts.append(f'<details><summary>{escape(pair["function"])} | {escape(row["method"])}</summary><div class="pair">')
            for field, label in (('testcase', 'Testcase'), ('freeTest', 'Free-test defect')):
                example = pair[field]
                identifier = example.get('testId') or example.get('defectId')
                parts.append(f'<div><h3>{label} {escape(identifier)}</h3><p>{escape(example["name"])}</p><pre>{escape(example["excerpt"])}</pre></div>')
            parts.append('</div></details>')
    parts.append('<h2>Function x Snapshot Confirmation Counts</h2><p>This table retains the previous snapshot count fields. The discovery-pattern library above rechecks answer text where available and records any corrections; these are different counting versions and must not be mixed.</p><div class="scroll"><table><tr><th>Function</th><th>Testcases with scripts</th><th>Free-test defects</th><th>Snapshot attempts</th><th>Snapshot lifecycles</th></tr>')
    for row in result['functionEffort'][:40]:
        parts.append('<tr>' + ''.join(f'<td>{escape(str(row[key]))}</td>' for key in ('function', 'testcases', 'freeTestTickets', 'attempts', 'lifecycles')) + '</tr>')
    parts.append('</table></div><h2>Scope and Interpretation</h2><ul>')
    parts.extend(f'<li>{escape(note)}</li>' for note in result['limitations'])
    parts.append('</ul><h2>Full Free-test Procedure and Review Index</h2><p>Every cohort ticket is included. No-action or no-rule-match entries are retained for review, not counted as no testing effort.</p><label>Search ticket or text <input id="ticket-search" type="search"></label> <label>Review filter <select id="ticket-review"><option value="">All records</option><option value="no_actions">No identified actions</option><option value="no_method">Actions without method match</option></select></label><p id="visible-count"></p>')
    for ticket in result['freeTestEvidence']:
        review = 'no_actions' if not ticket['actions'] else 'no_method' if not ticket['methodEvidence'] else 'matched'
        parts.append(f'<details class="ticket-record" data-review="{review}"><summary>D{escape(ticket["defectId"])}: {escape(ticket["name"])}</summary><p>Extraction: {escape(ticket["extractionStatus"])} | Methods: {escape("; ".join(ticket["methodEvidence"])) or "No rule match"}</p><h3>Preconditions</h3><pre>{escape(ticket["preconditions"])}</pre><h3>Identified actions</h3><pre>{escape(ticket["actions"])}</pre>')
        if ticket.get('combinedContext'):
            parts.append(f'<h3>Original combined context</h3><pre>{escape(ticket["combinedContext"])}</pre>')
        if ticket.get('unclassifiedContext'):
            parts.append(f'<h3>Unclassified context for review</h3><pre>{escape(ticket["unclassifiedContext"])}</pre>')
        if not ticket['actions']:
            parts.append(f'<h3>Source description excerpt, not method evidence</h3><pre>{escape(ticket.get("descriptionExcerpt", ""))}</pre>')
        parts.append('</details>')
    parts.append('''</main><script>
const records = [...document.querySelectorAll('.ticket-record')];
const search = document.getElementById('ticket-search');
const filter = document.getElementById('ticket-review');
function renderReview() {
  const query = search.value.toLowerCase().trim();
  let visible = 0;
  records.forEach(record => {
    const show = (!filter.value || record.dataset.review === filter.value) && (!query || record.textContent.toLowerCase().includes(query));
    record.hidden = !show;
    if (show) visible += 1;
  });
  document.getElementById('visible-count').textContent = `${visible} / ${records.length} records`;
}
search.addEventListener('input', renderReview);
filter.addEventListener('change', renderReview);
renderReview();
</script></html>''')
    return ''.join(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db-path', default='database/source/qgate_raw.db')
    parser.add_argument('--team', default='DTSV_China')
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--baseline', help='Preserve ticket/testcase membership and script versions from a previous comparison JSON.')
    parser.add_argument('--cache', default='backend/cache/test_method_steps_DTSV_China.json')
    parser.add_argument('--output-directory', default='exports/test_method_evidence/actual_steps')
    args = parser.parse_args()
    if args.baseline and args.fetch:
        parser.error('--baseline cannot be combined with --fetch; frozen script versions must be retained')
    baseline = json.loads(Path(args.baseline).read_text(encoding='utf-8')) if args.baseline else None
    payload = None if baseline else build_test_method_evidence_payload(db_path=args.db_path, team=args.team)
    with sqlite3.connect(args.db_path) as connection:
        connection.row_factory = sqlite3.Row
        defects = {_canonical_defect_id(row['defect_id']): dict(row) for row in connection.execute('SELECT defect_id,name,description,product_areas,last_modified,fetched_at FROM octane_defects WHERE team=?', (args.team,))}
        metadata = {}
        for row in connection.execute("SELECT defect_id,test_id,test_name FROM octane_manual_runs WHERE test_id IS NOT NULL AND defect_id IS NOT NULL"):
            defect_id = _canonical_defect_id(row['defect_id'])
            test_id = str(row['test_id'] or '').strip()
            if defect_id in defects and test_id:
                case = metadata.setdefault(test_id, {'testId': test_id, 'name': row['test_name'], 'defectIds': [], 'functions': []})
                case['defectIds'].append(defect_id)
                case['functions'].extend(_split_values(defects[defect_id]['product_areas']))
    cache_path = Path(args.cache)
    cached = json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
    if args.fetch:
        from backend.analytics.ingest import build_default_octane_client
        local = threading.local()

        def fetch(test_id):
            if not hasattr(local, 'client'):
                local.client = build_default_octane_client()
            try:
                return test_id, {**local.client.fetch_test_steps(test_id=test_id), 'fetchedAt': datetime.now(timezone.utc).isoformat()}
            except Exception as exc:
                return test_id, {'fetchError': str(exc)}

        with ThreadPoolExecutor(max_workers=4) as executor:
            for index, (test_id, record) in enumerate(executor.map(fetch, sorted(metadata)), 1):
                cached[test_id] = record
                if index % 25 == 0:
                    print(f'fetched={index}/{len(metadata)}', flush=True)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cached, ensure_ascii=False, indent=2), encoding='utf-8')
    cases = []
    for test_id, case in sorted(metadata.items()):
        snapshot = cached.get(test_id, {})
        cases.append({**case, 'defectIds': sorted(set(case['defectIds'])), 'functions': sorted(set(case['functions'])), 'script': _safe(snapshot.get('script', '')), 'testVersion': snapshot.get('test_version'), 'fetchedAt': snapshot.get('fetchedAt'), 'fetchError': snapshot.get('fetchError')})
    if baseline:
        cases = baseline['testcaseEvidence']
    cohort_tickets = baseline['freeTestEvidence'] if baseline else payload['freeTest']['tickets']
    tickets = [
        {**ticket, 'description': defects.get(ticket['defectId'], {}).get('description', ''),
         'sourceLastModified': defects.get(ticket['defectId'], {}).get('last_modified', ''),
         'sourceFetchedAt': defects.get(ticket['defectId'], {}).get('fetched_at', ''),
         'sourceDescriptionAvailable': ticket['defectId'] in defects}
        for ticket in cohort_tickets
    ]
    result = build_comparison(cases, tickets)
    result['scope'] = {
        'team': args.team, 'sourceDb': args.db_path, 'generatedAt': datetime.now(timezone.utc).isoformat(),
        'classification': baseline['scope']['classification'] if baseline else payload['summary'],
        'cohortGeneratedAt': (baseline['scope'].get('cohortGeneratedAt') or baseline['scope']['generatedAt']) if baseline else None,
        'baselinePath': args.baseline,
        'analysisPolicyVersion': 'free-test-procedure-v2-combined',
        'descriptionBasis': 'Local database descriptions read at regeneration; original cohort, scripts and recorded effort retained when --baseline is used.',
    }
    output = Path(args.output_directory)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'step_method_comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'step_method_comparison.html').write_text(_render(result), encoding='utf-8')
    print(json.dumps({'coverage': result['coverage'], 'html': str(output / 'step_method_comparison.html')}, ensure_ascii=False))


if __name__ == '__main__':
    main()