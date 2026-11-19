from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook, load_workbook
import pytest

from backend.analytics.free_test_effort_breakdown import build_free_test_effort_workbook


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    workbook = Workbook()
    source = workbook.active
    source.title = 'Effort Breakdown Details'
    source['A1'] = 'WORKLOAD ESTIMATION (IN %)'
    for cell, value in {'D2': 'Name', 'C2': 'Role', 'Y2': 'Test case excu.', 'Z2': 'Free test (Incl. tooling dev)', 'AA2': 'Ticket creation', 'AB2': 'Ticket Maintain'}.items():
        source[cell] = value
    source['D3'], source['C3'], source['Z3'] = 'Tester A', 'Tester', 0.25
    source['D4'], source['C4'], source['Z4'] = 'Tester B', 'Tester - HK/MO', 0.15
    source['AF3'] = '=SUM(M3:AE3)'
    workbook.create_sheet('Global effort Eval')['B19'] = '1,840 hours per FTE per year'
    input_path = tmp_path / 'source.xlsx'
    workbook.save(input_path)
    evidence_path = tmp_path / 'evidence.json'
    evidence_path.write_text(json.dumps({
        'scope': {'generatedAt': '2026-10-28T00:00:00Z'},
        'coverage': {'freeTestTickets': 2, 'freeTestWithActions': 2, 'testcaseCount': 1},
        'discoveryPatternLibrary': {
            'candidateTicketCount': 1,
            'candidateEffort': {'reportedAttempts': 5, 'casesWithAttempts': 1, 'reportedLifecycles': 2, 'casesWithLifecycles': 1},
            'patterns': [{'patternId': 'example', 'pattern': 'Example pattern', 'caseCount': 1,
                          'reportedAttempts': 5, 'casesWithAttempts': 1, 'reportedLifecycles': 2,
                          'casesWithLifecycles': 1, 'testcaseSupportCount': 1, 'strategySections': '7.3',
                          'caseStudies': [{'defectId': '123', 'name': 'Failure', 'discoveryPath': 'Open then switch.',
                                           'methodInterpretation': 'State contrast', 'valueHypothesis': 'Risk evidence',
                                           'reproductionAttempts': 5, 'lifecycleRuns': 2,
                                           'confirmationEvidence': {}, 'functions': ['Audio']}]}],
        },
    }), encoding='utf-8')
    return input_path, evidence_path


def test_effort_workbook_keeps_source_and_separates_budget_assumptions_from_evidence(tmp_path: Path) -> None:
    source, evidence = _inputs(tmp_path)
    original = source.read_bytes()
    output = tmp_path / 'expanded.xlsx'
    result = build_free_test_effort_workbook(source, evidence, output)
    assert source.read_bytes() == original
    assert result['freeTestAllocationFte'] == pytest.approx(0.4)
    assert result['annualBudget1840'] == pytest.approx(736)
    assert result['annualBudget1984'] == pytest.approx(793.6)
    workbook = load_workbook(output)
    assert workbook['Effort Breakdown Details']['AF3'].value == '=SUM(M3:AE3)'
    assert {'FT_Model', 'FT_WBS', 'FT_People', 'FT_Time_Model', 'FT_Pattern_Output', 'FT_Case_Evidence'} <= set(workbook.sheetnames)
    wbs = workbook['FT_WBS']
    assert wbs.max_row >= 9
    assert workbook['FT_People']['F5'].value == "='Effort Breakdown Details'!Z3"
    assert workbook['FT_Model']['B8'].value == 1840
    assert workbook['FT_Model']['B9'].value == 1984
    assert 'assumption' in workbook['FT_Model']['C8'].value.casefold()
    assert workbook['FT_Time_Model']['C5'].value == '=IF(\'Effort Breakdown Details\'!I72="","",\'Effort Breakdown Details\'!I72)'
    assert 'planning assumption' in workbook['FT_Time_Model']['I5'].value.casefold()
    assert workbook['FT_Time_Model'].max_row == 9
    assert workbook['FT_Model']['B22'].value == '=SUM(FT_Time_Model!C5:C9)'
    workbook.close()


def test_vertical_breakdown_is_visible_and_editable_on_the_requested_source_tab(tmp_path: Path) -> None:
    source, evidence = _inputs(tmp_path)
    output = tmp_path / 'inline.xlsx'
    result = build_free_test_effort_workbook(source, evidence, output)
    workbook = load_workbook(output)
    sheet = workbook['Effort Breakdown Details']
    assert result['inlineStartRow'] == 67
    assert workbook.active.title == sheet.title
    assert sheet.sheet_view.topLeftCell == 'A65'
    assert sheet['A67'].value == 'FREE TEST: VERTICAL WORK-PACKAGE BREAKDOWN (Z.01-Z.05)'
    assert [sheet.cell(row, 1).value for row in range(72, 77)] == [f'Z.0{number}' for number in range(1, 6)]
    assert sheet['I72'].value is None
    assert sheet['I72'].fill.fgColor.rgb.endswith('FFF0C2')
    assert sheet['M72'].value == '=IF(I72="","",FT_Model!$B$10*I72)'
    assert sheet['Z2'].value == 'Free test (Incl. tooling dev)'
    assert sheet['Z2'].hyperlink.location == "'Effort Breakdown Details'!A67"
    assert sheet['AF3'].value == '=SUM(M3:AE3)'
    workbook.close()


def test_inline_breakdown_appends_after_existing_content_without_overwriting_it(tmp_path: Path) -> None:
    source, evidence = _inputs(tmp_path)
    workbook = load_workbook(source)
    workbook['Effort Breakdown Details']['A90'] = 'Existing user content'
    workbook.save(source)
    workbook.close()
    output = tmp_path / 'appended.xlsx'
    result = build_free_test_effort_workbook(source, evidence, output)
    assert result['inlineStartRow'] > 90
    workbook = load_workbook(output)
    assert workbook['Effort Breakdown Details']['A90'].value == 'Existing user content'
    workbook.close()


def test_effort_workbook_refuses_to_overwrite_the_supplied_source(tmp_path: Path) -> None:
    source, evidence = _inputs(tmp_path)
    with pytest.raises(ValueError, match='overwrite'):
        build_free_test_effort_workbook(source, evidence, source)


def test_effort_only_mode_keeps_original_tabs_and_uses_same_sheet_formulas(tmp_path: Path) -> None:
    source, evidence = _inputs(tmp_path)
    original = load_workbook(source)
    original['Global effort Eval']['D7'] = '=2*21'
    original.save(source)
    original.close()
    source_bytes = source.read_bytes()
    output = tmp_path / 'effort_only.xlsx'
    result = build_free_test_effort_workbook(source, evidence, output, only_effort_sheet=True)
    original = load_workbook(source)
    workbook = load_workbook(output)
    assert workbook.sheetnames == original.sheetnames
    assert not any(name.startswith('FT_') for name in workbook.sheetnames)
    assert result['onlyEffortSheet'] is True
    sheet = workbook['Effort Breakdown Details']
    assert sheet['E69'].value == '=SUM($Z$3:$Z$28)'
    assert sheet['U69'].value == 1840
    assert sheet['M69'].value == '=E69*U69'
    assert sheet['M72'].value == '=IF(I72="","",$M$69*I72)'
    assert sheet['I72'].value is None
    assert [sheet.cell(row, 3).value.split('\n')[0] for row in range(72, 77)] == [
        'Exploration goals and scenario design',
        'Exploration setup and discovery execution',
        'Pre-ticket reproduction and trigger isolation',
        'Exploration evidence and regression suggestions',
        'Free-test enabler tooling',
    ]
    assert sheet['A77'].value == 'TOTAL INTERNAL Z ALLOCATION'
    assert sheet['I77'].value == '=SUM(I72:I76)'
    assert sheet['M77'].value == '=IF(COUNT(I72:I76)=0,"",SUM(M72:M76))'
    assert 'SUM(I72:I76)' in sheet['Q77'].value
    assert [str(rule.sqref) for rule in sheet.data_validations.dataValidation] == ['I72:I76']
    assert all(sheet.cell(row, 9).value is None for row in range(72, 77))
    assert 'Not calibrated' in sheet['Q72'].value
    assert 'Not calibrated' in sheet['Q73'].value
    assert sheet['E79'].value == 1984
    assert sheet['M79'].value == '=$E$69*E79'
    assert workbook.calculation.calcMode == 'auto'
    assert workbook['Global effort Eval']['D7'].value == '=2*21'
    for row in sheet.iter_rows():
        for cell in row:
            assert 'FT_' not in str(cell.value or '')
            if cell.hyperlink:
                assert 'FT_' not in str(cell.hyperlink.location or '')
    assert source.read_bytes() == source_bytes
    original.close()
    workbook.close()


def _matrix_inputs(tmp_path: Path) -> tuple[Path, Path, list[Path]]:
    source, evidence = _inputs(tmp_path)
    personnel = (
        'Test require. Collection', 'Test Case Planning', 'Q-Gate', 'Test Focus mgmt',
        'Test Report summary', 'SW flash prep.', 'Car flash (IuK)',
        'Check in/ out after flashing\n(Trouble shooting)', 'TSP flash report',
        'Road test weekly', 'Road test long dist.', 'Weekly issue sync',
        'Test case excu.', 'Free test (Incl. tooling dev)', 'Ticket creation',
        'Ticket Maintain', 'Logistic', 'Platform share',
        'TPM meeting (case/ ticket KPI monitor)',
    )
    managers = (
        'Test requirement review', 'Test Case review ', 'Defect alignmment/tracking',
        'On-demand test coordinate', 'AIDA Rating test report', 'SW flash prep/inspection',
        'Test car arrangement', 'Check in/ out after flashing\n(Trouble shooting)',
        'TSP Approval', 'Road test weekly', 'Road test long dist.', 'Weekly issue sync',
        'Test case excu.', 'Free test (Incl. tooling dev)', 'Ticket creation',
        'Ticket Maintain', 'Logistic', 'Platform Coordination',
        'TPM meeting (case/ ticket KPI monitor)', 'Project aligment meeting',
        'Partner management', 'Project status (non-testing, CTP, MDR...)',
        'TA / Data analysis AI tooling development',
    )
    workbook = load_workbook(source)
    sheet = workbook['Effort Breakdown Details']
    for column, value in enumerate(personnel, 13):
        sheet.cell(2, column, value)
    for column, value in enumerate(managers, 13):
        sheet.cell(33, column, value)
    workbook.save(source)
    workbook.close()
    data = json.loads(evidence.read_text(encoding='utf-8'))
    data['methodComparison'] = [{'method': 'Input / language / command variation', 'freeTestTickets': 7}]
    evidence.write_text(json.dumps(data), encoding='utf-8')
    strategy_paths = []
    for page_id, title, version in (
        ('4447601857', 'DTSV CN Test Strategy - Validation Beyond', 89),
        ('8681681592', 'Test_focus_CW34_2026', 14),
        ('8735186775', 'Test_focus_CW36_2026', 24),
    ):
        path = tmp_path / f'{page_id}.json'
        path.write_text(json.dumps({
            'id': page_id, 'title': title,
            'version': {'number': version, 'when': '2026-01-01T00:00:00Z'},
            'body': {'storage': {'value': '<p>Drating</p><h2>7.3 Experience-based Testing</h2><p>Exploratory testing targets corner and cross function cases.</p>'}},
        }), encoding='utf-8')
        strategy_paths.append(path)
    return source, evidence, strategy_paths


def test_work_package_matrix_covers_all_work_types_without_changing_source(tmp_path: Path) -> None:
    from backend.analytics.work_package_matrix import build_work_package_matrix

    source, evidence, strategies = _matrix_inputs(tmp_path)
    source_bytes = source.read_bytes()
    output = tmp_path / 'matrix.xlsx'
    result = build_work_package_matrix(source, evidence, strategies, output)
    assert result['workTypeCount'] == 32
    assert result['sourceHeaderCount'] == 42
    assert result['packageCount'] >= 40
    assert result['sharedPackageCount'] > 0
    assert source.read_bytes() == source_bytes
    workbook = load_workbook(output)
    assert len(workbook.sheetnames) == 1
    sheet = workbook.active
    assert sheet.sheet_state == 'visible'
    assert sheet.freeze_panes == 'E8'
    assert sheet.auto_filter.ref.startswith('A7:')
    references = {reference for column in result['workColumns'] for reference in column['sourceRefs']}
    assert len(references) == 42 and {'M2', 'M33', 'AI33'} <= references
    assert any(column['sourceRefs'] == ['Y2', 'Y33'] for column in result['workColumns'])
    for column in result['workColumns']:
        assert any(sheet.cell(row, column['column']).value for row in range(8, sheet.max_row + 1)), column
        assert sheet.cell(7, column['column']).comment
    assert all(cell.data_type != 'f' for row in sheet for cell in row)
    input_row = next(row for row in range(8, sheet.max_row + 1) if sheet.cell(row, 1).value == 'FREE-01')
    input_evidence = sheet.cell(input_row, 6 + result['workTypeCount']).value
    assert ' 7\uff1b' in input_evidence
    assert '484' not in input_evidence
    workbook.close()


def test_work_package_matrix_colors_shared_packages_and_preserves_conditional_actions(tmp_path: Path) -> None:
    from backend.analytics.work_package_matrix import build_work_package_matrix

    source, evidence, strategies = _matrix_inputs(tmp_path)
    result = build_work_package_matrix(source, evidence, strategies, tmp_path / 'matrix.xlsx')
    workbook = load_workbook(tmp_path / 'matrix.xlsx')
    sheet = workbook.active
    colors = []
    conditional = False
    for row in range(8, sheet.max_row + 1):
        cells = [sheet.cell(row, column['column']) for column in result['workColumns']]
        populated = [cell for cell in cells if cell.value]
        assert populated
        conditional = conditional or any(str(cell.value).startswith('\u6761\u4ef6') for cell in populated)
        if len(populated) > 1:
            assert len({cell.fill.fgColor.rgb for cell in populated}) == 1
            colors.append(populated[0].fill.fgColor.rgb)
        assert all(cell.comment and 'Proposed' in cell.comment.text for cell in populated)
    assert len(colors) == len(set(colors))
    assert conditional
    workbook.close()


def test_work_package_matrix_refuses_to_overwrite_inputs_or_existing_outputs(tmp_path: Path) -> None:
    from backend.analytics.work_package_matrix import build_work_package_matrix

    source, evidence, strategies = _matrix_inputs(tmp_path)
    with pytest.raises(ValueError, match='overwrite'):
        build_work_package_matrix(source, evidence, strategies, source)
    output = tmp_path / 'matrix.xlsx'
    output.write_bytes(b'Existing output')
    with pytest.raises(FileExistsError):
        build_work_package_matrix(source, evidence, strategies, output)
    assert output.read_bytes() == b'Existing output'


@pytest.mark.parametrize('invalid_source', ['missing_weekly_pages', 'unreviewed_version'])
def test_work_package_matrix_rejects_incomplete_or_unreviewed_strategy_sources(tmp_path: Path, invalid_source: str) -> None:
    from backend.analytics.work_package_matrix import build_work_package_matrix

    source, evidence, strategies = _matrix_inputs(tmp_path)
    if invalid_source == 'missing_weekly_pages':
        strategies = strategies[:1]
    else:
        page = json.loads(strategies[0].read_text(encoding='utf-8'))
        page['version']['number'] = 90
        strategies[0].write_text(json.dumps(page), encoding='utf-8')
    with pytest.raises(ValueError, match='strategy|version'):
        build_work_package_matrix(source, evidence, strategies, tmp_path / 'matrix.xlsx')


def test_work_package_explanations_extend_existing_sheet1_without_changing_user_matrix(tmp_path: Path) -> None:
    from copy import deepcopy

    from backend.analytics.work_package_matrix import add_work_package_explanations, build_work_package_matrix

    source, evidence, strategies = _matrix_inputs(tmp_path)
    output = tmp_path / 'matrix.xlsx'
    build_work_package_matrix(source, evidence, strategies, output)
    workbook = load_workbook(output)
    matrix = workbook.active
    matrix.delete_rows(2, 5)
    matrix.auto_filter.ref = f'A2:AN{matrix.max_row}'
    matrix.freeze_panes = 'AA3'
    notes = workbook.create_sheet('Sheet1')
    notes['A2'] = 'Existing user explanation'
    notes['M17'] = 's'
    original_cells = {cell.coordinate: (cell.value, deepcopy(cell._style)) for row in matrix for cell in row}
    original_view = deepcopy(matrix.sheet_view)
    result = add_work_package_explanations(matrix, notes)
    assert workbook.sheetnames == [matrix.title, 'Sheet1']
    assert notes['A2'].value == 'Existing user explanation'
    assert notes['M17'].value == 's'
    assert matrix.freeze_panes == 'AA3'
    assert matrix.sheet_view == original_view
    for row in matrix:
        for cell in row:
            assert (cell.value, cell._style) == original_cells[cell.coordinate]
    assert result['packageCount'] == 55
    assert result['prefixCount'] == 15
    assert result['domainCount'] == 9
    assert result['matrixHeaderRow'] == 2
    text = '\n'.join(str(cell.value or '') for row in notes for cell in row)
    assert 'WP ID' in text and 'Work Domain' in text
    assert 'Identifier' in text and 'Work Package' in text
    assert 'Requirements' in text and 'Environment' in text
    assert 'FREE-01' in text and 'CASE-02' in text
    assert '\u4e0d\u8868\u793a\u4f18\u5148\u7ea7' in text
    assert '\u4e0d\u662f\u90e8\u95e8' in text
    assert any(cell.hyperlink and matrix.title in (cell.hyperlink.location or '') for row in notes for cell in row)
    workbook.close()


def test_reference_workbook_style_matches_fonts_and_headers_without_changing_matrix_semantics() -> None:
    from copy import copy, deepcopy

    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.hyperlink import Hyperlink

    from backend.analytics.work_package_matrix import apply_reference_workbook_style

    reference = Workbook()
    sample = reference.active
    sample['A1'] = 'Header'
    sample['A1'].font = Font(name='Calibri', size=10, bold=True, color='FFFFFFFF')
    sample['A1'].fill = PatternFill('solid', fgColor='FF1F4E78')
    line = Side(style='hair', color='FFD9E2F3')
    sample['A1'].border = Border(left=line, right=line, top=line, bottom=line)
    sample['A1'].alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
    for row in (2, 3):
        sample.cell(row, 1, 'Body')
        sample.cell(row, 1).font = Font(name='Calibri', size=10, color='FF172B3A')
        sample.cell(row, 1).fill = PatternFill('solid', fgColor='FFFFFFFF')
        sample.cell(row, 1).border = copy(sample['A1'].border)
        sample.cell(row, 1).alignment = copy(sample['A1'].alignment)
    sample['C1'] = 'Title'
    sample['C1'].font = Font(name='Calibri', size=14, bold=True, color='FF172B3A')
    sample['C1'].fill = PatternFill('solid', fgColor='FFE2EFDA')
    workbook = Workbook()
    matrix = workbook.active
    matrix.title = 'Matrix'
    matrix['A1'] = 'Existing title'
    matrix['A1'].font = Font(name='Microsoft YaHei', size=16, bold=True)
    matrix['A2'] = 'WP ID'
    matrix['A2'].font = Font(name='Microsoft YaHei', size=12, bold=True, color='FFFFFFFF')
    matrix['A2'].fill = PatternFill('solid', fgColor='FF245B50')
    for row, code, color in ((3, 'REQ-01', 'FFFFE0A3'), (4, 'REQ-02', 'FFDBEAFE')):
        matrix.cell(row, 1, code)
        matrix.cell(row, 2, 'Existing domain')
        matrix.cell(row, 5, 'Existing action')
        for column in (1, 2, 5):
            matrix.cell(row, column).fill = PatternFill('solid', fgColor=color)
        matrix.row_dimensions[row].height = 40
    matrix['E3'].comment = Comment('Existing source evidence', 'User')
    matrix['E3'].number_format = '0.0%'
    matrix.auto_filter.ref = 'A2:E4'
    matrix.freeze_panes = 'AA3'
    notes = workbook.create_sheet('Sheet1')
    notes['A1'] = 'Open guide'
    notes['A1'].hyperlink = Hyperlink(ref='A1', location="'Sheet1'!A20", display='Open guide')
    notes.merge_cells('A20:H20')
    notes['A20'] = 'Guide heading'
    notes['A20'].font = Font(name='Microsoft YaHei', size=11, bold=True, color='FFFFFFFF')
    notes['A20'].fill = PatternFill('solid', fgColor='FF245B50')
    notes.merge_cells('A21:H21')
    notes['A21'] = 'Existing guide paragraph'
    notes['A21'].font = Font(name='Microsoft YaHei', size=11)
    notes['A21'].fill = PatternFill('solid', fgColor='FFF2F7F4')
    original_values = {(sheet.title, cell.coordinate): cell.value for sheet in workbook for row in sheet for cell in row}
    old_view = deepcopy(matrix.sheet_view)
    old_columns = {sheet.title: {key: dict(value) for key, value in sheet.column_dimensions.items()} for sheet in workbook}
    old_colors = {cell.coordinate: copy(cell.fill) for row in matrix.iter_rows(min_row=3) for cell in row}
    result = apply_reference_workbook_style(workbook, reference)
    assert result['bodyFont'] == 'Calibri' and result['bodySize'] == 10
    assert matrix['A2'].font == copy(sample['A1'].font)
    assert matrix['A2'].fill == copy(sample['A1'].fill)
    assert matrix['A1'].font == copy(sample['C1'].font)
    assert matrix['B3'].font.name == 'Calibri' and matrix['B3'].font.sz == 10
    assert matrix['B3'].alignment.vertical == 'center' and matrix['B3'].alignment.wrap_text
    assert matrix['B3'].border == copy(sample['A2'].border)
    assert notes['A20'].font == copy(sample['A1'].font) and notes['A20'].fill == copy(sample['A1'].fill)
    assert notes['A21'].fill == copy(sample['A2'].fill)
    assert notes['A1'].hyperlink.location == "'Sheet1'!A20"
    assert notes['A1'].font.underline == 'single'
    assert matrix['E3'].comment.text == 'Existing source evidence'
    assert matrix['E3'].number_format == '0.0%'
    assert matrix.sheet_view == old_view and matrix.freeze_panes == 'AA3'
    assert matrix.auto_filter.ref == 'A2:E4'
    assert str(next(iter(notes.merged_cells.ranges))) in {'A20:H20', 'A21:H21'}
    for sheet in workbook:
        assert {key: dict(value) for key, value in sheet.column_dimensions.items()} == old_columns[sheet.title]
        for row in sheet:
            for cell in row:
                assert cell.value == original_values[(sheet.title, cell.coordinate)]
    for row in matrix.iter_rows(min_row=3):
        for cell in row:
            assert cell.fill == old_colors[cell.coordinate]


def test_workbook_navigation_opens_at_layered_guide_and_preserves_matrix_rows(tmp_path: Path) -> None:
    from copy import copy
    import re

    from openpyxl.worksheet.hyperlink import Hyperlink

    from backend.analytics.work_package_matrix import add_work_package_explanations, add_workbook_navigation, build_work_package_matrix

    source, evidence, strategies = _matrix_inputs(tmp_path)
    path = tmp_path / 'matrix.xlsx'
    build_work_package_matrix(source, evidence, strategies, path)
    workbook = load_workbook(path)
    matrix = workbook.active
    for merged in list(matrix.merged_cells.ranges):
        if merged.min_row > 1:
            matrix.unmerge_cells(str(merged))
    matrix.delete_rows(2, 5)
    matrix.auto_filter.ref = f'A2:AN{matrix.max_row}'
    matrix.freeze_panes = 'E3'
    notes = workbook.create_sheet('Sheet1')
    notes['A2'] = 'Existing metadata'
    add_work_package_explanations(matrix, notes)
    notes['A1'].hyperlink = Hyperlink(ref='A1', location="'Sheet1'!A20")
    values = {(sheet.title, cell.coordinate): cell.value for sheet in workbook for row in sheet for cell in row}
    fills = {(sheet.title, cell.coordinate): copy(cell.fill) for sheet in workbook for row in sheet for cell in row}
    result = add_workbook_navigation(workbook)
    assert workbook.active.title == result['guideSheet']
    assert workbook.sheetnames[0] == result['guideSheet']
    assert matrix.freeze_panes == 'E3' and matrix.auto_filter.ref == 'A2:AN57'
    guide = workbook.active
    text = '\n'.join(str(cell.value or '') for row in guide for cell in row)
    assert all(term in text for term in ('\u7b2c\u4e00\u5c42', '\u7b2c\u4e8c\u5c42', '\u7b2c\u4e09\u5c42'))
    assert result['packageCount'] == 55 and result['domainCount'] == 9 and result['workTypeCount'] == 32
    assert all(domain in text for domain in set(matrix.cell(row, 2).value for row in range(3, 58)))
    internal = [cell.hyperlink.location for sheet in workbook for row in sheet for cell in row if cell.hyperlink and cell.hyperlink.location]
    for location in internal:
        match = re.fullmatch(r"'((?:[^']|'')+)'!([A-Z]+\d+)", location)
        assert match, location
        sheet_name, address = match.groups()
        target = workbook[sheet_name.replace("''", "'")][address]
        assert target.value is not None, location
    assert matrix['A1'].hyperlink.location == f"'{result['guideSheet']}'!A1"
    assert notes['A1'].hyperlink.location == result['explanationTarget']
    for key, value in values.items():
        sheet_name, address = key
        assert workbook[sheet_name][address].value == value
        assert copy(workbook[sheet_name][address].fill) == fills[key]
    workbook.save(path)
    workbook.close()
    reopened = load_workbook(path)
    assert reopened.active.title == result['guideSheet']
    assert reopened.active.sheet_view.topLeftCell == 'A1'
    assert any(cell.hyperlink and '4447601857' in (cell.hyperlink.target or '') for row in reopened.active for cell in row)
    for (sheet_name, address), value in values.items():
        assert reopened[sheet_name][address].value == value
    reopened.close()


def test_workbook_walkthrough_preserves_source_data_and_embeds_six_safe_tour_steps(tmp_path: Path) -> None:
    from hashlib import sha256
    import re

    from backend.analytics.work_package_matrix import add_work_package_explanations, add_workbook_navigation, build_work_package_matrix
    from backend.analytics.workbook_walkthrough import build_workbook_walkthrough

    source, evidence, strategies = _matrix_inputs(tmp_path)
    workbook_path = tmp_path / 'matrix.xlsx'
    build_work_package_matrix(source, evidence, strategies, workbook_path)
    workbook = load_workbook(workbook_path)
    matrix = workbook.active
    notes = workbook.create_sheet('Sheet1')
    add_work_package_explanations(matrix, notes)
    add_workbook_navigation(workbook)
    matrix['C8'] = '</script><img src=x onerror=alert(1)>'
    workbook.save(workbook_path)
    workbook.close()
    source_bytes = workbook_path.read_bytes()
    output = tmp_path / 'walkthrough.html'
    result = build_workbook_walkthrough(workbook_path, output)
    assert workbook_path.read_bytes() == source_bytes
    assert result['sourceSha256'] == sha256(source_bytes).hexdigest()
    assert result['sheetCount'] == 3 and result['packageCount'] == 55 and result['tourSteps'] == 6
    html = output.read_text(encoding='utf-8')
    match = re.search(r'<script id="workbook-data" type="application/json">(.*?)</script>', html, re.S)
    assert match
    data = json.loads(match.group(1))
    assert data['sourceSha256'] == result['sourceSha256']
    assert [sheet['name'] for sheet in data['sheets']] == ['\u9605\u8bfb\u5411\u5bfc', matrix.title, 'Sheet1']
    cells = {cell['address']: cell for sheet in data['sheets'] if sheet['name'] == matrix.title for cell in sheet['cells']}
    assert cells['C8']['text'] == '</script><img src=x onerror=alert(1)>'
    assert '</script><img src=x' not in html
    assert len(data['tour']) == 6
    assert 'tour-mask' in html and 'tour-highlight' in html and 'tour-arrow' in html
    assert 'tour-next' in html and 'tour-back' in html and 'tour-restart' in html
    assert 'role="dialog"' in html and 'lucide' in html
    assert 'cdn.jsdelivr' not in html and 'unpkg.com' not in html


def test_workbook_walkthrough_refuses_to_overwrite_source_or_existing_output(tmp_path: Path) -> None:
    from backend.analytics.workbook_walkthrough import build_workbook_walkthrough

    source, evidence, strategies = _matrix_inputs(tmp_path)
    with pytest.raises(ValueError, match='overwrite'):
        build_workbook_walkthrough(source, source)
    output = tmp_path / 'walkthrough.html'
    output.write_text('User output', encoding='utf-8')
    with pytest.raises(FileExistsError):
        build_workbook_walkthrough(source, output)
    assert output.read_text(encoding='utf-8') == 'User output'


def test_quality_scope_deduplicates_candidates_and_does_not_confuse_triggers_with_quality() -> None:
    from backend.analytics.free_test_quality_scope import classify_free_test_evidence

    slow = {
        'defectId': '123', 'name': 'Speech response takes 8 seconds',
        'discoveryPath': 'Activate speech and observe delayed response.',
        'functions': ['Speech'], 'reproductionAttempts': 5, 'lifecycleRuns': 2,
    }
    wrong = {
        'defectId': '124', 'name': 'Navigation route is wrong',
        'discoveryPath': 'Rapidly tap the destination and check the incorrect route.',
        'functions': ['Navigation'],
    }
    recovery = {
        'defectId': '125', 'name': 'Camera preview remains black after permission is restored',
        'discoveryPath': 'Disable camera permission, restore permission and reopen the app.',
        'functions': ['Camera'],
    }
    data = {
        'coverage': {'freeTestTickets': 20, 'freeTestWithActions': 18},
        'discoveryPatternLibrary': {
            'candidateTicketCount': 3,
            'patterns': [
                {'patternId': 'input', 'pattern': 'Voice-input channel and mode boundaries',
                 'caseStudies': [slow, wrong]},
                {'patternId': 'recovery', 'pattern': 'Disconnect/reconnect sequence exploration',
                 'caseStudies': [slow, recovery]},
            ],
        },
    }
    result = classify_free_test_evidence(data)
    records = {record['ticketId']: record for record in result['records']}
    assert set(records) == {'123', '124', '125'}
    assert records['123']['qualityPrimary'] == 'PE.TIME'
    assert records['124']['qualityPrimary'] == 'FS.CORRECT'
    assert 'PE.TIME' not in records['124']['qualitySecondary']
    assert records['125']['qualityPrimary'] == 'RE.RECOVER'
    assert not any(code.startswith('SE.') for code in records['125']['qualitySecondary'])
    assert records['123']['sourcePatterns'] == ['input', 'recovery']
    assert all(record['reviewStatus'] == 'candidate_needs_review' for record in records.values())
    assert result['summary']['uniqueTickets'] == 3
    assert sum(result['summary']['primaryQualityCounts'].values()) == 3
    assert sum(result['summary']['primaryScenarioCounts'].values()) == 3
    assert sum(result['summary']['qualityScenarioCounts'].values()) == 3
    assert result['summary']['sourceFreeTestTickets'] == 20
    assert result['summary']['unit'] == 'unique_candidate_ticket'


def test_quality_scope_keeps_the_full_cohort_separate_from_the_complex_candidate_subset() -> None:
    from copy import deepcopy

    from backend.analytics.free_test_quality_scope import classify_free_test_evidence

    data = {
        'coverage': {'freeTestTickets': 3, 'freeTestWithActions': 2},
        'freeTestEvidence': [
            {'defectId': '123', 'name': 'Speech loads slowly', 'actions': 'Activate speech.',
             'discoveryBasis': 'Explicit Free-test report reference',
             'methodEvidence': {'Input / language / command variation': {'source': 'actions', 'excerpt': 'Activate speech.'}}},
            {'defectId': '124', 'name': 'No audio after source selection', 'actions': 'Quickly select a source.',
             'discoveryBasis': 'No testcase link (Free test by policy)'},
            {'defectId': '125', 'name': 'Observation needs clarification', 'actions': '',
             'discoveryBasis': 'No testcase link (Free test by policy)'},
        ],
        'discoveryPatternLibrary': {
            'candidateTicketCount': 1, 'candidateTicketIds': ['123'],
            'patterns': [{'patternId': 'input_channel_contrast', 'pattern': 'Voice-input channel and mode boundaries',
                          'caseStudies': [{'defectId': '123', 'name': 'Speech loads slowly',
                                           'discoveryPath': 'Activate speech.'}]}],
        },
    }
    original = deepcopy(data)
    result = classify_free_test_evidence(data)
    records = {record['ticketId']: record for record in result['records']}
    assert data == original
    assert set(records) == {'123', '124', '125'}
    assert records['123']['qualityPrimary'] == 'PE.TIME'
    assert records['123']['inComplexSubset'] is True
    assert records['124']['inComplexSubset'] is False
    assert records['125']['qualityPrimary'] == 'UNCLASSIFIED'
    assert records['123']['discoveryBasis'] == 'Explicit Free-test report reference'
    assert result['summary']['complexCandidateTickets'] == 1
    assert result['summary']['uniqueTickets'] == 3
    assert result['summary']['cohortBasisCounts'] == {
        'Explicit Free-test report reference': 1,
        'No testcase link (Free test by policy)': 2,
    }
    assert sum(result['summary']['primaryQualityCounts'].values()) == 3


@pytest.mark.parametrize(('invalid_source', 'message'), [
    ('complex_ids', 'Complex candidate ID mismatch'),
    ('complex_count', 'Complex candidate count mismatch'),
    ('cohort_count', 'Full cohort count mismatch'),
    ('cohort_basis', 'Conflicting discovery basis'),
])
def test_quality_scope_rejects_inconsistent_frozen_evidence(tmp_path: Path, invalid_source: str, message: str) -> None:
    from backend.analytics.free_test_quality_scope import classify_free_test_evidence

    source, evidence = _inputs(tmp_path)
    data = json.loads(evidence.read_text(encoding='utf-8'))
    if invalid_source == 'complex_ids':
        data['discoveryPatternLibrary']['candidateTicketIds'] = ['999']
    elif invalid_source == 'complex_count':
        data['discoveryPatternLibrary']['candidateTicketCount'] = 2
    elif invalid_source == 'cohort_count':
        data['freeTestEvidence'] = [{'defectId': '123', 'name': 'Failure'}]
    else:
        data['coverage']['freeTestTickets'] = 1
        data['freeTestEvidence'] = [
            {'defectId': '123', 'name': 'Failure', 'discoveryBasis': 'Explicit Free-test report reference'},
            {'defectId': '123', 'name': 'Failure', 'discoveryBasis': 'No testcase link (Free test by policy)'},
        ]
    with pytest.raises(ValueError, match=message):
        classify_free_test_evidence(data)


def test_quality_scope_workbook_preserves_existing_navigation_and_adds_independent_axes(tmp_path: Path) -> None:
    from copy import copy
    import re

    from backend.analytics.free_test_quality_scope import SHEETS, build_quality_scope_workbook
    from backend.analytics.work_package_matrix import add_work_package_explanations, add_workbook_navigation, build_work_package_matrix

    source, evidence, strategies = _matrix_inputs(tmp_path)
    navigator = tmp_path / 'navigator.xlsx'
    build_work_package_matrix(source, evidence, strategies, navigator)
    original = load_workbook(navigator)
    add_work_package_explanations(original.active, original.create_sheet('Sheet1'))
    add_workbook_navigation(original)
    original.save(navigator)
    original.close()
    original = load_workbook(navigator)
    expected = {(sheet.title, cell.coordinate): (cell.value, copy(cell._style))
                for sheet in original for row in sheet for cell in row}
    original.close()
    source_bytes, evidence_bytes = navigator.read_bytes(), evidence.read_bytes()
    output = tmp_path / 'quality_navigator.xlsx'
    result = build_quality_scope_workbook(navigator, evidence, output)
    assert navigator.read_bytes() == source_bytes
    assert evidence.read_bytes() == evidence_bytes
    assert result['qualityPackageCount'] == 7
    assert result['qualityCharacteristicCount'] == 8
    assert result['summary']['uniqueTickets'] == 1
    workbook = load_workbook(output)
    assert workbook.active.title == SHEETS['guide']
    assert set(SHEETS.values()) <= set(workbook.sheetnames)
    assert len(workbook.sheetnames) == 12
    for (sheet_name, address), value in expected.items():
        cell = workbook[sheet_name][address]
        assert (cell.value, copy(cell._style)) == value
    packages = workbook[SHEETS['packages']]
    assert [packages.cell(row, 1).value for row in range(6, 13)] == [f'FT-Q{index:02d}' for index in range(1, 8)]
    taxonomy = workbook[SHEETS['taxonomy']]
    assert taxonomy.max_row == 37
    assert {taxonomy.cell(row, 1).value for row in range(6, 38)} >= {'FS.CORRECT', 'PE.TIME', 'CO.INTEROP', 'RE.RECOVER', 'SE.CONF', 'UNCLASSIFIED'}
    assert workbook[SHEETS['checkpoints']].max_row >= 40
    details = workbook[SHEETS['evidence']]
    assert details['A6'].value == '123'
    assert details['G6'].value is None and details['K6'].value is None
    assert details['H6'].value == '=IF(G6="",F6,IF(COUNTIF(FTQ_QualityCodes,G6)=1,G6,"UNCLASSIFIED"))'
    assert details['L6'].value == '=IF(K6="",J6,IF(COUNTIF(FTQ_Scenarios,K6)=1,K6,"UNMAPPED"))'
    assert 'INVALID_REVIEW' in details['X6'].value
    log = workbook[SHEETS['activity']]
    assert log['A6'].value is None and log['N6'].value is None
    assert 'COUNTIFS' in log['S6'].value
    assert 'INVALID_VALUE' in log['R6'].value
    assert 'NEGATIVE_OR_NONNUMERIC' in log['R6'].value
    assert 'FTQ_WorkPackages' in log['R6'].value
    assert '"PASS"' in log['S6'].value and '"NO_FINDING"' in log['S6'].value
    assert log.data_validations.count >= 8
    statistics = workbook[SHEETS['summary']]
    assert any('waiting FreeTest minutes' in str(cell.value) for row in statistics for cell in row)
    assert any('$N$6:$N$1005,"<>"' in str(cell.value) for row in statistics for cell in row)
    assert workbook.calculation.calcMode == 'auto'
    assert workbook.calculation.fullCalcOnLoad is True
    for sheet in workbook:
        for row in sheet:
            for cell in row:
                if cell.hyperlink and cell.hyperlink.location:
                    match = re.fullmatch(r"'((?:[^']|'')+)'!([A-Z]+\d+)", cell.hyperlink.location)
                    assert match, cell.hyperlink.location
                    title, address = match.groups()
                    assert workbook[title.replace("''", "'")][address].value is not None
    workbook.close()
    with pytest.raises(FileExistsError):
        build_quality_scope_workbook(navigator, evidence, output)
    with pytest.raises(ValueError, match='overwrite'):
        build_quality_scope_workbook(navigator, evidence, navigator)


@pytest.mark.parametrize('compact_header', [False, True])
def test_free_test_only_revision_changes_only_the_existing_six_matrix_rows(tmp_path: Path, compact_header: bool) -> None:
    from copy import copy

    from backend.analytics.work_package_matrix import (
        add_work_package_explanations, add_workbook_navigation, build_work_package_matrix,
        revise_free_test_quality_rows,
    )

    source, evidence, strategies = _matrix_inputs(tmp_path)
    path = tmp_path / 'original.xlsx'
    build_work_package_matrix(source, evidence, strategies, path)
    workbook = load_workbook(path)
    matrix = workbook.active
    if compact_header:
        for merged in list(matrix.merged_cells.ranges):
            if merged.min_row > 1:
                matrix.unmerge_cells(str(merged))
        matrix.delete_rows(2, 5)
    add_work_package_explanations(matrix, workbook.create_sheet('Sheet1'))
    add_workbook_navigation(workbook)
    workbook.save(path)
    workbook.close()
    original_bytes = path.read_bytes()
    workbook = load_workbook(path)
    titles = list(workbook.sheetnames)
    target_rows = {row[0].row for row in workbook[matrix.title]
                   if str(row[0].value).startswith('FREE-')}
    preserved = {(sheet.title, cell.coordinate): (cell.value, copy(cell._style),
                 cell.comment.text if cell.comment else None)
                 for sheet in workbook for row in sheet for cell in row
                 if sheet.title != matrix.title or cell.row not in target_rows}
    size = (workbook[matrix.title].max_row, workbook[matrix.title].max_column)
    result = revise_free_test_quality_rows(workbook)
    assert workbook.sheetnames == titles
    assert set(result['rows']) == target_rows
    assert result['packageCount'] == 6
    assert (workbook[matrix.title].max_row, workbook[matrix.title].max_column) == size
    assert [workbook[matrix.title].cell(row, 1).value for row in sorted(target_rows)] == [f'FREE-0{index}' for index in range(1, 7)]
    for (title, address), value in preserved.items():
        cell = workbook[title][address]
        assert (cell.value, copy(cell._style), cell.comment.text if cell.comment else None) == value
    names = [workbook[matrix.title].cell(row, 3).value for row in sorted(target_rows)]
    assert names == [
        '\u529f\u80fd\u6b63\u786e\u6027\u4e0e\u4ea4\u4e92\u8fb9\u754c\u63a2\u7d22',
        '\u65f6\u95f4\u884c\u4e3a\u4e0e\u54cd\u5e94\u6027\u80fd\u63a2\u7d22',
        '\u8de8\u7cfb\u7edf\u4e0e\u8de8\u8bbe\u5907\u4e92\u64cd\u4f5c\u63a2\u7d22',
        '\u6b63\u5e38\u4f7f\u7528\u7a33\u5b9a\u6027\u4e0e\u53ef\u7528\u6027\u63a2\u7d22',
        '\u5f02\u5e38\u6761\u4ef6\u5bb9\u9519\u4e0e\u964d\u7ea7\u884c\u4e3a\u63a2\u7d22',
        '\u4e2d\u65ad\u540e\u6062\u590d\u4e0e\u72b6\u6001\u4fdd\u6301\u63a2\u7d22',
    ]
    workbook.save(tmp_path / 'free_test_only.xlsx')
    workbook.close()
    assert path.read_bytes() == original_bytes