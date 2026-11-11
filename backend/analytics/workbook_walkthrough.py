"""Export an offline workbook viewer with a six-step spotlight walkthrough."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter


def _rgb(color, default):
    return '#' + color.rgb[-6:] if color is not None and color.type == 'rgb' else default


def _icons():
    script = """
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import * as lucide from 'lucide-react';
const names = ['FileSpreadsheet', 'ChevronLeft', 'ChevronRight', 'X', 'Search', 'Info', 'Download', 'RotateCcw', 'PanelRightClose'];
console.log(JSON.stringify(Object.fromEntries(names.map(name => [name, renderToStaticMarkup(React.createElement(lucide[name], {size:18, strokeWidth:1.7, 'aria-hidden':true}))]))));
"""
    result = subprocess.run(['node', '--input-type=module', '-e', script],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def _sheet_data(sheet):
    cells = []
    for row in sheet:
        for cell in row:
            if cell.value is None:
                continue
            link = None
            if cell.hyperlink:
                link = {'location': cell.hyperlink.location, 'target': cell.hyperlink.target}
            cells.append({'address': cell.coordinate, 'row': cell.row, 'col': cell.column,
                          'text': str(cell.value), 'comment': cell.comment.text if cell.comment else '',
                          'link': link, 'fill': _rgb(cell.fill.fgColor, '#ffffff') if cell.fill.patternType == 'solid' else '#ffffff',
                          'color': _rgb(cell.font.color, '#172b3a'), 'bold': bool(cell.font.b),
                          'size': cell.font.sz or 10})
    widths = []
    for column in range(1, sheet.max_column + 1):
        dimension = sheet.column_dimensions.get(get_column_letter(column))
        widths.append(min(350, max(88, round((dimension.width if dimension else 13) * 6 + 8))))
    if sheet.max_column >= 30:
        widths[:4] = [94, 155, 245, 270]
    heights = [min(132, max(30, (sheet.row_dimensions[row].height or 22) * 4 / 3))
               for row in range(1, sheet.max_row + 1)]
    return {'name': sheet.title, 'rows': sheet.max_row, 'cols': sheet.max_column, 'cells': cells,
            'widths': widths, 'heights': heights,
            'merges': [{'top': merged.min_row, 'bottom': merged.max_row, 'left': merged.min_col, 'right': merged.max_col}
                       for merged in sheet.merged_cells.ranges]}


def build_workbook_walkthrough(source_path, output_path) -> dict:
    source_path, output_path = Path(source_path), Path(output_path)
    if source_path.resolve() == output_path.resolve():
        raise ValueError('Refusing to overwrite the source workbook')
    if output_path.exists():
        raise FileExistsError(f'Output already exists: {output_path}')
    source_hash = sha256(source_path.read_bytes()).hexdigest()
    book = load_workbook(source_path, data_only=False)
    sheets = [_sheet_data(sheet) for sheet in book]
    book.close()
    matrix_index = next((index for index, sheet in enumerate(sheets)
                         if any(cell['text'] in ('WP ID', '\u0057\u0050 \u7f16\u53f7') for cell in sheet['cells'])), None)
    if matrix_index is None:
        raise ValueError('The work-package matrix was not found')
    matrix = sheets[matrix_index]
    header = next(cell['row'] for cell in matrix['cells'] if cell['text'] in ('WP ID', '\u0057\u0050 \u7f16\u53f7'))
    packages = [cell for cell in matrix['cells'] if cell['col'] == 1 and re.fullmatch(r'[A-Z]+-\d{2,}', cell['text'])]
    source_column = next(cell['col'] for cell in matrix['cells']
                         if cell['row'] == header and 'Confluence' in cell['text'])
    notes_index = next(index for index, sheet in enumerate(sheets) if sheet['name'] == 'Sheet1')
    notes = sheets[notes_index]
    note_row = next(cell['row'] for cell in notes['cells'] if cell['text'].startswith('WP ID / Work Domain\uff1a'))
    tour = [
        {'sheet': matrix_index, 'region': 'tabs', 'title': '\u4e09\u5f20\u5de5\u4f5c\u8868\u7684\u5206\u5de5',
         'body': '\u77e9\u9635\u4fdd\u7559\u5177\u4f53\u5de5\u4f5c\u5305\u4e0e\u539f\u5de5\u4f5c\u7c7b\u578b\u7684\u5173\u7cfb\uff1bSheet1 \u89e3\u91ca\u7f16\u53f7\u548c\u5de5\u4f5c\u57df\uff1b\u9605\u8bfb\u5411\u5bfc\u4fdd\u7559\u5206\u7c7b\u5165\u53e3\u548c\u539f\u59cb\u5f15\u7528\u3002'},
        {'sheet': matrix_index, 'region': 'coordinates', 'row': header, 'lastRow': header + 3, 'col': 1, 'lastCol': 4,
         'title': '\u5de5\u4f5c\u5305\u5750\u6807\uff1a\u505a\u4ec0\u4e48\u4e0e\u4ea7\u51fa\u4ec0\u4e48',
         'body': 'WP ID \u552f\u4e00\u6807\u8bc6\u5177\u4f53\u5de5\u4f5c\u5305\uff1bWork Domain \u662f\u4e0a\u5c42\u5de5\u4f5c\u5206\u7c7b\uff0c\u4e0d\u662f\u90e8\u95e8\u3002\u5177\u4f53\u5de5\u4f5c\u5305\u548c\u4ea4\u4ed8\u7269\u5b9a\u4e49\u4e86\u8fd9\u4e00\u884c\u5b9e\u9645\u8981\u505a\u7684\u4e8b\u3002'},
        {'sheet': matrix_index, 'region': 'work-types', 'row': header, 'lastRow': header, 'col': 5, 'lastCol': 8,
         'title': '\u6a2a\u8f74\uff1a\u539f\u8868\u7684\u5de5\u4f5c\u7c7b\u578b',
         'body': '\u8fd9\u4e9b\u5217\u6765\u81ea Effort Breakdown Details\uff0c\u5305\u62ec\u6267\u884c\u4e0e\u7ba1\u7406\u5de5\u4f5c\u3002\u540d\u79f0\u5b8c\u5168\u76f8\u540c\u8005\u5408\u5e76\uff0c\u9700\u6c42\u6536\u96c6\u4e0e\u8bc4\u5ba1\u7b49\u804c\u8d23\u4e0d\u540c\u7684\u5de5\u4f5c\u4ecd\u4fdd\u7559\u4e3a\u4e0d\u540c\u5217\u3002'},
        {'sheet': matrix_index, 'region': 'intersection', 'row': packages[0]['row'], 'lastRow': packages[0]['row'] + 2, 'col': 5, 'lastCol': 8,
         'title': '\u4ea4\u53c9\u5de5\u4f5c\uff1a\u591a\u4e2a\u52a8\u4f5c\u5f62\u6210\u540c\u4e00\u4ea4\u4ed8\u7269',
         'body': '\u4e00\u4e2a\u5de5\u4f5c\u5305\u53ef\u4ee5\u5173\u8054\u591a\u4e2a\u539f\u5de5\u4f5c\u7c7b\u578b\uff0c\u683c\u5185\u5199\u660e\u4e0d\u540c\u52a8\u4f5c\u3002\u8fd9\u662f\u5efa\u8bae\u7684\u9002\u7528\u5173\u7cfb\uff0c\u4e0d\u662f\u5df2\u6279\u51c6\u7684 RACI\uff1b\u540c\u4e00\u5206\u949f\u4e0d\u80fd\u5728\u591a\u4e2a\u5305\u91cd\u590d\u8bb0\u65f6\u3002'},
        {'sheet': matrix_index, 'region': 'evidence', 'row': header, 'lastRow': header + 2, 'col': source_column, 'lastCol': matrix['cols'],
         'title': '\u5f15\u7528\u4e0e\u6570\u636e\u8fb9\u754c',
         'body': '\u53f3\u4fa7\u4fdd\u7559 Confluence \u6765\u6e90\u3001\u6570\u636e\u652f\u6301\u548c\u672a\u91cf\u5316\u90e8\u5206\u3002\u65b9\u6cd5\u4fe1\u53f7\u3001\u590d\u5408\u5019\u9009\u3001\u6267\u884c\u4e8b\u4ef6\u548c\u5de5\u65f6\u662f\u4e0d\u540c\u53e3\u5f84\uff0c\u4e0d\u80fd\u76f4\u63a5\u76f8\u52a0\u6216\u6362\u7b97\u3002'},
        {'sheet': notes_index, 'region': 'definitions', 'row': note_row, 'lastRow': note_row + 1, 'col': 1, 'lastCol': notes['cols'],
         'title': '\u8be6\u7ec6\u8bf4\u660e\u4e0e\u8bcd\u5178',
         'body': 'Sheet1 \u4fdd\u7559\u7f16\u53f7\u89c4\u5219\u3001\u524d\u7f00\u8bcd\u5178\u3001\u5de5\u4f5c\u57df\u8303\u56f4\u4e0e\u8fb9\u754c\u3002\u7f16\u53f7\u4e0d\u8868\u793a\u4f18\u5148\u7ea7\u6216\u5de5\u65f6\u6743\u91cd\uff1b\u5177\u4f53\u7f16\u53f7\u793a\u4f8b\u4fdd\u7559\u56de\u5230\u77e9\u9635\u7684\u5f15\u7528\u3002'},
    ]
    data = {'sourceName': source_path.name, 'sourceSha256': source_hash,
            'sourceHref': os.path.relpath(source_path, output_path.parent).replace('\\', '/'),
            'sheets': sheets, 'matrixIndex': matrix_index, 'headerRow': header,
            'sourceColumn': source_column, 'packageCount': len(packages), 'tour': tour}
    payload = json.dumps(data, ensure_ascii=True).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    html = _HTML.replace('%%DATA%%', payload)
    for name, svg in _icons().items():
        html = html.replace('%%ICON_' + name + '%%', svg)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding='utf-8')
    if sha256(source_path.read_bytes()).hexdigest() != source_hash:
        raise RuntimeError('Source workbook changed during export')
    return {'outputPath': str(output_path), 'sourceSha256': source_hash,
            'sheetCount': len(sheets), 'packageCount': len(packages), 'tourSteps': len(tour)}


_HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DTSV Workbook | Guided Review</title>
<style>
:root{--ink:#172b3a;--muted:#63717a;--blue:#1f4e78;--line:#dce3e8;--paper:#fff;--accent:#24a28a;--light:#f3f6f8}
*{box-sizing:border-box;letter-spacing:0}html,body{height:100%;margin:0}body{font:14px Calibri,"Microsoft YaHei",sans-serif;color:var(--ink);background:var(--light)}
button,input,select{font:inherit}button,a,input,select{outline-offset:3px}button:focus-visible,a:focus-visible{outline:2px solid #38bca9}button{cursor:pointer}button:disabled{cursor:default;opacity:.4}
[hidden]{display:none!important}.app{height:100dvh;display:flex;flex-direction:column;min-width:0}
.appbar{display:flex;align-items:center;gap:12px;padding:12px 18px;background:#fff;border-bottom:1px solid var(--line);flex:none;min-height:58px}
.brand-icon{color:var(--accent);display:flex}.brand{min-width:0;flex:1}.brand h1{font-size:17px;font-weight:700;margin:0;line-height:1.2}.brand small{display:block;font-size:11px;color:var(--muted);margin-top:3px}
.tool{height:34px;min-width:34px;border:1px solid var(--line);background:#fff;color:var(--ink);display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:0 9px;border-radius:4px;text-decoration:none;flex:none}
.tool:hover{background:#f0f5f7}.tool svg{flex:none}.toolbar{padding:8px 18px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--line);background:#fafcfd;flex:none}
.search{height:32px;display:flex;align-items:center;gap:7px;border:1px solid var(--line);background:white;padding:0 9px;border-radius:4px;width:245px;min-width:100px}.search svg{color:var(--muted);flex:none}.search input{min-width:0;border:0;outline:0;background:transparent;width:100%}
.toolbar select{height:32px;max-width:250px;min-width:130px;border:1px solid var(--line);background:white;padding:0 8px;border-radius:4px}.count{font-size:12px;color:var(--muted);margin-left:auto;white-space:nowrap}
.formula{height:33px;display:flex;align-items:center;gap:10px;padding:0 18px;background:#fff;border-bottom:1px solid var(--line);font-size:12px;flex:none}.cell-address{font-weight:700;width:55px;flex:none}.formula-value{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--muted)}
.work-area{display:flex;flex:1;min-height:0;min-width:0}.grid-scroller{flex:1;overflow:auto;background:#fff;position:relative;scrollbar-color:#b8c5cc #f1f5f6;scrollbar-width:thin}
table{border-collapse:separate;border-spacing:0;table-layout:fixed;width:max-content;min-width:100%;font-size:13px}
th,td{border-right:1px solid #dce3e8;border-bottom:1px solid #dce3e8;padding:7px 9px;vertical-align:middle;white-space:pre-wrap;overflow-wrap:anywhere;position:relative;line-height:1.3}
.letters th{height:25px;padding:3px;text-align:center;font-size:11px;background:#f4f7f9;color:#6c7b84;position:sticky;top:0;z-index:9;font-weight:400}
.row-number{width:42px;position:sticky;left:0;background:#f4f7f9;color:#6c7b84;text-align:center;font-size:11px;z-index:7;padding:3px;font-weight:400}
.header-cell{position:sticky;top:25px;z-index:8}.sticky-col{position:sticky;z-index:5}.header-cell.sticky-col{z-index:10}.letters .row-number{z-index:12}
.data-cell{cursor:default}.data-cell:hover{box-shadow:inset 0 0 0 1px #90b7c5}.data-cell.selected{box-shadow:inset 0 0 0 2px #199f8b}.has-comment:after{content:"";position:absolute;right:0;top:0;width:0;height:0;border-top:7px solid #c5683c;border-left:7px solid transparent}
.cell-link{color:#0563c1;text-decoration:underline;cursor:pointer}.sheet-footer{flex:none;display:flex;align-items:center;min-height:42px;background:#f4f7f9;border-top:1px solid var(--line);overflow:auto;padding:0 12px;gap:5px}
.sheet-tab{height:40px;flex:none;border:0;background:transparent;padding:0 14px;color:#596971;border-bottom:3px solid transparent;font-size:13px}.sheet-tab.active{background:#fff;color:#176956;border-bottom-color:var(--accent);font-weight:700}.sheet-tab:hover{background:#edf3f4}
.status{padding:4px 18px;background:#e9f0f4;color:#607780;font-size:11px;flex:none;display:flex;justify-content:space-between;gap:10px}
.details{width:330px;flex:none;background:white;border-left:1px solid var(--line);display:flex;flex-direction:column;min-height:0}.details-head{padding:13px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--line);font-weight:700}.details-head .tool{margin-left:auto}.details-content{padding:14px;overflow:auto;font-size:13px;white-space:pre-wrap;line-height:1.6}.details-label{font-weight:700;margin-top:16px;color:#466273}
.tour-root{position:fixed;inset:0;z-index:50}.mask-piece{position:fixed;background:rgba(14,24,31,.67);z-index:50}.tour-highlight{position:fixed;z-index:51;pointer-events:none;border:3px solid #50dbc0;box-shadow:0 0 0 1px #fff;border-radius:4px}
.tour-arrow{position:fixed;inset:0;z-index:52;pointer-events:none;width:100%;height:100%;overflow:visible}.tour-arrow path{fill:none;stroke:#fff;stroke-width:2.3;stroke-linecap:round;stroke-linejoin:round}.tour-arrow marker path{fill:#fff;stroke:none}
.tour-card{position:fixed;z-index:53;width:330px;max-width:calc(100vw - 24px);padding:17px 18px 15px;background:#fff;border:1px solid #d7e0e5;box-shadow:0 14px 50px rgba(0,0,0,.22);border-radius:6px;animation:arrive .16s ease-out}
.tour-eyebrow{display:flex;justify-content:space-between;align-items:center;color:#61717d;font-size:11px;margin-bottom:8px}.tour-eyebrow .close{border:0;background:transparent;width:24px;height:24px;padding:2px;color:#66757c;display:flex;align-items:center;justify-content:center}
.tour-card h2{font-size:18px;line-height:1.35;margin:0 0 10px;font-weight:700}.tour-card p{font-size:14px;line-height:1.6;margin:0;color:#4e606c;overflow-wrap:anywhere}.tour-steps{display:flex;gap:6px;margin:16px 0 14px}.step-dot{height:25px;width:25px;flex:none;border-radius:50%;border:1px solid #d8e1e7;background:#fff;color:#75818a;font-size:12px;padding:0}.step-dot.current{background:#1f4e78;color:white;border-color:#1f4e78}.step-dot.done{background:#e0f3ec;color:#277966;border-color:#c5e6dc}
.tour-actions{display:flex;align-items:center;justify-content:space-between;gap:8px;border-top:1px solid #e8edf0;padding-top:12px}.tour-skip{border:0;background:transparent;color:#6b7c87;font-size:12px;padding:7px 0}.pager{display:flex;gap:7px}.pager button{border-radius:4px;border:1px solid #d8e1e7;background:white;height:34px;display:flex;align-items:center;gap:5px;padding:0 9px;font-size:13px}.pager .next{background:#1f4e78;color:white;border-color:#1f4e78}
@keyframes arrive{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:translateY(0)}}
@media(max-width:1100px){.appbar{padding:10px;gap:8px}.brand h1{font-size:15px}.brand small{max-width:170px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.tool-label{display:none}.toolbar{padding:7px 10px;flex-wrap:wrap}.search{flex:1;width:155px}.toolbar select{width:145px;max-width:48%;min-width:110px}.count{font-size:11px}.formula{padding:0 10px}.details{position:absolute;right:0;top:110px;bottom:65px;z-index:20;width:min(330px,calc(100vw - 24px));box-shadow:-10px 0 20px #0002}.status{padding:4px 10px}.sheet-tab{padding:0 10px}.tour-card{width:310px;padding:14px 16px 12px}.tour-card h2{font-size:17px}.tour-card p{font-size:13px;line-height:1.55}.tour-steps{margin:12px 0}.sticky-col:not(.first-col){position:relative;left:auto!important;z-index:1}.header-cell.sticky-col:not(.first-col){position:sticky;top:25px;z-index:8}.tour-actions{padding-top:10px}}
@media(max-height:720px){.tour-card{padding:12px 16px}.tour-card h2{font-size:17px;line-height:1.25;margin-bottom:8px}.tour-card p{font-size:13px;line-height:1.5}.tour-eyebrow{margin-bottom:6px}.tour-steps{margin:10px 0}.tour-actions{padding-top:10px}.pager button{height:32px}}
@media(max-height:720px) and (min-width:761px){.tour-card{width:400px}}
@media(prefers-reduced-motion:reduce){*{animation:none!important;scroll-behavior:auto!important}}
</style></head><body>
<main class="app" id="app">
 <header class="appbar"><span class="brand-icon">%%ICON_FileSpreadsheet%%</span><div class="brand"><h1>DTSV Work Breakdown</h1><small id="source-name"></small></div>
 <button class="tool" id="tour-restart" title="&#x91CD;&#x65B0;&#x64AD;&#x653E;&#x516D;&#x6B65;&#x5411;&#x5BFC;">%%ICON_RotateCcw%%<span class="tool-label">&#x5206;&#x6B65;&#x5411;&#x5BFC;</span></button>
 <button class="tool" id="details-toggle" title="&#x67E5;&#x770B;&#x5355;&#x5143;&#x683C;&#x6279;&#x6CE8;">%%ICON_Info%%</button>
 <a class="tool" id="download-workbook" title="&#x6253;&#x5F00;&#x539F; Excel">%%ICON_Download%%<span class="tool-label">Excel</span></a></header>
 <section class="toolbar"><label class="search">%%ICON_Search%%<input id="search" aria-label="&#x641C;&#x7D22;&#x5DE5;&#x4F5C;&#x5305;" placeholder="WP ID / &#x5DE5;&#x4F5C;&#x5305;"></label><select id="domain-filter" aria-label="Work Domain"></select><span class="count" id="row-count"></span></section>
 <div class="formula"><span class="cell-address" id="cell-address">A1</span><span class="formula-value" id="formula-value"></span></div>
 <div class="work-area"><div class="grid-scroller" id="grid-scroller"></div><aside class="details" id="details" hidden><div class="details-head"><span id="details-title"></span><button class="tool" id="details-close" title="&#x5173;&#x95ED;&#x6279;&#x6CE8;">%%ICON_PanelRightClose%%</button></div><div class="details-content" id="details-content"></div></aside></div>
 <nav class="sheet-footer" id="sheet-tabs" aria-label="&#x5DE5;&#x4F5C;&#x8868;"></nav><footer class="status"><span id="status-sheet"></span><span id="status-source"></span></footer>
</main>
<div class="tour-root" id="tour-root" hidden>
 <div id="tour-mask"><div class="mask-piece" id="mask-top"></div><div class="mask-piece" id="mask-left"></div><div class="mask-piece" id="mask-right"></div><div class="mask-piece" id="mask-bottom"></div></div>
 <div class="tour-highlight" id="tour-highlight"></div>
 <svg class="tour-arrow" id="tour-arrow" aria-hidden="true"><defs><marker id="arrow-head" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 Z"></path></marker></defs><path id="arrow-path" marker-end="url(#arrow-head)"></path></svg>
 <section class="tour-card" id="tour-card" role="dialog" aria-modal="true" aria-labelledby="tour-title" aria-describedby="tour-body" tabindex="-1">
  <div class="tour-eyebrow"><span id="tour-index"></span><button class="close" id="tour-close" aria-label="&#x5173;&#x95ED;&#x5411;&#x5BFC;">%%ICON_X%%</button></div><h2 id="tour-title"></h2><p id="tour-body"></p>
  <nav class="tour-steps" id="tour-steps" aria-label="&#x5411;&#x5BFC;&#x6B65;&#x9AA4;"></nav><div class="tour-actions"><button class="tour-skip" id="tour-skip">&#x8DF3;&#x8FC7;&#x5411;&#x5BFC;</button><div class="pager"><button id="tour-back">%%ICON_ChevronLeft%%<span>&#x4E0A;&#x4E00;&#x6B65;</span></button><button class="next" id="tour-next"><span id="next-label">&#x4E0B;&#x4E00;&#x6B65;</span>%%ICON_ChevronRight%%</button></div></div>
 </section>
</div>
<script id="workbook-data" type="application/json">%%DATA%%</script>
<script>
'use strict';
const DATA=JSON.parse(document.getElementById('workbook-data').textContent);
const $=id=>document.getElementById(id);
const grid=$('grid-scroller'), root=$('tour-root'), card=$('tour-card'), app=$('app');
const state={sheet:DATA.matrixIndex,step:0,touring:false,selected:null,query:'',domain:''};
const maps=DATA.sheets.map(sheet=>new Map(sheet.cells.map(cell=>[cell.address,cell])));
const widths=sheet=>sheet.widths;
function letter(index){let value='';while(index>0){index--;value=String.fromCharCode(65+index%26)+value;index=Math.floor(index/26);}return value;}
function make(tag,className,text){const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;}
function safeExternal(target){try{const url=new URL(target,location.href);return ['http:','https:','file:'].includes(url.protocol)?url.href:null;}catch{return null;}}
function selectCell(cell){state.selected=cell;$('cell-address').textContent=cell.address;$('formula-value').textContent=cell.text;document.querySelectorAll('.selected').forEach(node=>node.classList.remove('selected'));const node=grid.querySelector('[data-address="'+cell.address+'"]');if(node)node.classList.add('selected');$('details-title').textContent=cell.address+' | '+DATA.sheets[state.sheet].name;$('details-content').replaceChildren(make('div','',cell.text));if(cell.comment){$('details-content').append(make('div','details-label','\u539f\u59cb\u6279\u6ce8 / \u5f15\u7528'),make('div','',cell.comment));}}
function showSheet(index,reset=true){state.sheet=index;const sheet=DATA.sheets[index], cellMap=maps[index];const table=make('table');const colgroup=make('colgroup');const first=make('col');first.style.width='42px';colgroup.append(first);sheet.widths.forEach(width=>{const col=make('col');col.style.width=width+'px';colgroup.append(col);});table.append(colgroup);
 const letters=make('tr','letters');letters.append(make('th','row-number',''));for(let col=1;col<=sheet.cols;col++)letters.append(make('th','',letter(col)));const head=make('thead');head.append(letters);table.append(head);const body=make('tbody');const blocked=new Set(), merged=new Map();sheet.merges.forEach(range=>{merged.set(range.top+':'+range.left,range);for(let row=range.top;row<=range.bottom;row++)for(let col=range.left;col<=range.right;col++)if(row!==range.top||col!==range.left)blocked.add(row+':'+col);});let shown=0;
 for(let row=1;row<=sheet.rows;row++){const id=cellMap.get('A'+row);const packageRow=id&&/^[A-Z]+-\d{2,}$/.test(id.text);if(packageRow&&((state.domain&&cellMap.get('B'+row)?.text!==state.domain)||(state.query&&!sheet.cells.some(cell=>cell.row===row&&cell.text.toLowerCase().includes(state.query)))))continue;if(packageRow)shown++;const tr=make('tr');tr.dataset.row=row;tr.style.height=sheet.heights[row-1]+'px';tr.append(make('th','row-number',String(row)));
  for(let col=1;col<=sheet.cols;col++){if(blocked.has(row+':'+col))continue;const address=letter(col)+row,cell=cellMap.get(address),range=merged.get(row+':'+col);const td=make('td','data-cell');td.dataset.address=address;td.dataset.row=row;td.dataset.col=col;if(range){td.colSpan=range.right-range.left+1;td.rowSpan=range.bottom-range.top+1;}const isMatrix=index===DATA.matrixIndex;const isHeader=isMatrix&&row===DATA.headerRow;if(isHeader)td.classList.add('header-cell');if(isMatrix&&col<=4&&!range){td.classList.add('sticky-col');if(col===1)td.classList.add('first-col');td.style.left=(42+sheet.widths.slice(0,col-1).reduce((total,width)=>total+width,0))+'px';}
   td.style.background=cell?.fill||'#fff';if(cell){td.style.color=cell.color;td.style.fontWeight=cell.bold?'700':'400';td.style.fontSize=Math.min(18,Math.max(12,cell.size*4/3))+'px';const label=make('span',cell.link?'cell-link':'',cell.text);td.append(label);if(cell.comment)td.classList.add('has-comment');td.addEventListener('click',()=>{selectCell(cell);if(cell.link){if(cell.link.location)follow(cell.link.location);else if(cell.link.target){const target=safeExternal(cell.link.target);if(target)window.open(target,'_blank','noopener,noreferrer');}}});}
   tr.append(td);}body.append(tr);}table.append(body);grid.replaceChildren(table);if(reset){grid.scrollLeft=0;grid.scrollTop=0;}
 $('sheet-tabs').replaceChildren(...DATA.sheets.map((item,itemIndex)=>{const tab=make('button','sheet-tab'+(itemIndex===index?' active':''),item.name);tab.type='button';tab.setAttribute('aria-current',itemIndex===index?'page':'false');tab.addEventListener('click',()=>{state.query='';state.domain='';$('search').value='';$('domain-filter').value='';showSheet(itemIndex);});return tab;}));$('domain-filter').disabled=index!==DATA.matrixIndex;$('search').disabled=index!==DATA.matrixIndex;$('row-count').textContent=index===DATA.matrixIndex?shown+' / '+DATA.packageCount+' WP':sheet.rows+' \u884c';$('status-sheet').textContent=sheet.name;$('status-source').textContent='\u672c\u5730\u5de5\u4f5c\u7c3f\u5feb\u7167';const initial=sheet.cells[0];if(initial)selectCell(initial);
}
function follow(locationText){const match=locationText.match(/^'((?:[^']|'')+)'!([A-Z]+)(\d+)$/);if(!match)return;const name=match[1].replace(/''/g,"'");const index=DATA.sheets.findIndex(sheet=>sheet.name===name);if(index<0)return;state.query='';state.domain='';$('search').value='';$('domain-filter').value='';showSheet(index);requestAnimationFrame(()=>{const target=grid.querySelector('[data-address="'+match[2]+match[3]+'"]');if(target){target.scrollIntoView({block:'center',inline:'center'});const cell=maps[index].get(match[2]+match[3]);if(cell)selectCell(cell);}});}
function viewportRect(){const rect=grid.getBoundingClientRect();return {left:rect.left,top:rect.top,right:rect.right,bottom:rect.bottom};}
function intersect(rect,clip){return {left:Math.max(rect.left,clip.left),top:Math.max(rect.top,clip.top),right:Math.min(rect.right,clip.right),bottom:Math.min(rect.bottom,clip.bottom)};}
function targetRect(step){if(step.region==='tabs'){const rect=$('sheet-tabs').getBoundingClientRect();return {left:rect.left+5,top:rect.top+2,right:Math.min(rect.right-5,rect.left+560),bottom:rect.bottom-2};}const viewport=viewportRect();const mobile=innerWidth<=1100;const sticky=state.sheet===DATA.matrixIndex?(mobile?42+DATA.sheets[state.sheet].widths[0]:42+DATA.sheets[state.sheet].widths.slice(0,4).reduce((sum,width)=>sum+width,0)):42;const clip={...viewport,left:viewport.left+(step.col>4&&state.sheet===DATA.matrixIndex?sticky:42)};const lastRow=innerHeight<720?step.row:step.lastRow;const rects=[...grid.querySelectorAll('.data-cell')].filter(node=>+node.dataset.row>=step.row&&+node.dataset.row<=lastRow&&+node.dataset.col>=step.col&&+node.dataset.col<=step.lastCol).map(node=>intersect(node.getBoundingClientRect(),clip)).filter(rect=>rect.right>rect.left&&rect.bottom>rect.top);if(!rects.length)return {left:viewport.left+45,top:viewport.top+30,right:Math.min(viewport.right-8,viewport.left+330),bottom:Math.min(viewport.bottom-8,viewport.top+150)};return {left:Math.min(...rects.map(rect=>rect.left)),top:Math.min(...rects.map(rect=>rect.top)),right:Math.max(...rects.map(rect=>rect.right)),bottom:Math.max(...rects.map(rect=>rect.bottom))};}
function setBox(node,left,top,width,height){Object.assign(node.style,{left:left+'px',top:top+'px',width:Math.max(0,width)+'px',height:Math.max(0,height)+'px'});}
function placeTour(){
 if(!state.touring)return;
 const raw=targetRect(DATA.tour[state.step]);
 const rect={left:Math.max(4,raw.left-3),top:Math.max(4,raw.top-3),right:Math.min(innerWidth-4,raw.right+3),bottom:Math.min(innerHeight-4,raw.bottom+3)};
 const width=card.offsetWidth,height=card.offsetHeight,pad=12,gap=innerHeight<720?16:24;
 function candidates(){
    const centered=Math.max(pad,Math.min(innerWidth-width-pad,(rect.left+rect.right-width)/2));
    return [{left:centered,top:rect.bottom+gap,side:'below'},{left:centered,top:rect.top-height-gap,side:'above'},
     {left:rect.right+gap,top:Math.max(pad,Math.min(innerHeight-height-pad,rect.top)),side:'right'},
     {left:rect.left-width-gap,top:Math.max(pad,Math.min(innerHeight-height-pad,rect.top)),side:'left'}];
 }
 function choose(){return candidates().find(item=>item.left>=pad&&item.top>=pad&&item.left+width<=innerWidth-pad&&item.top+height<=innerHeight-pad);}
 let position=choose();
 if(!position){
    const availableRight=innerWidth-width-pad-gap;
    if(availableRight-rect.left>=100){rect.right=Math.min(rect.right,availableRight);position=choose();}
    else if(rect.right-(width+pad+gap)>=100){rect.left=Math.max(rect.left,width+pad+gap);position=choose();}
 }
 if(!position){
    const availableBelow=innerHeight-height-pad-gap;
    if(availableBelow-rect.top>=30){rect.bottom=Math.min(rect.bottom,availableBelow);position=choose();}
 }
 if(!position)position={left:Math.max(pad,innerWidth-width-pad),top:Math.max(pad,innerHeight-height-pad),side:'below'};
 setBox($('tour-highlight'),rect.left,rect.top,rect.right-rect.left,rect.bottom-rect.top);
 setBox($('mask-top'),0,0,innerWidth,rect.top);setBox($('mask-bottom'),0,rect.bottom,innerWidth,innerHeight-rect.bottom);
 setBox($('mask-left'),0,rect.top,rect.left,rect.bottom-rect.top);setBox($('mask-right'),rect.right,rect.top,innerWidth-rect.right,rect.bottom-rect.top);
 card.style.left=position.left+'px';card.style.top=position.top+'px';card.dataset.placement=position.side;
 const centerX=(rect.left+rect.right)/2,centerY=(rect.top+rect.bottom)/2;
 let startX=position.left+width/2,startY=position.top,endX=centerX,endY=rect.bottom+2;
 if(position.side==='above'){startY=position.top+height;endY=rect.top-2;}
 else if(position.side==='right'){startX=position.left;startY=position.top+Math.min(height-28,Math.max(28,centerY-position.top));endX=rect.right+2;endY=centerY;}
 else if(position.side==='left'){startX=position.left+width;startY=position.top+Math.min(height-28,Math.max(28,centerY-position.top));endX=rect.left-2;endY=centerY;}
 const bendX=position.side==='below'||position.side==='above'?startX:(startX+endX)/2;
 const bendY=position.side==='below'||position.side==='above'?(startY+endY)/2:startY;
 $('arrow-path').setAttribute('d',`M ${startX} ${startY} Q ${bendX} ${bendY} ${endX} ${endY}`);
}
function goStep(index){state.step=Math.max(0,Math.min(DATA.tour.length-1,index));const step=DATA.tour[state.step];state.query='';state.domain='';$('search').value='';$('domain-filter').value='';showSheet(step.sheet);$('tour-title').textContent=step.title;$('tour-body').textContent=step.body;$('tour-index').textContent='DTSV WORKBOOK  /  '+(state.step+1)+' / '+DATA.tour.length;$('tour-back').disabled=state.step===0;$('next-label').textContent=state.step===DATA.tour.length-1?'\u5b8c\u6210':'\u4e0b\u4e00\u6b65';$('tour-steps').replaceChildren(...DATA.tour.map((item,itemIndex)=>{const button=make('button','step-dot'+(itemIndex===state.step?' current':itemIndex<state.step?' done':''),String(itemIndex+1));button.setAttribute('aria-label','\u7b2c '+(itemIndex+1)+' \u6b65\uff1a'+item.title);button.setAttribute('aria-current',itemIndex===state.step?'step':'false');button.addEventListener('click',()=>goStep(itemIndex));return button;}));requestAnimationFrame(()=>{if(step.region!=='tabs'){const widths=DATA.sheets[state.sheet].widths;const mobile=innerWidth<=1100;const sticky=state.sheet===DATA.matrixIndex?(mobile?42+widths[0]:42+widths.slice(0,4).reduce((sum,width)=>sum+width,0)):42;grid.scrollLeft=Math.max(0,42+widths.slice(0,step.col-1).reduce((sum,width)=>sum+width,0)-sticky);const row=grid.querySelector('tr[data-row="'+step.row+'"]');if(row)grid.scrollTop=Math.max(0,row.offsetTop-25);}requestAnimationFrame(()=>{placeTour();$('tour-next').focus({preventScroll:true});});});}
function startTour(){state.touring=true;root.hidden=false;app.inert=true;$('details').hidden=true;goStep(0);}
function stopTour(){state.touring=false;root.hidden=true;app.inert=false;$('tour-restart').focus({preventScroll:true});}
$('source-name').textContent=DATA.sourceName;$('download-workbook').href=DATA.sourceHref;
const domains=[...new Set(DATA.sheets[DATA.matrixIndex].cells.filter(cell=>cell.col===2&&cell.row>DATA.headerRow).map(cell=>cell.text))];$('domain-filter').append(new Option('\u5168\u90e8\u5de5\u4f5c\u57df',''),...domains.map(domain=>new Option(domain,domain)));
$('search').addEventListener('input',event=>{state.query=event.target.value.trim().toLowerCase();showSheet(state.sheet);});$('domain-filter').addEventListener('change',event=>{state.domain=event.target.value;showSheet(state.sheet);});$('details-toggle').addEventListener('click',()=>{$('details').hidden=!$('details').hidden;});$('details-close').addEventListener('click',()=>{$('details').hidden=true;});$('tour-restart').addEventListener('click',startTour);$('tour-close').addEventListener('click',stopTour);$('tour-skip').addEventListener('click',stopTour);$('tour-back').addEventListener('click',()=>goStep(state.step-1));$('tour-next').addEventListener('click',()=>state.step===DATA.tour.length-1?stopTour():goStep(state.step+1));
document.addEventListener('keydown',event=>{if(!state.touring)return;if(event.key==='Escape'){event.preventDefault();stopTour();}else if(event.key==='ArrowRight'){event.preventDefault();state.step===DATA.tour.length-1?stopTour():goStep(state.step+1);}else if(event.key==='ArrowLeft'){event.preventDefault();goStep(state.step-1);}else if(event.key==='Tab'){const items=[...card.querySelectorAll('button:not(:disabled)')];const first=items[0],last=items[items.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}});
grid.addEventListener('scroll',()=>{if(state.touring)requestAnimationFrame(placeTour);});window.addEventListener('resize',()=>{if(state.touring)goStep(state.step);});new ResizeObserver(()=>{if(state.touring)requestAnimationFrame(placeTour);}).observe(grid);
showSheet(DATA.matrixIndex);requestAnimationFrame(startTour);
</script></body></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(build_workbook_walkthrough(args.source, args.output), ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
