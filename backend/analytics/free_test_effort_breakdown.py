"""Expand an existing workload workbook without inventing measured Free-test hours."""
from __future__ import annotations

import argparse
from copy import copy
from hashlib import sha256
import json
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.hyperlink import Hyperlink
from openpyxl.worksheet.views import Selection
from openpyxl.workbook.properties import CalcProperties


SOURCE_SHEET = 'Effort Breakdown Details'
STAGES = (
    ('Z.01', 'Exploration goals and scenario design',
     'Define the customer risk, scope and stopping criteria; choose function, role, account, state, sequence and input contrasts.',
     'A bounded exploration goal and reproducible scenario/contrast set with observable expected outcomes.',
     'M/N/P formal requirement, testcase and test-focus management remain outside this Z subpackage.',
     'Exploration goals, designed combinations and contrasts; candidate pattern families', 'scenario bundle', None, None),
    ('Z.02', 'Exploration setup and discovery execution',
     'Establish exploration-specific accounts, permissions, devices and observation readiness; execute and adapt the actions.',
     'Reproducible setup and session notes, including passed/no-finding paths and candidate findings.',
     'R/S/T platform work, AD/AC coordination and logistics, Y formal testcase execution and V/W road-test time remain separate.',
     'Setup and exploration sessions; pass/no-finding results; candidate discoveries', 'exploration session', None, None),
    ('Z.03', 'Pre-ticket reproduction and trigger isolation',
     'Repeat a finding, vary one condition and isolate the smallest meaningful trigger before handoff.',
     'A reproducible counterexample, comparisons and recovery boundary.',
     'AB post-submission maintenance/retest is separate. Waiting is recorded apart from active work.',
     'Attempts and lifecycle answers with coverage; trigger contrasts', 'finding investigation', 20, 90),
    ('Z.04', 'Exploration evidence and regression suggestions',
     'Consolidate the exploration path, observations and minimal evidence for handoff; propose regression scenarios.',
     'A session evidence bundle and regression candidate, not an already-reviewed testcase.',
     'AA formal ticket authoring, AB follow-up, X/AE meetings and N testcase formalization stay outside Z.',
     'Evidence bundles and reviewable regression candidates', 'handoff bundle', 10, 30),
    ('Z.05', 'Free-test enabler tooling',
     'Build or improve reusable capture, setup or observation tools specific to exploratory work.',
     'A reviewed tool increment with reuse/maintenance records.',
     'Only tooling actually booked under Z belongs here. Do not duplicate manager-row AI tooling allocation.',
     'Tool increments, reuse, measured manual minutes saved', 'tool increment', 120, 480),
)


def _literal(value):
    if isinstance(value, str) and value.startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def _sheet(workbook, name, title, note, headers, rows):
    if name in workbook.sheetnames:
        raise ValueError(f'Analysis sheet already exists: {name}')
    sheet = workbook.create_sheet(name)
    sheet.append([title])
    sheet.append([note])
    sheet.append(['Input cells: amber. Source references/evidence are not time measurements.'])
    sheet.append(headers)
    for row in rows:
        sheet.append([_literal(value) for value in row])
    sheet.freeze_panes = 'C5'
    sheet.auto_filter.ref = f'A4:{sheet.cell(4, len(headers)).column_letter}{max(5, sheet.max_row)}'
    sheet.sheet_view.showGridLines = False
    for row in sheet.iter_rows():
        for cell in row:
            cell.font = Font(name='Calibri', size=10)
            cell.alignment = Alignment(vertical='top', wrap_text=True)
    for cell in sheet[4]:
        cell.fill = PatternFill('solid', fgColor='245B50')
        cell.font = Font(name='Calibri', size=10, bold=True, color='FFFFFF')
    sheet['A1'].font = Font(name='Calibri', size=15, bold=True, color='245B50')
    for row_number in (1, 2, 3):
        sheet.merge_cells(start_row=row_number, start_column=1, end_row=row_number, end_column=len(headers))
    sheet.row_dimensions[1].height = 25
    sheet.row_dimensions[2].height = 42
    sheet.row_dimensions[3].height = 30
    sheet.row_dimensions[4].height = 42
    for column in range(1, len(headers) + 1):
        sheet.column_dimensions[sheet.cell(4, column).column_letter].width = 23
    sheet.print_title_rows = '1:4'
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    return sheet


def _input(cell, note):
    cell.fill = PatternFill('solid', fgColor='FFF0C2')
    cell.comment = Comment(note, 'Planning model')


def _inline_breakdown(workbook, source, library, self_contained=False):
    start = max(67, source.max_row + 4)
    header_row = start + 4
    first_stage = header_row + 1
    last_stage = first_stage + len(STAGES) - 1
    footer = last_stage + 1
    groups = ((1, 2), (3, 8), (9, 12), (13, 16), (17, 20), (21, 24), (25, 30), (31, 36))

    def band(row, left, right, value, heading=False):
        source.merge_cells(start_row=row, start_column=left, end_row=row, end_column=right)
        cell = source.cell(row, left, value)
        cell.font = Font(name='Calibri', size=11, bold=heading, color='FFFFFF' if heading else '173D35')
        cell.fill = PatternFill('solid', fgColor='245B50' if heading else 'F2F7F4')
        cell.alignment = Alignment(vertical='top', wrap_text=True)
        return cell

    band(start, 1, 36, f'FREE TEST: VERTICAL WORK-PACKAGE BREAKDOWN ({STAGES[0][0]}-{STAGES[-1][0]})', True)
    source.row_dimensions[start].height = 30
    band(start + 1, 1, 36, 'This section expands source column Z on this same tab. Original personnel rows, source allocations and other work packages are unchanged. Annual hours are budget, not measured effort.')
    source.row_dimensions[start + 1].height = 36
    band(start + 2, 1, 4, 'Source Z allocation (FTE)')
    band(start + 2, 5, 8, '=SUM($Z$3:$Z$28)' if self_contained else '=FT_Model!B7').number_format = '0.00'
    band(start + 2, 9, 12, 'Annual budget (h)')
    band(start + 2, 13, 16, f'=E{start + 2}*U{start + 2}' if self_contained else '=FT_Model!B10').number_format = '0.0'
    band(start + 2, 17, 20, 'Hours/FTE/year assumption')
    capacity_cell = band(start + 2, 21, 24, 1840 if self_contained else '=FT_Model!B8')
    capacity_cell.number_format = '0'
    if self_contained:
        _input(capacity_cell, 'Planning capacity assumption, not measured hours. Source Global effort Eval uses 1,840 and an alternative 1,984 hours/FTE/year.')
    band(start + 2, 25, 36, f"Evidence: {library['candidateTicketCount']} unique candidate tickets / {len(library['patterns'])} overlapping patterns. Not a same-period productivity denominator.")
    source.row_dimensions[start + 2].height = 38
    instruction = f'Fill the amber I{first_stage}:I{last_stage} cells with confirmed internal shares of Z (total 100%). Blank shares mean not allocated; merged-stage durations need calibration. Other minute ranges are illustrative assumptions, not measured times.'
    if self_contained:
        instruction += f' Annual capacity assumptions are editable in U{start + 2} and E{footer + 2}.'
    band(start + 3, 1, 36, instruction)
    source.row_dimensions[start + 3].height = 38
    headers = ('Child WP', 'Activity / work content', 'Share within Z (input)', 'Annual budget hours',
               'Unit time assumption', 'Work product', 'Data / output support', 'Boundary: do not count twice')
    for (left, right), title in zip(groups, headers):
        band(header_row, left, right, title, True)
    source.row_dimensions[header_row].height = 36
    effort = library['candidateEffort']
    supports = (
        f"{len(library['patterns'])} compound pattern families / {library['candidateTicketCount']} unique candidate records; goal and design quantities are not measured.",
        f"{library['candidateTicketCount']} candidate discovery records; setup quantities, active minutes and passed/no-finding sessions are not available.",
        f"{effort['reportedAttempts']} reported attempts in {effort['casesWithAttempts']} tickets; {effort['reportedLifecycles']} lifecycle answers in {effort['casesWithLifecycles']} tickets. Not work hours.",
        'Traceable case evidence and regression candidates; formal testcase conversion is not yet demonstrated.',
        'Tool increments, reuse and measured savings not quantified in the current evidence.',
    )
    time_model = None if self_contained else workbook['FT_Time_Model']
    for offset, stage in enumerate(STAGES):
        row = first_stage + offset
        code, activity, description, output, boundary, metric, unit, low, high = stage
        annual_formula = f'=IF(I{row}="","",$M${start + 2}*I{row})' if self_contained else f'=IF(I{row}="","",FT_Model!$B$10*I{row})'
        values = (code, activity + '\n' + description, None,
              annual_formula,
                  f'{low}-{high} minutes / {unit}; planning assumption only' if low is not None and high is not None else f'Not calibrated; measure active minutes / {unit}. Do not add former-stage assumptions.',
                  output, supports[offset], boundary)
        for (left, right), value in zip(groups, values):
            band(row, left, right, value)
        _input(source.cell(row, 9), 'Confirmed internal share of the existing Z budget; not a new personnel allocation. Leave blank until calibrated or owner-approved.')
        source.cell(row, 9).number_format = '0.0%'
        source.cell(row, 13).number_format = '0.0'
        source.row_dimensions[row].height = 100
        if time_model is not None:
            time_row = 5 + offset
            time_model.cell(time_row, 3, f'=IF(\'{SOURCE_SHEET}\'!I{row}="","",\'{SOURCE_SHEET}\'!I{row})')
            time_model.cell(time_row, 3).fill = PatternFill('solid', fgColor='F2F7F4')
            time_model.cell(time_row, 3).comment = Comment(f'Linked input: enter the share on {SOURCE_SHEET}!I{row}, not here.', 'Planning model')
    shares = DataValidation(type='decimal', operator='between', formula1=0, formula2=1, allow_blank=True)
    shares.showErrorMessage = True
    shares.errorTitle, shares.error = 'Invalid Z subpackage share', f'Enter a fraction between 0 and 1; all {len(STAGES)} shares must total 100%.'
    source.add_data_validation(shares)
    shares.add(f'I{first_stage}:I{last_stage}')
    band(footer, 1, 8, 'TOTAL INTERNAL Z ALLOCATION', True)
    band(footer, 9, 12, f'=SUM(I{first_stage}:I{last_stage})').number_format = '0.0%'
    band(footer, 13, 16, f'=IF(COUNT(I{first_stage}:I{last_stage})=0,"",SUM(M{first_stage}:M{last_stage}))').number_format = '0.0'
    band(footer, 17, 36, f'=IF(COUNT(I{first_stage}:I{last_stage})=0,"NOT ALLOCATED - enter confirmed shares",IF(ABS(SUM(I{first_stage}:I{last_stage})-1)<0.000001,"READY - original Z capacity preserved","REVIEW - shares must total 100%"))')
    source.row_dimensions[footer].height = 36
    if self_contained:
        alternative_row = footer + 2
        band(alternative_row, 1, 4, 'Alternative hours/FTE/year')
        alternative = band(alternative_row, 5, 8, 1984)
        _input(alternative, 'Alternative capacity scenario; not added to the main annual budget.')
        band(alternative_row, 9, 12, 'Alternative annual budget (h)')
        band(alternative_row, 13, 16, f'=$E${start + 2}*E{alternative_row}').number_format = '0.0'
        band(alternative_row, 17, 36, 'Alternative scenario only. One source personnel row is assumed to represent one FTE for this planning conversion.')
        band(footer + 3, 1, 36, 'Booking boundary: do not add road-test V/W, formal testcase Y, flash R/S/T, ticket AA/AB, meetings or logistics again to Z. Tooling already booked elsewhere is excluded.')
        band(footer + 4, 1, 36, f"Evidence scope: {library['candidateTicketCount']} unique rule-candidate ticket records, {len(library['patterns'])} overlapping compound patterns, {effort['reportedAttempts']} reported attempts and {effort['reportedLifecycles']} lifecycle answers. Not accepted-defect counts, independent events or work hours.")
        band(footer + 5, 1, 36, 'Actual stage hours are not available. Calibrate internal Z shares and record active/waiting minutes; candidate ticket counts must not be multiplied into every stage. All formulas and inputs for this block are on this tab.')
        for row in range(alternative_row, footer + 6):
            source.row_dimensions[row].height = 40
    else:
        links = (("FT_WBS", 'Detailed work-package boundaries'), ("FT_Time_Model", 'Quantity / duration assumptions'),
                 ("FT_Pattern_Output", 'Pattern-output evidence'), ("FT_Case_Evidence", 'Case-level evidence'))
        for index, (target, label) in enumerate(links, footer + 2):
            cell = band(index, 1, 12, label)
            cell.hyperlink = Hyperlink(ref=cell.coordinate, location=f"'{target}'!A1", display=label)
            cell.font = Font(name='Calibri', size=11, color='0563C1', underline='single')
    source['Z2'].hyperlink = Hyperlink(ref='Z2', location=f"'{SOURCE_SHEET}'!A{start}", display=str(source['Z2'].value))
    source['Z2'].comment = Comment(f'Free-test child work packages are on this same sheet, rows {start}:{footer}. Click to jump.', 'Free-test breakdown')
    header_font = copy(source['Z2'].font)
    header_font.color = '0563C1'
    header_font.underline = 'single'
    source['Z2'].font = header_font
    source.sheet_view.topLeftCell = f'A{max(1, start - 2)}'
    source.sheet_view.selection = [Selection(activeCell=f'A{start}', sqref=f'A{start}')]
    if self_contained:
        source.sheet_view.tabSelected = True
    else:
        for sheet in workbook.worksheets:
            sheet.sheet_view.tabSelected = sheet is source
    workbook.active = workbook.sheetnames.index(SOURCE_SHEET)
    return start


def build_free_test_effort_workbook(source_path: str | Path, evidence_path: str | Path, output_path: str | Path, *, only_effort_sheet: bool = False) -> dict:
    source_path, evidence_path, output_path = Path(source_path), Path(evidence_path), Path(output_path)
    if source_path.resolve() == output_path.resolve():
        raise ValueError('Refusing to overwrite the supplied source workbook')
    if output_path.exists():
        raise FileExistsError(f'Output already exists: {output_path}')
    source_hash = sha256(source_path.read_bytes()).hexdigest()
    evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
    workbook = load_workbook(source_path)
    source = workbook[SOURCE_SHEET]
    if 'free test' not in str(source['Z2'].value).lower():
        raise ValueError('Expected source Z2 to contain the Free test work package')
    people_rows = [row for row in range(3, 29) if source.cell(row, 4).value]
    if not people_rows:
        raise ValueError('No personnel allocation rows found in source rows 3:28')
    allocation = sum(float(source.cell(row, 26).value or 0) for row in people_rows if isinstance(source.cell(row, 26).value, (int, float)))
    active_people = sum(isinstance(source.cell(row, 26).value, (int, float)) and source.cell(row, 26).value > 0 for row in people_rows)
    library = evidence['discoveryPatternLibrary']
    effort = library['candidateEffort']
    last_time_row = 4 + len(STAGES)
    if only_effort_sheet:
        inline_start = _inline_breakdown(workbook, source, library, self_contained=True)
        workbook.calculation = CalcProperties(calcId=191029, calcMode='auto', fullCalcOnLoad=True, forceFullCalc=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(output_path)
        workbook.close()
        if sha256(source_path.read_bytes()).hexdigest() != source_hash:
            raise RuntimeError('Source workbook changed while the analysis was being generated')
        return {'outputPath': str(output_path), 'sourceSha256': source_hash, 'onlyEffortSheet': True,
                'inlineStartRow': inline_start, 'personnelRows': len(people_rows),
                'peopleWithFreeTestAllocation': active_people, 'freeTestAllocationFte': allocation,
                'annualBudget1840': round(allocation * 1840, 2), 'annualBudget1984': round(allocation * 1984, 2),
                'candidateTickets': library['candidateTicketCount'], 'patternFamilies': len(library['patterns']),
                'timeStatus': 'Self-contained effort-tab planning inputs; no measured stage hours inferred.'}
    model = _sheet(workbook, 'FT_Model', 'Free-test scope, budget and evidence basis',
                   '2026 source allocations are planning percentages, not measured hours. Evidence is a separate historical ticket cohort; do not calculate annual productivity from the two.',
                   ['Parameter', 'Value / formula', 'Basis / interpretation'], [])
    model_rows = [
        ('Personnel allocation rows', len(people_rows), 'Source D3:D28; one row does not independently prove one full FTE.'),
        ('Rows with Free-test allocation', active_people, 'Source Z3:Z28.'),
        ('Planned Free-test FTE equivalent', '=SUM(FT_People!G5:G' + str(4 + len(people_rows)) + ')', 'Weighted personnel FTE assumptions x source Free-test shares; not new headcount.'),
        ('Annual capacity, hours/FTE', 1840, 'Planning assumption already used in Global effort Eval!B19 and its formulas; confirm the capacity basis.'),
        ('Alternative annual capacity', 1984, 'Alternative planning assumption shown in Global effort Eval; not combined with the 1,840-hour scenario.'),
        ('Annual Free-test budget, hours', '=B7*B8', 'Capacity allocation, not measured work hours.'),
        ('Alternative annual budget, hours', '=B7*B9', 'Same allocation with the alternative annual capacity.'),
        ('Team capacity share', '=IFERROR(B7/SUM(FT_People!E5:E' + str(4 + len(people_rows)) + '),0)', 'Depends on the assumed personnel FTE weights.'),
        ('Source workbook', str(source_path), 'Original sheets and values are retained in this analysis copy.'),
        ('Evidence cohort generated', evidence.get('scope', {}).get('cohortGeneratedAt') or evidence.get('scope', {}).get('generatedAt', ''), 'Ticket/script cohort date; not an annual execution denominator.'),
        ('Free-test ticket records', evidence['coverage']['freeTestTickets'], 'Reported discoveries under the agreed no-testcase-link policy; not independent accepted defects.'),
        ('Tickets with identified actions', evidence['coverage']['freeTestWithActions'], 'Parser coverage, not total test coverage.'),
        ('Compound-path candidates', library['candidateTicketCount'], 'Unique candidate ticket IDs, not independent test designs.'),
        ('Compound pattern families', len(library['patterns']), 'Overlapping families, not additive work packages.'),
        ('Candidate reported attempts', effort['reportedAttempts'], f"Answer coverage: {effort['casesWithAttempts']} candidate tickets; not hours or unique execution events."),
        ('Candidate reported lifecycle runs', effort['reportedLifecycles'], f"Answer coverage: {effort['casesWithLifecycles']} candidate tickets; active and waiting time unknown."),
        ('Measured Free-test hours', None, 'Not available. Populate activity logs; do not infer from attempts or ticket timestamps.'),
        ('Allocated Z subpackage shares', f'=SUM(FT_Time_Model!C5:C{last_time_row})', 'Leave unallocated until time sampling or owner-approved assumptions exist.'),
        ('Share-model readiness', f'=IF(COUNT(FT_Time_Model!C5:C{last_time_row})=0,"NOT ALLOCATED",IF(ABS(B22-1)<0.000001,"READY","REVIEW SHARES"))', 'Internal Z shares must sum to 100%; no source total is automatically increased.'),
        ('Source SHA256', source_hash, 'Verifies the original file was not modified.'),
        ('Evidence file', str(evidence_path), 'Case-level actions and confirmation answer provenance.'),
    ]
    for row_number, values in enumerate(model_rows, 5):
        for column, value in enumerate(values, 1):
            model.cell(row_number, column, value)
        model.row_dimensions[row_number].height = 42
    model.column_dimensions['A'].width = 38
    model.column_dimensions['B'].width = 48
    model.column_dimensions['C'].width = 96
    for coordinate in ('B8', 'B9'):
        _input(model[coordinate], 'Scenario input, not a measured duration. Use one annual-capacity basis at a time.')
    model['B12'].number_format = '0.0%'
    model['B22'].number_format = '0.0%'

    people = _sheet(workbook, 'FT_People', 'Source allocation and capacity conversion',
                    'Personnel FTE defaults to 1 only as a transparent planning assumption. Do not normalize source allocations or interpret the result as new headcount.',
                    ['Source row', 'Name', 'Role', 'Market by role text', 'Personnel FTE input', 'Source Z share', 'Planned FT FTE', 'FT hours at main basis', 'FT hours at alt basis', 'Source reference', 'Source M:AE total', 'Allocation check'], [])
    for target_row, source_row in enumerate(people_rows, 5):
        role = str(source.cell(source_row, 3).value or '')
        values = [source_row, source.cell(source_row, 4).value, role,
                  'HK/TW' if any(value in role.upper() for value in ('HK', 'TW')) else 'Mainland',
                  1, f"='{SOURCE_SHEET}'!Z{source_row}", f'=E{target_row}*F{target_row}',
                  f'=G{target_row}*FT_Model!$B$8', f'=G{target_row}*FT_Model!$B$9',
                  f'{SOURCE_SHEET}!Z{source_row}', f"=SUM('{SOURCE_SHEET}'!M{source_row}:AE{source_row})",
                  f'=IF(ABS(K{target_row}-1)<0.000001,"OK","REVIEW SOURCE ALLOCATION")']
        for column, value in enumerate(values, 1):
            people.cell(target_row, column, value)
        _input(people.cell(target_row, 5), 'Default 1 FTE is an assumption; replace for part-time, contractor capacity or availability if applicable.')
        for column in (6, 11):
            people.cell(target_row, column).number_format = '0.0%'
    people.column_dimensions['B'].width = 24
    people.column_dimensions['C'].width = 30

    wbs_rows = [[code, activity, description, output, boundary, metric, unit,
                 'Z: internal child only; adjacent-column boundaries apply', 'Planning decomposition, not measured stage hours']
                for code, activity, description, output, boundary, metric, unit, low, high in STAGES]
    wbs = _sheet(workbook, 'FT_WBS', 'Vertical Free-test work-package decomposition',
                 'WBS stages describe the work chain. Test-method families are horizontal tags across stages and must not be added as separate FTE rows.',
                 ['Child WP', 'Activity', 'Work content', 'Work product', 'Boundary / no double counting', 'Output measure', 'Measurement unit', 'Primary booking', 'Evidence status'], wbs_rows)
    for column in ('C', 'D', 'E', 'F'):
        wbs.column_dimensions[column].width = 58
    for row in range(5, wbs.max_row + 1):
        wbs.row_dimensions[row].height = 90

    time_rows = [[code, activity, None, low, high, unit, None, None,
                  'Planning assumption only; illustrative unmeasured range. Confirm through sampled active minutes.',
                  None, None, None, None, None]
                 for code, activity, description, output, boundary, metric, unit, low, high in STAGES]
    time_model = _sheet(workbook, 'FT_Time_Model', 'Time model: allocation and optional bottom-up estimate',
                        'C shares link to amber inputs on Effort Breakdown Details. J/K measurements and L quantities start blank. D/E are illustrative assumptions, not observations. Do not seed every stage with ticket counts.',
                        ['Child WP', 'Activity', 'Share of Z input', 'Low minutes assumption', 'High minutes assumption', 'Unit', 'Allocated annual hours', 'Allocated FTE', 'Time basis', 'Measured minutes/unit', 'Measured total hours', 'Activity quantity input', 'Bottom-up low hours', 'Bottom-up high hours'], time_rows)
    for row in range(5, last_time_row + 1):
        time_model.cell(row, 7, f'=IF(C{row}="","",FT_Model!$B$10*C{row})')
        time_model.cell(row, 8, f'=IF(C{row}="","",FT_Model!$B$7*C{row})')
        time_model.cell(row, 13, f'=IF(OR(L{row}="",D{row}=""),"",L{row}*D{row}/60)')
        time_model.cell(row, 14, f'=IF(OR(L{row}="",E{row}=""),"",L{row}*E{row}/60)')
        time_model.cell(row, 3).number_format = '0.0%'
        for column in (3, 4, 5, 10, 11, 12):
            _input(time_model.cell(row, column), 'Unconfirmed planning input or future measurement. Do not present as an observed source fact.')
        time_model.row_dimensions[row].height = 70
    share_validation = DataValidation(type='decimal', operator='between', formula1=0, formula2=1, allow_blank=True)
    share_validation.errorTitle, share_validation.error, share_validation.showErrorMessage = 'Invalid share', 'Use a number from 0 to 1.', True
    time_model.add_data_validation(share_validation)
    share_validation.add(f'C5:C{last_time_row}')
    time_model.column_dimensions['B'].width = 45
    time_model.column_dimensions['I'].width = 66

    pattern_rows = [[pattern['patternId'], pattern['pattern'], pattern['caseCount'], pattern['reportedAttempts'],
                     pattern['casesWithAttempts'], pattern['reportedLifecycles'], pattern['casesWithLifecycles'],
                     pattern.get('testcaseSupportCount', 0), pattern.get('strategySections', ''),
                     'Z.01 / Z.02 / Z.03; activity booking is not proven by a ticket tag',
                     'Overlapping candidate families; not hours or unique independent defects']
                    for pattern in library['patterns']]
    patterns = _sheet(workbook, 'FT_Pattern_Output', 'Evidence: discovered patterns and recorded confirmation volume',
                      'Historical evidence, not annualized productivity. Testcase support is in the selected script sample, not the whole testcase library.',
                      ['Pattern ID', 'Pattern', 'Candidate tickets', 'Reported attempts', 'Attempt field coverage', 'Reported lifecycles', 'Lifecycle field coverage', 'Testcase support', 'Strategy sections', 'Possible WBS relevance', 'Interpretation boundary'], pattern_rows)
    patterns.column_dimensions['B'].width = 52
    patterns.column_dimensions['J'].width = 62
    patterns.column_dimensions['K'].width = 62
    unique_cases = {}
    memberships = {}
    for pattern in library['patterns']:
        for case in pattern['caseStudies']:
            unique_cases[case['defectId']] = case
            memberships.setdefault(case['defectId'], []).append(pattern['pattern'])
    case_rows = []
    for defect_id, case in sorted(unique_cases.items()):
        provenance = case.get('confirmationEvidence', {})
        case_rows.append([defect_id, case['name'], '; '.join(memberships[defect_id]), case['discoveryPath'],
                          case.get('methodInterpretation', ''), case.get('valueHypothesis', ''),
                          case.get('reproductionAttempts'), provenance.get('reproductionAttempts', {}).get('source', ''),
                          case.get('lifecycleRuns'), provenance.get('lifecycleRuns', {}).get('source', ''),
                          case.get('reviewStatus', 'rule_candidate_requires_review'),
                          'No duration or work-package ownership is inferred from this ticket.',
                          f'https://octane-prod.bmwgroup.net/ui/?p=1002/2001#/entity-navigation?entityType=work_item&id={defect_id}'])
    cases = _sheet(workbook, 'FT_Case_Evidence', 'Unique candidate evidence: path, method, value and confirmation',
                   'One row per ticket; pattern memberships can overlap. A reported attempt/lifecycle answer is not a time measurement or proof of a separate execution event.',
                   ['Defect ID', 'Finding', 'Pattern memberships', 'Recorded discovery path', 'Method interpretation', 'Value hypothesis', 'Attempts', 'Attempt source', 'Lifecycles', 'Lifecycle source', 'Review status', 'Time / booking boundary', 'Octane link'], case_rows)
    for column in ('B', 'C', 'D', 'E', 'F', 'L'):
        cases.column_dimensions[column].width = 58
    for row in range(5, cases.max_row + 1):
        cases.row_dimensions[row].height = 95

    log = _sheet(workbook, 'FT_Activity_Log', 'Calibration log: one primary booking per activity',
                 'Measure active minutes separately from waiting; log passed/no-finding sessions too. Road-test or ticket work already booked outside Z must not be added to Z again.',
                 ['Date', 'Person', 'Primary source column', 'Z child WP if applicable', 'Session / activity ID', 'Outcome / work product', 'Active minutes', 'Waiting minutes', 'Active hours', 'Ticket / evidence reference', 'Method tags (non-additive)'], [])
    for row in range(5, 35):
        log.cell(row, 9, f'=IF(G{row}="","",G{row}/60)')
        for column in (1, 2, 3, 4, 5, 6, 7, 8, 10, 11):
            _input(log.cell(row, column), 'Future observed activity entry; no measured values are prefilled.')
    booking = DataValidation(type='list', formula1='"Z,V,W,Y,M,N,P,R,S,T,AA,AB,X,AE,AI"', allow_blank=True)
    log.add_data_validation(booking)
    booking.add('C5:C34')
    log.auto_filter.ref = 'A4:K34'

    inline_start = _inline_breakdown(workbook, source, library)
    workbook.calculation = CalcProperties(calcId=191029, calcMode='auto', fullCalcOnLoad=True, forceFullCalc=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)
    workbook.close()
    if sha256(source_path.read_bytes()).hexdigest() != source_hash:
        raise RuntimeError('Source workbook changed while the analysis was being generated')
    return {'outputPath': str(output_path), 'sourceSha256': source_hash, 'personnelRows': len(people_rows),
            'peopleWithFreeTestAllocation': active_people, 'freeTestAllocationFte': allocation,
            'annualBudget1840': round(allocation * 1840, 2), 'annualBudget1984': round(allocation * 1984, 2),
            'candidateTickets': library['candidateTicketCount'], 'patternFamilies': len(library['patterns']),
            'inlineStartRow': inline_start,
            'timeStatus': 'Planning allocation and unmeasured illustrative unit ranges; no actual stage hours inferred.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--evidence', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--only-effort-sheet', action='store_true')
    args = parser.parse_args()
    print(json.dumps(build_free_test_effort_workbook(args.source, args.evidence, args.output, only_effort_sheet=args.only_effort_sheet), ensure_ascii=False))


if __name__ == '__main__':
    main()