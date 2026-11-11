"""Build a source-preserving matrix of DTSV work types and proposed packages."""
from __future__ import annotations

import argparse
from collections import Counter
from colorsys import hls_to_rgb
from copy import copy
from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
import json
from math import ceil
from pathlib import Path
import re
from unicodedata import east_asian_width

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.hyperlink import Hyperlink
from openpyxl.worksheet.views import Selection

from .free_test_effort_breakdown import SOURCE_SHEET, _literal


STRATEGY_VERSIONS = {'4447601857': 89, '8681681592': 14, '8735186775': 24}


PREFIX_DEFINITIONS = {
    'REQ': ('Requirements', '需求输入、验收口径与适用范围。回答测什么、预期是什么、哪些版本/配置适用。'),
    'CASE': ('Test Case', '正式用例的追溯、步骤与断言设计、评审和版本整改；不是脚本执行结果。'),
    'PLAN': ('Planning', '风险优先级、测试重点、周计划及专项任务/资源安排；不是实际执行量。'),
    'ENV': ('Environment', '软件版本、升级前置、刷写、刷后检查、异常处理、TSP 准入及测试配置就绪。'),
    'ROAD': ('Road Testing', '路线与连续客户任务设计、动态道路执行及跨地点连续使用观察。'),
    'EXEC': ('Execution', '按既定脚本执行、判定预期、记录结果并关联证据/缺陷。'),
    'FREE': ('Free / Exploratory Testing', '由测试者主动设计和调整客户表达、状态、角色、时序、恢复及环境组合的探索目标。'),
    'EVID': ('Evidence', '可复现路径、对照、恢复边界，以及带时间、版本、平台的日志/Trace 证据包。'),
    'DEF': ('Defect', '缺陷提交、质量与分类、关联/责任对齐、补证跟踪、修复验证及关闭风险核验。'),
    'ASSET': ('Test Asset', '将实际发现转成回归候选，并进一步编写、评审和更新可复用的正式测试资产。'),
    'REP': ('Reporting', '测试摘要、AIDA 确认材料、质量风险建议，以及周期报告发布、归档和评审反馈。'),
    'GOV': ('Governance', '测试治理中的问题同步、KPI 口径、异常核对和跨团队依赖/决策行动跟踪。'),
    'OPS': ('Operations', '车辆设备物流、平台共享登记、使用冲突和跨项目资源阻塞协调。'),
    'MGT': ('Management', '项目接口/里程碑、合作方交付和非测试项目状态的管理与对齐。'),
    'TOOL': ('Tooling', '观测采集工具、自动化、数据分析和数据校验能力的开发维护；不同于直接使用工具。'),
}


DOMAIN_DEFINITIONS = {
    '需求与范围': ('定义待验证的客户功能、预期结果、需求歧义及版本/市场/配置边界。', '不含正式脚本编写、用例执行或实际发布批准。'),
    '测试资产与计划': ('建立可复用、可追溯的用例，评审版本，安排风险优先级与任务，并将发现反馈到回归资产。', '计划、编写和评审不是实际执行；回归候选也不等于已落库。'),
    '平台与版本就绪': ('使版本、车辆配置、刷写结果、账号设备权限及观测条件达到可开展测试的状态。', '不同于平台共享日历或运输物流；刷后基础检查不等于完整功能回归。'),
    '测试实施与探索': ('实施道路任务、既定脚本和主动探索，形成实际行为、通过/失败和异常观察。', '道路是执行环境，脚本/探索是测试方式，可能交叉；同一时间不可重复记入多包。'),
    '证据与缺陷闭环': ('将异常转成可审查证据与缺陷记录，并持续澄清、验证修复及核验关闭依据和残余风险。', '日志与复现不是 Free-test 独占；ticket 数不自动等于独立有效缺陷数。'),
    '报告与决策': ('将结果、覆盖限制、问题及风险整理成报告、确认材料与质量建议，并发布归档和评审。', '汇总报告不会新增测试执行量；测试建议不自动等于发布或 TSP 批准。'),
    '组织与协同': ('对齐问题、指标、跨团队依赖、项目/合作方接口与非测试项目状态，推动决策行动。', '不是把所有会议都算作测试实施，也不是组织架构或已批准的责任划分。'),
    '平台与运营支撑': ('保障车辆设备的交付、共享使用和资源可获得性，处理物流、使用冲突及协调阻塞。', '侧重资源运营；技术刷写/排障属于平台与版本就绪，实际道路测试属于测试实施。'),
    '工具与数据能力': ('建设并维护可复用的观测、自动化、分析报告和数据质量校验能力。', '开发工具与用工具执行/编报告分开识别；同一开发时间不能同时计入 Z 与 AI。'),
}


@dataclass(frozen=True)
class Package:
    code: str
    domain: str
    name: str
    output: str
    actions: dict[str, str]
    clauses: str
    evidence: str
    boundary: str
    unit: str


PACKAGES = tuple(Package(*values) for values in (
    ('REQ-01', '需求与范围', '功能需求、变更与客户问题输入收集', '带版本、功能及来源的需求/变更清单',
     {'M2': '主项\n收集并整理输入', 'M33': '协同\n审核来源与完整性', 'P2': '协同\n转入测试重点'},
     '2; 5.1; 11', 'requirements', '需求收集不是正式用例编写，也不等同于新增需求数量。', '需求/变更条目'),
    ('REQ-02', '需求与范围', '验收边界与需求歧义澄清', '可讨论的预期行为、适用条件及待决问题',
     {'M2': '主项\n澄清预期与边界', 'M33': '主项\n确认验收口径', 'N2': '协同\n转为可验证断言', 'N33': '协同\n审查断言依据', 'Z2': '条件\n需求缺口需产品探索时'},
     '5.1; 11', 'requirements', '探索观察不自动成为需求真值；正式需求或决策仍需确认。', '澄清/决策项'),
    ('REQ-03', '需求与范围', '功能、版本、配置与市场适用性界定', '功能/版本/配置/市场适用范围',
     {'M2': '主项\n整理范围与差异', 'M33': '主项\n评审适用边界', 'P2': '协同\n定义测试覆盖重点', 'P33': '协同\n协调专项范围', 'S33': '协同\n匹配所需车辆配置'},
     '5.2; 7.4; 7.6; 7.8', 'requirements', '兼容性方向不等于已完成全部平台或市场矩阵。', '适用范围条目'),
    ('CASE-01', '测试资产与计划', '需求到测试资产的追溯与缺口检查', '需求/功能/用例关联及待补覆盖清单',
     {'N2': '主项\n建立关联并识别缺口', 'N33': '主项\n检查覆盖与追溯', 'AE2': '协同\n核验覆盖统计口径'},
     '5.1; 5.3; 7.1; 11', 'cases', '已读取脚本样本不是全量需求覆盖率或评审完成率。', '关联/覆盖缺口'),
    ('CASE-02', '测试资产与计划', '正式用例步骤、断言与数据设计', '带前置条件、步骤、预期及版本的测试用例',
     {'N2': '主项\n编写或更新脚本', 'N33': '协同\n反馈设计问题', 'Y2': '协同\n反馈可执行性问题'},
     '5.1; 7.1', 'cases', '编写和执行是不同活动；执行反馈不重复计为新用例。', '用例/脚本版本'),
    ('CASE-03', '测试资产与计划', '正式用例评审、整改与版本确认', '评审意见、整改闭环及确认的用例版本',
     {'N33': '主项\n评审适用性与断言', 'N2': '协同\n整改并提交版本'},
     '5.1', 'review', '读取脚本不证明脚本已经通过评审。', '评审/版本'),
    ('PLAN-01', '测试资产与计划', '风险优先级与测试重点选取', '风险排序、优先场景及测试目标',
     {'P2': '主项\n确定测试重点', 'P33': '协同\n确认专项优先级', 'V2': '协同\n形成周路测目标', 'W2': '协同\n形成长途任务目标', 'Y2': '协同\n选择脚本集合', 'Z2': '协同\n选择探索风险目标'},
     '5.2; 7.3', 'scope', '同一风险目标可被多种活动引用，不能因此重复计设计时间。', '风险/测试目标'),
    ('PLAN-02', '测试资产与计划', '周计划、专项任务与资源排期', '任务、责任接口、平台需求及执行窗口',
     {'P2': '主项\n组织测试计划', 'P33': '主项\n协调专项窗口', 'S33': '协同\n安排测试车辆', 'AD2': '协同\n申请共享平台', 'AD33': '协同\n协调资源冲突'},
     '源表工作项；建议拆分', 'coordination', '排期不等于实际执行，也不是额外编制或实测投入。', '任务/排期'),
    ('PLAN-03', '测试资产与计划', '按需变更、紧急修复与影响范围验证安排', '专项范围、检查项及反馈入口',
     {'P33': '主项\n协调专项验证', 'P2': '协同\n形成重点与边界', 'O33': '协同\n关联问题与影响', 'Y2': '条件\n需要正式脚本验证时', 'Z2': '条件\n需要探索影响范围时'},
     '7.5; 7.10; 7.11', 'scope', '按需验证范围需确认；不宣称每次活动覆盖性能、安全等全部质量属性。', '专项任务'),
    ('ENV-01', '平台与版本就绪', '软件版本、升级包与前置依赖核对', '目标版本、升级包及前置条件记录',
     {'R2': '主项\n准备版本与依赖', 'R33': '主项\n检查准备就绪', 'S2': '协同\n确认刷写输入', 'U2': '协同\n保留版本依据'},
    '6.1；源表工作项', 'platform', '版本准备与实际刷写、刷后检查分开记录。', '版本/准备记录'),
    ('ENV-02', '平台与版本就绪', '刷写工具、连接及升级路径准备', '可执行升级路径与工具连接检查记录',
     {'R2': '主项\n准备工具和连接', 'R33': '主项\n检查升级路径', 'S2': '协同\n核对实施条件'},
     '源表工作项；建议拆分', 'platform', '具体刷写安全规范和审批要求需责任人确认，不能从缺陷数据推定。', '准备/检查记录'),
    ('ENV-03', '平台与版本就绪', 'IuK 软件刷写实施', '刷写完成记录、版本及异常结果',
     {'S2': '主项\n实施 IuK 刷写', 'R33': '条件\n需要现场检查或升级支持时'},
     '源表工作项；建议拆分', 'platform', '只包含实际刷写；下载准备、排期、等待和刷后测试分别辨识。', '刷写任务'),
    ('ENV-04', '平台与版本就绪', '刷后版本、基础功能与车辆交接检查', '版本核验、基础检查和交接结果',
     {'T2': '主项\n执行刷后检查', 'S2': '协同\n交接刷写结果', 'R33': '协同\n复核异常与准入', 'U2': '协同\n汇入检查报告'},
     '源表工作项；建议拆分', 'platform', '基础准入检查不等同于完整功能回归。', '交接/检查记录'),
    ('ENV-05', '平台与版本就绪', '刷写异常诊断、重试与升级支持', '异常路径、恢复尝试及升级处理记录',
     {'T2': '主项\n诊断并尝试恢复', 'S2': '协同\n执行必要重试', 'R33': '主项\n协调异常处理', 'AB2': '条件\n已有缺陷需要补充证据时'},
     '源表工作项；建议拆分', 'platform', '已登记到缺陷维护的同一调查时间不得再次加入刷写支持。', '异常处理活动'),
    ('ENV-06', '平台与版本就绪', 'TSP 刷写结果与准入材料整理', '可审查的版本、检查结果和问题清单',
     {'U2': '主项\n编制 TSP 报告', 'S2': '协同\n提供刷写记录', 'R33': '协同\n核验准备及检查结果', 'U33': '协同\n提出材料补充要求'},
     '源表工作项；建议拆分', 'platform', '报告编制和批准决策是不同职责，不合并为一次批准量。', 'TSP 报告'),
    ('ENV-07', '平台与版本就绪', 'TSP 材料评审与批准决策', '批准、限制或退回意见及依据',
     {'U33': '主项\n评审并记录决策', 'U2': '协同\n回应材料问题', 'R33': '协同\n提供技术准入意见'},
     '源表工作项；建议拆分', 'platform', 'TSP 批准不代替整车技术发布或 AIDA 结论。', '审批/决策项'),
    ('ENV-08', '平台与版本就绪', '车辆能力、配置匹配与使用安排', '车辆配置匹配及可用窗口记录',
     {'S33': '主项\n安排适配车辆', 'AD2': '协同\n确认共享需求', 'AD33': '主项\n协调平台可用性', 'P33': '协同\n确认专项资源需求'},
     '7.4; 7.6；源表工作项', 'platform', '台账和安排不证明实际车辆利用率；不重复计物流运输。', '车辆/使用窗口'),
    ('ENV-09', '平台与版本就绪', '测试账号、设备、权限与观测前置配置', '可重建的账号、设备、配置及观测条件',
     {'T2': '条件\n刷后需要恢复测试前置时', 'Y2': '主项\n按脚本建立前置', 'Z2': '主项\n按探索目标建立前置', 'V2': '条件\n路测需要专用前置时', 'W2': '条件\n长途任务需要专用前置时'},
     '5.1; 6.3; 7.3', 'platform', '属于执行活动的前置或独立准备，应选一个主记账位置。', '配置/准备活动'),
    ('ROAD-01', '测试实施与探索', '路线、地点与连续客户任务设计', '路线、目的地属性、客户任务和观察点',
     {'V2': '主项\n设计周路测场景', 'W2': '主项\n设计长途任务链', 'P2': '协同\n输入测试重点', 'Z2': '条件\n主动扩展真实场景时'},
     '2; 7.3; 7.8', 'field', '路线是场景输入；地点候选数不等于路线数或实地行车次数。', '路线/客户任务'),
    ('ROAD-02', '测试实施与探索', '动态路况中的客户功能验证', '路况、功能行为、通过结果与异常记录',
     {'V2': '主项\n执行周路测', 'W2': '主项\n执行长途动态验证', 'Y2': '条件\n路测中执行既定脚本时', 'Z2': '条件\n路测中主动探索时'},
     '2; 7.8', 'field', '路测是执行环境；同一分钟不得同时计入路测和 Y/Z。', '路测活动/任务'),
    ('ROAD-03', '测试实施与探索', '长时间、跨地点与连续使用状态验证', '连续任务过程、状态变化及恢复观察',
     {'W2': '主项\n执行连续客户任务', 'V2': '条件\n周路测包含连续任务时', 'Y2': '条件\n脚本明确规定连续场景时', 'Z2': '条件\n主动改变任务链时'},
     '2; 7.2; 7.3', 'field', '连续驾驶或等待不自动全部属于主动测试工时；不据此计算耐久覆盖率。', '连续任务活动'),
    ('EXEC-01', '测试实施与探索', '既定用例步骤执行与预期判定', '指定脚本版本的实际步骤、结果和 pass/fail',
     {'Y2': '主项\n按已定义脚本执行', 'V2': '条件\n在周路测执行脚本时', 'W2': '条件\n在长途路测执行脚本时'},
     '5.1; 7.1', 'cases', '不因执行中发现缺陷就把这段执行时间改记 Free test。', '用例执行记录'),
    ('EXEC-02', '测试实施与探索', '执行结果记录、证据关联与完成状态核验', '关联版本、平台、结果及缺陷的执行记录',
     {'Y2': '主项\n记录执行结果', 'Q2': '协同\n核验报告输入', 'AE2': '协同\n核验执行统计', 'Q33': '条件\n用于 AIDA 结论时核验'},
     '5.3; 6.3; 7.1', 'cases', '同一执行记录被报告和会议引用时不新增执行次数。', '可追溯执行记录'),
    ('FREE-01', '测试实施与探索', '客户表达、输入机制与交互边界探索', '表达/输入对照、非命令边界及异常触发路径',
     {'Z2': '主项\n主动设计表达与输入变体', 'Y2': '条件\n脚本已显式定义同类目标时'},
    '2; 7.3; 7.6', 'input', '操作信号不是确认的变体试验量；方法不是 Free-test 独占。', '探索目标/对照组'),
    ('FREE-02', '测试实施与探索', '状态历史、切换返回与生命周期探索', '状态迁移矩阵、保持结果及入口边界',
     {'Z2': '主项\n改变历史状态与 LC 入口', 'Y2': '条件\n脚本已定义状态或 LC 场景时', 'V2': '条件\n道路场景含同类目标时', 'W2': '条件\n连续任务含同类目标时'},
     '7.2; 7.3', 'state', '状态、LC 信号及条件候选口径不同；正常上电前置不等于电源探索。', '状态/入口场景'),
    ('FREE-03', '测试实施与探索', '多设备、多屏与座位角色组合探索', '发起端、角色、屏幕及控制对象组合的观察结果',
     {'Z2': '主项\n主动组合端、屏幕与角色', 'Y2': '条件\n脚本已有同类组合时'},
     '7.3; 7.4; 7.6', 'roles', '跨屏信号不证明完整设备矩阵；中国差异范围不等于全球车控 E2E 责任。', '角色/设备组合'),
    ('FREE-04', '测试实施与探索', '操作时序、并发与资源交接探索', '时机/顺序/重叠对照及退出后状态',
     {'Z2': '主项\n改变操作时机与重叠关系', 'Y2': '条件\n脚本已规定同类时序时'},
     '7.3; 7.6', 'timing', '词语命中不是并发根因证据；快速操作不自动等于性能测量。', '时序/干扰组合'),
    ('FREE-05', '测试实施与探索', '负向配置、中断重入与恢复探索', '权限/开关/中断对照及恢复边界',
     {'Z2': '主项\n主动改变负向与恢复路径', 'Y2': '条件\n脚本明确规定同类目标时', 'T2': '条件\n刷后异常恢复适用时'},
     '3; 7.3', 'recovery', '故障注入/恢复方向不证明已实施系统级故障注入或覆盖所有恢复方式。', '中断/恢复路径'),
    ('FREE-06', '测试实施与探索', '真实地点与客户旅程组合探索', '具体客户任务、环境上下文及异常/恢复观察',
     {'Z2': '主项\n扩展客户任务与环境条件', 'V2': '条件\n周路测承载该探索时', 'W2': '条件\n长途路线承载该探索时', 'Y2': '条件\n场景已固化为脚本时'},
     '2; 7.3; 7.8', 'field', '与路线规划、道路执行是不同交付；同一活动时间只能选一个主包。', '客户场景活动'),
    ('EVID-01', '证据与缺陷闭环', '异常复现、条件对照与恢复边界确认', '可复现反例、对照结果及恢复条件',
     {'Z2': '主项\n提单前收敛探索发现', 'Y2': '条件\n确认脚本执行异常时', 'AA2': '协同\n整理提单前复现依据', 'AB2': '条件\n提单后补充验证时', 'T2': '条件\n确认刷后异常时'},
     '6.3; 7.3', 'confirmation', '提单前与提单后是不同记账时段；次数和 LC 答案不是工时。', '发现调查活动'),
    ('EVID-02', '证据与缺陷闭环', '日志、Trace 与环境证据采集关联', '带时间、版本、平台和触发路径的证据包',
     {'Y2': '主项\n采集执行证据', 'Z2': '主项\n采集探索证据', 'V2': '条件\n采集道路异常证据时', 'W2': '条件\n采集长途异常证据时', 'T2': '条件\n采集刷后异常证据时', 'AA2': '协同\n关联提单附件', 'AB2': '条件\n补充诊断证据时'},
     '6.3', 'confirmation', '这是共同执行要求，不是 Free-test 独有贡献；共享附件不重复计采集。', '证据包'),
    ('DEF-01', '证据与缺陷闭环', '新缺陷结构化记录与证据提交', '完整缺陷记录、预期/实际、环境和证据链接',
     {'AA2': '主项\n编写并提交缺陷', 'O2': '协同\n检查记录质量', 'O33': '协同\n确认跟踪入口'},
    '6.2; 6.3；源表工作项', 'defects', 'ticket 是发现记录，不自动等于去重后独立有效缺陷。', '缺陷提交记录'),
    ('DEF-02', '证据与缺陷闭环', '缺陷质量门禁、分类与影响判断', '问题分类、影响依据及需补充字段',
     {'O2': '主项\n检查质量和分类依据', 'O33': '主项\n对齐影响与处理', 'AA2': '协同\n提供初始判断依据', 'AB2': '协同\n更新证据与分类'},
     '源表工作项；建议拆分', 'defects', 'Q-Gate 具体准入和分类规则待负责人确认，本表不替代正式规则。', '门禁/分类记录'),
    ('DEF-03', '证据与缺陷闭环', '已知问题、重复项与责任接口对齐', '已知/新问题判断、重复关联及责任接口',
     {'O2': '主项\n检查关联与重复', 'O33': '主项\n协调责任接口', 'AA2': '协同\n提交前查询已知问题', 'AB2': '协同\n维护关联和结论'},
     '7.3；源表工作项', 'defects', '语义相似不等于重复缺陷；责任和有效性仍需正式确认。', '问题关联/决策'),
    ('DEF-04', '证据与缺陷闭环', '提单后研发澄清、补证与进度跟踪', '澄清答复、补证记录、阻塞和下一步',
     {'AB2': '主项\n维护票据与补充证据', 'O33': '主项\n跟踪责任与阻塞', 'X2': '协同\n同步需决策问题'},
     '源表工作项；建议拆分', 'defects', '提单后的维护不得再次记为提单前 Free-test 调查。', '跟进/补证活动'),
    ('DEF-05', '证据与缺陷闭环', '修复验证与相关场景回归', '修复验证结果及相关路径的残余问题',
     {'AB2': '主项\n组织修复验证与票据更新', 'Y2': '条件\n按正式脚本回归时', 'Z2': '条件\n探索修复影响范围时', 'V2': '条件\n需要道路复现时'},
     '7.1; 7.5', 'defects', '票据更新、脚本执行和道路验证的同一分钟只选择一个主记账位置。', '修复验证活动'),
    ('DEF-06', '证据与缺陷闭环', '关闭依据与残余风险核验', '关闭理由、验证依据及未解决风险说明',
     {'AB2': '主项\n核验关闭证据', 'O33': '主项\n对齐处理结论', 'O2': '协同\n复核分类和依据', 'Q2': '协同\n汇入残余风险'},
     '源表工作项；建议拆分', 'defects', '没有完整关闭审计分母，不能从本矩阵推导闭环质量率。', '关闭/风险核验'),
    ('ASSET-01', '测试资产与计划', '发现路径转回归候选与正式用例', '回归建议及经评审后的用例更新',
     {'Z2': '协同\n提供新发现路径', 'AB2': '协同\n提供修复和复现依据', 'N2': '主项\n转为正式用例', 'N33': '主项\n评审新增或更新资产', 'Y2': '条件\n确认脚本可执行性时'},
     '7.1; 11', 'review', '回归候选不等于已入库或已通过评审；需额外追踪用例版本。', '回归候选/版本'),
    ('REP-01', '报告与决策', '测试结果、覆盖和问题摘要编制', '带范围、结果、限制和问题的测试摘要',
     {'Q2': '主项\n编制测试结果摘要', 'Q33': '协同\n核验结论所需输入', 'AE2': '协同\n复核统计口径'},
    '5.3; 6.2; 8', 'report', '发现数、脚本数与完整执行覆盖是不同计量单位。', '报告/版本'),
    ('REP-02', '报告与决策', 'AIDA 确认材料、Rating 与结论依据', '确认范围、执行依据、问题和 Rating 说明',
     {'Q33': '主项\n组织 AIDA 结论材料', 'Q2': '协同\n提供可追溯结果', 'Y2': '协同\n关联确认执行记录', 'O33': '协同\n提供阻塞问题结论'},
    '7.1; 7.6; 10', 'report', '中国差异范围与全球车控职责不同；样本数据不证明全部 AIDA 责任完成。', '确认/Rating 材料'),
    ('REP-03', '报告与决策', '发布阻塞与软件质量风险建议', '阻塞问题、验证限制及技术风险建议',
     {'Q33': '主项\n形成质量建议', 'Q2': '协同\n汇总结果和限制', 'O33': '主项\n对齐关键问题与风险', 'AF33': '协同\n协调项目决策'},
    '2; 6.2; 8', 'report', '测试建议不自动等于正式发布批准，也不代替 TSP 批准。', '风险/建议项'),
    ('REP-04', '报告与决策', '周期报告发布、归档与评审反馈', '已发布报告、归档链接及会议反馈/澄清记录',
    {'Q2': '主项\n发布和归档测试报告', 'Q33': '协同\n确认结论材料的发布范围', 'X2': '协同\n分享问题并记录澄清', 'AF33': '条件\n项目 CCB 或关键问题评审时'},
    '6.2; 8', 'report', '报告编制、发布与会议评审是不同活动；没有发布台账不能声称已完成归档。', '报告发布/评审周期'),
    ('GOV-01', '组织与协同', '周问题同步与决策行动跟踪', '问题、决定、责任接口和行动状态',
     {'X2': '主项\n组织或参与问题同步', 'O33': '主项\n跟踪关键行动', 'AB2': '协同\n提供票据进展', 'P2': '协同\n调整测试重点'},
    '6.2; 8；源表工作项', 'coordination', '会议参与与实际调查/修复验证分别记录，不重复计算同一行动。', '同步/行动项'),
    ('GOV-02', '组织与协同', '用例、缺陷 KPI 口径与异常核对', '口径定义、统计结果和异常解释',
     {'AE2': '主项\n监控并解释 KPI', 'Q2': '协同\n提供报告统计', 'O33': '协同\n核对问题状态'},
     '源表工作项；建议拆分', 'report', 'KPI 监控不等于重新执行用例或重新发现缺陷。', '统计周期/异常项'),
    ('GOV-03', '组织与协同', '跨团队专项需求与依赖协调', '依赖、资源冲突、执行窗口及责任约定',
     {'P33': '主项\n协调专项需求', 'AF33': '主项\n对齐项目依赖', 'AD33': '协同\n协调平台约束', 'S33': '协同\n确认车辆窗口'},
     '源表工作项；建议拆分', 'coordination', '这是建议的协同边界，不是已批准的 RACI 或实际参与次数。', '协调/依赖项'),
    ('OPS-01', '平台与运营支撑', '车辆、设备运输与交接物流', '运输、移交、接收及必要物资记录',
     {'AC2': '主项\n安排物流与交接', 'S33': '协同\n确认车辆安排', 'AD2': '协同\n确认共享平台交付', 'AD33': '协同\n协调使用方接口'},
     '源表工作项；建议拆分', 'coordination', '物流运输不等于道路测试；具体物资和行政流程需责任人确认。', '物流/交接活动'),
    ('OPS-02', '平台与运营支撑', '平台共享登记与使用冲突处理', '共享申请、使用日历和冲突处理结果',
     {'AD2': '主项\n登记共享及使用需求', 'AD33': '主项\n协调冲突与接口', 'S33': '协同\n匹配车辆安排'},
     '源表工作项；建议拆分', 'coordination', '共享记录不自动证明利用率；车辆安排和实际使用不能重复计量。', '共享/协调项'),
    ('OPS-03', '平台与运营支撑', '平台阻塞升级与跨项目资源决策', '阻塞说明、处理接口及资源决策',
     {'AD33': '主项\n协调平台阻塞', 'T2': '协同\n提供技术异常依据', 'AF33': '协同\n对齐跨项目取舍'},
     '源表工作项；建议拆分', 'coordination', '技术排障工作与协调决策工作需分别识别。', '阻塞/决策项'),
    ('MGT-01', '组织与协同', '项目接口、里程碑与决策对齐', '项目依赖、里程碑变化和决策行动',
     {'AF33': '主项\n组织项目对齐', 'P33': '协同\n输入专项测试依赖', 'AH33': '协同\n输入非测试项目状态', 'AG33': '协同\n输入合作方依赖'},
     '源表工作项；建议拆分', 'coordination', '项目会议不是全部测试活动的额外加成工时。', '对齐/决策项'),
    ('MGT-02', '组织与协同', '合作方交付、质量与接口管理', '交付约定、质量问题、改进行动和接口记录',
     {'AG33': '主项\n管理合作方交付接口', 'AF33': '协同\n协调项目依赖', 'P33': '协同\n对齐测试需求'},
     '源表工作项；建议拆分', 'coordination', '合同、采购或绩效流程不在当前证据中，不能自行推定。', '交付/接口项'),
    ('MGT-03', '组织与协同', '非测试项目状态与 CTP/MDR 信息维护', '非测试项目状态、变化和待决事项',
     {'AH33': '主项\n维护非测试项目状态', 'AF33': '协同\n对齐里程碑变化', 'AG33': '条件\n涉及合作方状态时'},
     '源表工作项；建议拆分', 'coordination', '不把非测试项目状态包装成测试发现产出；CTP/MDR 细则待确认。', '状态周期/变更项'),
    ('TOOL-01', '工具与数据能力', '探索配置、观测与证据采集工具开发', '工具增量、版本、复用及维护记录',
     {'Z2': '条件\n探索专用且实际记入 Z 时', 'AI33': '主项\n开发共用工具或底座', 'T2': '条件\n提供刷后观测需求时'},
    '6.3; 9；源表工作项', 'tooling', '同一工具开发时间只记 Z 或 AI 一处；没有实测节时就不宣称节约。', '工具版本/增量'),
    ('TOOL-02', '工具与数据能力', '测试自动化脚本、适配与执行支撑', '可执行脚本、适配接口及验证记录',
     {'AI33': '主项\n开发和维护自动化能力', 'N2': '条件\n提供待自动化场景时', 'Y2': '条件\n验证自动化执行时'},
     '源表工作项；建议拆分', 'tooling', '候选脚本或跳过的执行不等于已实现自动化覆盖。', '脚本/适配版本'),
    ('TOOL-03', '工具与数据能力', '测试、缺陷数据分析与报告工具开发', '分析逻辑、可追溯数据集及报告工具版本',
     {'AI33': '主项\n开发分析与报告能力', 'AE2': '协同\n定义 KPI 分析需求', 'Q2': '协同\n定义报告需求并验收'},
     '源表工作项；建议拆分', 'tooling', '开发分析工具与使用工具编制报告是不同活动。', '分析/工具版本'),
    ('TOOL-04', '工具与数据能力', '数据口径、完整性与证据追溯校验', '口径、校验结果、异常和修订记录',
     {'AI33': '主项\n实现或执行数据校验', 'AE2': '主项\n确认 KPI 口径', 'Q2': '主项\n核验报告证据与限制', 'Q33': '条件\n核验 Rating 输入时'},
     '5.3；源表工作项', 'scope', '校验支持质量决策，不构成需求真值或所有记录已人工审核的证明。', '口径/校验批次'),
))


class _PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {'p', 'li', 'tr', 'br', 'h1', 'h2', 'h3', 'h4'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'p', 'li', 'tr', 'h1', 'h2', 'h3', 'h4'}:
            self.parts.append('\n')

    def handle_data(self, data):
        self.parts.append(data)


def _strategy_pages(paths):
    pages = []
    for path in paths:
        page = json.loads(Path(path).read_text(encoding='utf-8'))
        parser = _PageText()
        parser.feed(page['body']['storage']['value'])
        lines = [re.sub(r'\s+', ' ', line).strip() for line in ''.join(parser.parts).splitlines()]
        pages.append({'id': str(page['id']), 'title': page['title'],
                      'version': page['version']['number'], 'when': page['version'].get('when', ''),
                      'url': 'https://atc.bmwgroup.net/confluence/pages/viewpage.action?pageId=' + str(page['id']),
                      'lines': [line for line in lines if line],
                      'drafting': any('drating' in line.casefold() for line in lines)})
    by_id = {page['id']: page for page in pages}
    missing = STRATEGY_VERSIONS.keys() - by_id.keys()
    if missing:
        raise ValueError(f'Required strategy pages missing: {sorted(missing)}')
    if len(by_id) != len(pages):
        raise ValueError('Duplicate strategy page snapshots')
    mismatched = {page_id: (expected, by_id[page_id]['version'])
                  for page_id, expected in STRATEGY_VERSIONS.items()
                  if by_id[page_id]['version'] != expected}
    if mismatched:
        raise ValueError(f'Unreviewed strategy version; revalidate the mapping first: {mismatched}')
    return pages


def _work_columns(source):
    columns = []
    by_label = {}
    for row, last, role, start_people, end_people in ((2, 31, '执行', 3, 28), (33, 35, '管理', 34, 41)):
        for column in range(13, last + 1):
            raw = source.cell(row, column).value
            if not isinstance(raw, str) or not raw.strip():
                continue
            key = re.sub(r'\s+', ' ', raw).strip().casefold()
            if key not in by_label:
                by_label[key] = {'label': raw.strip(), 'sourceRefs': [], 'allocations': []}
                columns.append(by_label[key])
            work = by_label[key]
            work['sourceRefs'].append(source.cell(row, column).coordinate)
            numbers = [source.cell(person, column).value for person in range(start_people, end_people + 1)
                       if isinstance(source.cell(person, column).value, (int, float))
                       and not isinstance(source.cell(person, column).value, bool)]
            work['allocations'].append(f'{role} {sum(numbers):.2f}' if numbers else f'{role}未填')
    for index, work in enumerate(columns, 5):
        work['column'] = index
    return columns


def _evidence(data):
    coverage = data['coverage']
    library = data['discoveryPatternLibrary']
    effort = library['candidateEffort']
    methods = {row['method']: row['freeTestTickets'] for row in data.get('methodComparison', [])}
    patterns = {row['pattern']: row['caseCount'] for row in library['patterns']}

    def method(name):
        return str(methods.get(name, '未提取'))

    def pattern(name):
        return str(patterns.get(name, '未提取'))

    return {
        'requirements': '源表确认该职责；完整需求基线、澄清和评审活动数量未纳入当前数据。',
        'cases': f"已读取 {coverage['testcaseCount']} 个实际脚本样本；没有完整通过执行/工时分母，不代表全部用例库。",
        'review': f"有 {coverage['testcaseCount']} 个脚本样本供审查；正式评审和回归入库完成量未核实。",
        'scope': f"Free-test 范围 {coverage['freeTestTickets']} 条，识别动作 {coverage['freeTestWithActions']} 条；复合候选 {library['candidateTicketCount']} 条，模式可重叠。",
        'platform': '源表确认平台/刷写职责；刷写任务、准入、占用与排障工时未在当前快照量化。',
        'coordination': '源表确认该工作项；此处为建议的具体拆分，会议、物流、接口和管理活动量未统计。',
        'input': f"输入/语音操作信号 {method('Input / language / command variation')}；输入机制复合候选 {pattern('Voice-input channel and mode boundaries')}，自然交谈候选 {pattern('Natural speech and unintended activation')}。口径不同。",
        'state': f"状态切换信号 {method('State / source / account transition')}；LC 动作信号 {method('Lifecycle / power transition')}；生命周期条件复合候选 {pattern('Lifecycle-conditioned customer operation')}，不能相加。",
        'roles': f"跨设备/屏幕信号 {method('Cross-device / cross-screen handoff')}；角色/屏幕媒体复合候选 {pattern('Occupant-role and screen-conditioned media control')}。未证明完整矩阵。",
        'timing': f"时序信号 {method('Timing / repetition / sequence')}；并发信号 {method('Concurrent activity / interference')}；其他音频中调用语音复合候选 {pattern('Voice invocation during another audio activity')}。可重叠。",
        'recovery': f"中断/恢复信号 {method('Interruption / cancellation / recovery')}；显式负向信号 {method('Explicit boundary / negative setup')}；断开重连复合候选 {pattern('Disconnect/reconnect sequence exploration')}。",
        'field': f"地点/道路导航复合候选 {pattern('Location/road-conditioned navigation')}；不是实地行车次数、路线数、长途活动数或工时。",
        'confirmation': f"复合候选中的报告字段：{effort['reportedAttempts']} 次尝试 / {effort['casesWithAttempts']} 票有值；{effort['reportedLifecycles']} 个 LC 答案 / {effort['casesWithLifecycles']} 票有值。不是独立执行事件或工时。",
        'defects': f"当前 Free-test 发现记录 {coverage['freeTestTickets']} 条可供审查；提单、维护和 triage 活动数未单独统计，不是独立有效缺陷数。",
        'report': '已有结构化覆盖/方法/模式结果；报告发布次数、完整执行覆盖及 AIDA 确认完成量未统计。',
        'tooling': '源表 Z 与管理 AI 包含工具职责；工具版本数、复用量、自动化覆盖和实测节时未在此量化。',
    }


def _strategy_note(package, pages):
    main = next(page for page in pages if page['id'] == '4447601857')
    parts = [f"{main['title']} v{main['version']} ({main['when']}); body status: {'Drating' if main['drafting'] else 'not verified'}.",
             main['url'], 'Proposed decomposition; not an approved RACI or a compliance certification.']
    for clause in re.findall(r'\b\d+(?:\.\d+)?\b', package.clauses):
        for index, line in enumerate(main['lines']):
            if re.match(r'^' + re.escape(clause) + r'(?:\s|\.)', line):
                parts.append(' '.join(main['lines'][index:index + 5])[:850])
                break
    if package.evidence in {'input', 'roles', 'state', 'timing', 'field'}:
        for page in pages:
            if page is main:
                continue
            parts.append(f"{page['title']} v{page['version']}: {page['url']}")
            for index, line in enumerate(page['lines']):
                if any(term in line.casefold() for term in ('2812911', '2820755', '2825176', 'three phases')):
                    parts.append(' '.join(page['lines'][max(0, index - 1):index + 4])[:650])
                    break
    return '\n\n'.join(parts)


def _overlap_color(index):
    red, green, blue = hls_to_rgb((index * 0.618033988749895) % 1, 0.88, 0.51)
    return ''.join(f'{round(channel * 255):02X}' for channel in (red, green, blue))


def add_work_package_explanations(matrix_sheet, explanation_sheet) -> dict:
    packages = []
    header_row = None
    for row in matrix_sheet.iter_rows():
        code = row[0].value
        if code in ('WP 编号', 'WP ID'):
            header_row = row[0].row
        elif isinstance(code, str) and re.fullmatch(r'[A-Z]+-\d{2,}', code):
            packages.append((code, row[1].value, row[2].value, row[0].row))
    if not packages or header_row is None:
        raise ValueError('The matrix work-package identifiers and header were not found')
    prefixes = list(dict.fromkeys(code.split('-', 1)[0] for code, domain, name, row in packages))
    domains = list(dict.fromkeys(domain for code, domain, name, row in packages))
    missing_prefixes = set(prefixes) - PREFIX_DEFINITIONS.keys()
    missing_domains = set(domains) - DOMAIN_DEFINITIONS.keys()
    if missing_prefixes or missing_domains:
        raise ValueError(f'Undefined explanation vocabulary: {missing_prefixes}, {missing_domains}')
    populated = any(cell.value is not None for row in explanation_sheet for cell in row)
    start = explanation_sheet.max_row + 3 if populated else 1
    current = start
    border = Side(style='thin', color='D8DEDD')
    target_title = matrix_sheet.title.replace("'", "''")

    def band(row, left, right, value, heading=False):
        explanation_sheet.merge_cells(start_row=row, start_column=left, end_row=row, end_column=right)
        cell = explanation_sheet.cell(row, left, value)
        cell.font = Font(name='Microsoft YaHei', size=11, bold=heading, color='FFFFFF' if heading else '263B38')
        cell.fill = PatternFill('solid', fgColor='245B50' if heading else 'F2F7F4')
        cell.alignment = Alignment(vertical='top', wrap_text=True)
        for column in range(left, right + 1):
            explanation_sheet.cell(row, column).border = Border(left=border, right=border, top=border, bottom=border)
        return cell

    def paragraph(title, text, height=62):
        nonlocal current
        band(current, 1, 8, title, True)
        explanation_sheet.row_dimensions[current].height = 25
        current += 1
        band(current, 1, 8, text)
        explanation_sheet.row_dimensions[current].height = height
        current += 2

    paragraph('WP ID / Work Domain：编号、工作域及矩阵阅读说明',
              f'本页解释当前矩阵中的 {len(packages)} 个具体工作包、{len(prefixes)} 个编号前缀和 {len(domains)} 个工作域。它们是本分析采用的建议分类，不是 Confluence 官方编码、Octane ID、组织架构或已批准的人员 RACI。')
    paragraph('1. WP ID / 工作包编号：怎样读一个编号',
              'WP = Work Package（工作包）；ID = Identifier（标识符）。完整编号采用“前缀-两位序号”，例如 FREE-01。前缀是工作包族，后面的 01 是该族内的条目标识；完整编号唯一标识一个具体工作包。同一个工作域可以容纳多个前缀。编号不表示优先级、工时大小、执行先后、人员级别或产出权重；也不是工时科目或上游系统 ID。', 80)
    paragraph('2. Work Domain / 工作域：它与前缀、横轴有什么不同',
              'Work Domain 是按工作目的、内容及交付物归纳的上层分类，不是部门、岗位或责任人。前缀是该工作域内更细的工作包族，完整 WP ID 才定位某个具体包。横轴是原 Effort Breakdown Details 的工作类型/记账来源，与工作域不是同一个维度。例如“测试实施与探索”包含 ROAD、EXEC、FREE 三个族；一次路测可以执行脚本或承载探索，因此会出现跨列的条件性交叉。', 88)
    band(current, 1, 8, '3. 编号前缀词典：英文释义、用途、现有编号及所属工作域', True)
    explanation_sheet.row_dimensions[current].height = 26
    current += 1
    for left, right, title in ((1, 1, '前缀'), (2, 3, '英文释义'), (4, 6, '工作包族的具体含义'), (7, 7, '当前编号'), (8, 8, '所属工作域')):
        band(current, left, right, title, True)
    explanation_sheet.row_dimensions[current].height = 32
    current += 1
    for prefix in prefixes:
        english, meaning = PREFIX_DEFINITIONS[prefix]
        members = [(code, domain) for code, domain, name, row in packages if code.startswith(prefix + '-')]
        values = ((1, 1, prefix), (2, 3, english), (4, 6, meaning),
                  (7, 7, '\n'.join(code for code, domain in members)),
                  (8, 8, ' / '.join(dict.fromkeys(domain for code, domain in members))))
        for left, right, value in values:
            band(current, left, right, value)
        explanation_sheet.row_dimensions[current].height = max(66, len(members) * 17)
        current += 1
    current += 1
    band(current, 1, 8, '4. 工作域词典：包含范围、边界及当前对应的编号族', True)
    explanation_sheet.row_dimensions[current].height = 26
    current += 1
    for left, right, title in ((1, 2, 'Work Domain / 工作域'), (3, 5, '包含的工作与目的'), (6, 7, '不包含什么 / 主要边界'), (8, 8, '编号族')):
        band(current, left, right, title, True)
    explanation_sheet.row_dimensions[current].height = 32
    current += 1
    for domain in domains:
        scope, boundary = DOMAIN_DEFINITIONS[domain]
        members = list(dict.fromkeys(code.split('-', 1)[0] for code, work_domain, name, row in packages if work_domain == domain))
        for left, right, value in ((1, 2, domain), (3, 5, scope), (6, 7, boundary), (8, 8, ' / '.join(members))):
            band(current, left, right, value)
        explanation_sheet.row_dimensions[current].height = 92
        current += 1
    current += 1
    band(current, 1, 8, '5. 具体编号示例：从工作域到工作包，再看横轴交叉', True)
    explanation_sheet.row_dimensions[current].height = 26
    current += 1
    for example in ('CASE-02', 'FREE-01', 'ROAD-02', 'ENV-04', 'OPS-02', 'TOOL-03'):
        matches = [package for package in packages if package[0] == example]
        if not matches:
            continue
        code, domain, name, matrix_row = matches[0]
        cell = band(current, 1, 2, code)
        cell.hyperlink = Hyperlink(ref=cell.coordinate, location=f"'{target_title}'!A{matrix_row}", display=code)
        cell.font = Font(name='Microsoft YaHei', size=11, color='0563C1', underline='single', bold=True)
        band(current, 3, 8, f'{domain} → {code.split("-", 1)[0]} → {code}：{name}。点击编号返回矩阵该行，再查看哪些原工作类型参与、参与动作和交付物。')
        explanation_sheet.row_dimensions[current].height = 58
        current += 1
    current += 1
    paragraph('6. 怎样理解同色、主项、协同、条件及空白',
              '同一行的非空交叉格同色，表示同一个工作包可关联多个原工作类型；不同工作包不同色。主项=建议在该工作类型下实际开展的核心动作；协同=为同一交付物提供输入、核验或接口；条件=只有格内条件成立时才适用；空白=本次未建议关联，不代表永远禁止。颜色不代表部门、优先级、工时比例或已发生的重复工作。', 88)
    paragraph('7. 使用与计量边界',
              '使用时先按“工作域”定位大类，再读“具体工作包”和“交付物”，最后查看横轴的适用关系。同一场景可以带多个方法标签，但同一分钟只能记一个主工作包。WP ID 不用于从 ticket 数直接推算工时；信号数量、候选数量、执行事件、已审核有效缺陷及工时是不同口径。未来新包应追加新序号，已有编号不因排序、筛选或显示顺序改变而重新编号。', 88)
    if explanation_sheet['A1'].value is None:
        cell = explanation_sheet['A1']
        cell.value = 'WP ID / Work Domain 详细说明（点击跳转）'
        notes_title = explanation_sheet.title.replace("'", "''")
        cell.hyperlink = Hyperlink(ref='A1', location=f"'{notes_title}'!A{start}", display=cell.value)
        cell.font = Font(name='Microsoft YaHei', size=12, bold=True, color='0563C1', underline='single')
    for column, width in ((1, 15), (2, 20), (3, 24), (4, 24), (5, 24), (6, 24), (7, 24), (8, 27)):
        dimension = explanation_sheet.column_dimensions[get_column_letter(column)]
        dimension.width = max(dimension.width or 0, width)
    return {'packageCount': len(packages), 'prefixCount': len(prefixes), 'domainCount': len(domains),
            'matrixHeaderRow': header_row, 'explanationStartRow': start,
            'explanationLastRow': current - 1, 'sheet': explanation_sheet.title}


def apply_reference_workbook_style(workbook, reference_workbook) -> dict:
    samples = [cell for sheet in reference_workbook for row in sheet.iter_rows(
        max_row=min(sheet.max_row, 200), max_col=min(sheet.max_column, 80))
        for cell in row if cell.value is not None]

    def white_font(cell):
        color = cell.font.color
        return color is not None and color.type == 'rgb' and color.rgb[-6:].upper() == 'FFFFFF'

    def dominant(cells):
        if not cells:
            raise ValueError('Reference workbook lacks a required header or body style')
        style_id = Counter(cell.style_id for cell in cells).most_common(1)[0][0]
        return next(cell for cell in cells if cell.style_id == style_id)

    body = dominant([cell for cell in samples if not cell.font.b and not cell.hyperlink])
    header = dominant([cell for cell in samples if cell.font.b and white_font(cell)
                       and cell.fill.patternType == 'solid'])
    titles = [cell for cell in samples if cell.font.b and not white_font(cell)
              and (cell.font.sz or 0) > (body.font.sz or 0)]
    title = max(titles, key=lambda cell: cell.font.sz) if titles else header
    links = [cell for cell in samples if cell.hyperlink]
    link_font = copy(dominant(links).font) if links else copy(body.font)
    if not links:
        link_font.color = 'FF0563C1'
    link_font.underline = 'single'
    changed = 0
    for sheet in workbook:
        package_rows = {row[0].row for row in sheet if isinstance(row[0].value, str)
                        and re.fullmatch(r'[A-Z]+-\d{2,}', row[0].value)}
        merged_widths = {merged.start_cell.coordinate: (merged.min_col, merged.max_col)
                         for merged in sheet.merged_cells.ranges}
        row_heights = {}
        for row in sheet:
            for cell in row:
                if cell.value is None and not cell.has_style:
                    continue
                is_header = cell.font.b and white_font(cell) and cell.fill.patternType == 'solid'
                is_title = cell.coordinate == 'A1' and cell.value is not None and not cell.hyperlink
                sample = title if is_title else header if is_header else body
                old_bold = cell.font.b
                font = copy(link_font if cell.hyperlink else sample.font)
                if not is_header and not is_title:
                    font.b = old_bold
                cell.font = font
                if cell.row not in package_rows:
                    cell.fill = copy(sample.fill)
                cell.border = copy(sample.border)
                alignment = copy(sample.alignment)
                alignment.wrap_text = True
                cell.alignment = alignment
                if cell.value is None:
                    continue
                left, right = merged_widths.get(cell.coordinate, (cell.column, cell.column))
                width = sum((sheet.column_dimensions.get(get_column_letter(column)).width
                             if sheet.column_dimensions.get(get_column_letter(column)) is not None else None)
                            or sheet.sheet_format.defaultColWidth or 13
                            for column in range(left, right + 1))
                capacity = max(3, (width - 1.5) * 11 / (cell.font.sz or 10))
                lines = sum(max(1, ceil(sum(2 if east_asian_width(character) in 'WF' else 1
                                          for character in line) / capacity))
                            for line in str(cell.value).split('\n'))
                needed = min(409.5, lines * (cell.font.sz or 10) * 1.4 + 6)
                row_heights[cell.row] = max(row_heights.get(cell.row, 0), needed)
                changed += 1
        for row, height in row_heights.items():
            current = sheet.row_dimensions[row].height or sheet.sheet_format.defaultRowHeight or 15
            sheet.row_dimensions[row].height = max(current, height)
    return {'bodyFont': body.font.name, 'bodySize': body.font.sz,
            'headerFont': header.font.name, 'headerSize': header.font.sz,
            'headerFill': header.fill.fgColor.rgb if header.fill.fgColor.type == 'rgb' else header.fill.fgColor.value,
            'titleSize': title.font.sz, 'styledPopulatedCells': changed}


def add_workbook_navigation(workbook, evidence_catalog: str | None = None) -> dict:
    guide_name = '阅读向导'
    if guide_name in workbook.sheetnames:
        raise ValueError('Workbook navigation already exists; refusing to overwrite it')
    matrix = None
    header_row = None
    for sheet in workbook:
        for row in sheet:
            if row[0].value in ('WP 编号', 'WP ID'):
                matrix, header_row = sheet, row[0].row
                break
        if matrix is not None:
            break
    if matrix is None or 'Sheet1' not in workbook.sheetnames:
        raise ValueError('The work-package matrix and Sheet1 explanations are required')
    notes = workbook['Sheet1']
    packages = [(row[0].value, row[1].value, row[0].row) for row in matrix
                if isinstance(row[0].value, str) and re.fullmatch(r'[A-Z]+-\d{2,}', row[0].value)]
    if not packages:
        raise ValueError('No work-package navigation anchors found')
    meta_columns = {re.sub(r'\s+', '', str(cell.value)): cell.column for cell in matrix[header_row]
                    if cell.value is not None}
    source_column = meta_columns.get('Confluence/来源依据')
    if source_column is None:
        raise ValueError('The matrix source/evidence region was not found')

    def section(prefix):
        for row in notes:
            if any(isinstance(cell.value, str) and cell.value.strip().startswith(prefix) for cell in row):
                return next(cell.coordinate for cell in row if isinstance(cell.value, str) and cell.value.strip().startswith(prefix))
        raise ValueError(f'Explanation navigation section missing: {prefix}')

    def location(sheet, address):
        return "'" + sheet.title.replace("'", "''") + "'!" + address

    explanation = location(notes, section('WP ID / Work Domain：'))
    wp_explanation = location(notes, section('1. WP ID'))
    domain_explanation = location(notes, section('2. Work Domain'))
    prefix_glossary = location(notes, section('3. 编号前缀'))
    color_explanation = location(notes, section('6. 怎样理解同色'))
    boundary_explanation = location(notes, section('7. 使用与计量边界'))
    domain_rows = {}
    code_rows = {}
    for code, domain, row in packages:
        domain_rows.setdefault(domain, row)
        code_rows[code] = row
    source_urls = {}
    for sheet in workbook:
        for row in sheet:
            for cell in row:
                texts = [cell.comment.text] if cell.comment else []
                if cell.hyperlink and cell.hyperlink.target:
                    texts.append(cell.hyperlink.target)
                for text in texts:
                    for url in re.findall(r'https://atc\.bmwgroup\.net/confluence/[^\s<>"\)]+', text):
                        for page_id in STRATEGY_VERSIONS:
                            if page_id in url:
                                source_urls.setdefault(page_id, url.rstrip('.,;'))
    guide = workbook.create_sheet(guide_name, 0)
    guide.sheet_view.showGridLines = False
    guide.sheet_view.zoomScale = 85
    guide.sheet_view.topLeftCell = 'A1'
    guide.sheet_view.selection = [Selection(activeCell='A1', sqref='A1')]
    line = Side(style='hair', color='D9E2F3')

    def tile(row, left, right, text, target=None, external=False, heading=False):
        guide.merge_cells(start_row=row, start_column=left, end_row=row, end_column=right)
        cell = guide.cell(row, left, text)
        cell.fill = PatternFill('solid', fgColor='1F4E78' if heading else 'E8F0F8' if target else 'FFFFFF')
        cell.font = Font(name='Calibri', size=10, bold=heading or bool(target), color='FFFFFF' if heading else '0563C1' if target else '172B3A', underline='single' if target else None)
        cell.alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
        cell.border = Border(left=line, right=line, top=line, bottom=line)
        if target:
            cell.hyperlink = Hyperlink(ref=cell.coordinate, target=target if external else None,
                                       location=None if external else target, tooltip='打开引用来源' if external else '跳转到对应区域', display=text)
        return cell

    tile(1, 1, 12, 'DTSV Work Breakdown | 阅读向导', heading=True).font = Font(name='Calibri', size=16, bold=True, color='FFFFFF')
    tile(2, 1, 12, f'{len(packages)} 个具体工作包 × {source_column - 5} 类原工作。先定位区域，再理解坐标，最后查看关联动作和证据；不改变原矩阵分类或工时口径。')
    tile(3, 1, 12, '第一层：选择入口', heading=True)
    tile(4, 1, 4, '矩阵总览\n查看全部工作包与原工作类型', location(matrix, f'A{header_row}'))
    tile(4, 5, 8, '编号与工作域说明\nWP ID、Domain、边界与阅读示例', explanation)
    tile(4, 9, 12, '来源与数据依据\n策略引用、证据缺口与计量边界', location(matrix, f'{get_column_letter(source_column)}{header_row}'))
    tile(5, 1, 12, '第二层：矩阵的左、中、右区域分别代表什么', heading=True)
    tile(6, 1, 4, '左侧 A:D｜工作包坐标\n编号、工作域、具体工作包与交付物；回答“做什么、产出什么”。', location(matrix, f'A{header_row}'))
    tile(6, 5, 8, f'中间 E:{get_column_letter(source_column - 1)}｜适用关系\n横轴是原工作类型；非空格写具体动作，同一行同色表示同一工作包的交叉。', location(matrix, f'E{header_row}'))
    tile(6, 9, 12, f'右侧 {get_column_letter(source_column)}:{get_column_letter(matrix.max_column)}｜证据与限制\n查看引用、数据支持、未量化部分和不重复计时边界。', location(matrix, f'{get_column_letter(source_column)}{header_row}'))
    tile(7, 1, 12, '第三层：按工作域定位（点击跳到该域第一个工作包）', heading=True)
    for index, (domain, row) in enumerate(domain_rows.items()):
        left = 1 + (index % 3) * 4
        tile(8 + index // 3, left, left + 3, domain + '\n从 ' + matrix.cell(row, 1).value + ' 开始', location(matrix, f'A{row}'))
    source_heading = 8 + (len(domain_rows) + 2) // 3
    tile(source_heading, 1, 12, '引用入口：沿用当前矩阵已保留的 Confluence 来源', heading=True)
    for index, (page_id, label) in enumerate((('4447601857', 'DTSV 主策略'), ('8681681592', 'CW34｜输入方式 / HiCar'), ('8735186775', 'CW36｜启动 / 屏幕 / 导航恢复'))):
        left = 1 + index * 4
        tile(source_heading + 1, left, left + 3, label + '\n' + ('打开原始页面' if page_id in source_urls else '当前矩阵未保留该来源链接'), source_urls.get(page_id), external=True)
    tile(source_heading + 2, 1, 12, '来源链接来自当前矩阵的引用和批注。本次只增加阅读导航，没有同步 Octane、更新策略版本或将方法信号换算为工时；文档效力以来源状态为准。')
    tile(source_heading + 3, 1, 4, 'WP 编号规则\n前缀与序号分别表示什么', wp_explanation)
    tile(source_heading + 3, 5, 8, '工作域范围\n与前缀、原工作类型的区别', domain_explanation)
    tile(source_heading + 3, 9, 12, '编号前缀词典\nREQ / ENV / FREE 等释义', prefix_glossary)
    tile(source_heading + 4, 1, 4, '同色与交叉规则\n空白、条件适用与重复计时', color_explanation)
    tile(source_heading + 4, 5, 8, '计量与使用边界\n信号、候选、执行与工时', boundary_explanation)
    tile(source_heading + 4, 9, 12, '实际步骤 / 案例目录\n打开原分析证据', evidence_catalog, external=True) if evidence_catalog else tile(source_heading + 4, 9, 12, '实际步骤 / 案例依据\n在矩阵右侧查看数据和引用', location(matrix, f'{get_column_letter(source_column + 1)}{header_row}'))
    tile(source_heading + 5, 1, 12, '返回：点击矩阵顶部标题，或说明页的“返回阅读向导”。有色格表示建议关联，不是重复工时；若显示主项/协同/条件标签，按格内文字解释，未显示时不要自行推定角色。')
    for row in range(1, source_heading + 6):
        guide.row_dimensions[row].height = 25 if row in (3, 5, 7, source_heading) else 54 if row in (4, 6, source_heading + 1) else 38
    guide.row_dimensions[1].height = 34
    for column in range(1, 13):
        guide.column_dimensions[get_column_letter(column)].width = 13
    guide.page_setup.orientation = 'landscape'
    guide.page_setup.paperSize = guide.PAPERSIZE_A3
    guide.page_setup.fitToWidth = 1
    guide.page_setup.fitToHeight = 1
    guide.sheet_properties.pageSetUpPr.fitToPage = True
    guide.print_area = f'A1:L{source_heading + 5}'
    home = location(guide, 'A1')
    matrix['A1'].hyperlink = Hyperlink(ref='A1', location=home, tooltip='返回阅读向导', display=str(matrix['A1'].value))
    repaired = 0
    for row in notes:
        for cell in row:
            if cell.hyperlink and cell.value in code_rows:
                link = copy(cell.hyperlink)
                target = location(matrix, f'A{code_rows[cell.value]}')
                repaired += link.location != target
                link.location = target
                cell.hyperlink = link
    if notes['A1'].hyperlink:
        link = copy(notes['A1'].hyperlink)
        repaired += link.location != explanation
        link.location = explanation
        notes['A1'].hyperlink = link
    return_row = 2 if all(cell.value is None for cell in notes[2]) and not any(merged.min_row <= 2 <= merged.max_row for merged in notes.merged_cells.ranges) else notes.max_row + 2
    notes.merge_cells(start_row=return_row, start_column=1, end_row=return_row, end_column=8)
    cell = notes.cell(return_row, 1, '返回阅读向导｜区域入口、引用与工作域导航')
    cell.hyperlink = Hyperlink(ref=cell.coordinate, location=home, display=cell.value)
    cell.font = Font(name='Calibri', size=10, bold=True, color='FFFFFF', underline='single')
    cell.fill = PatternFill('solid', fgColor='1F4E78')
    cell.alignment = Alignment(vertical='center', wrap_text=True)
    notes.row_dimensions[return_row].height = 24
    workbook.active = 0
    return {'guideSheet': guide_name, 'packageCount': len(packages), 'domainCount': len(domain_rows),
            'workTypeCount': source_column - 5, 'matrixHeaderRow': header_row,
            'explanationTarget': explanation, 'repairedExistingLinks': repaired,
            'confluenceReferences': len(source_urls), 'guideLastRow': source_heading + 5}


def build_work_package_matrix(source_path, evidence_path, strategy_paths, output_path) -> dict:
    source_path, evidence_path, output_path = Path(source_path), Path(evidence_path), Path(output_path)
    strategy_paths = [Path(path) for path in strategy_paths]
    inputs = {path.resolve() for path in (source_path, evidence_path, *strategy_paths)}
    if output_path.resolve() in inputs:
        raise ValueError('Refusing to overwrite an input file')
    if output_path.exists():
        raise FileExistsError(f'Output already exists: {output_path}')
    source_hash = sha256(source_path.read_bytes()).hexdigest()
    data = json.loads(evidence_path.read_text(encoding='utf-8'))
    pages = _strategy_pages(strategy_paths)
    main_status = 'Drating' if next(page for page in pages if page['id'] == '4447601857')['drafting'] else '正文状态未标定'
    original = load_workbook(source_path, data_only=False)
    columns = _work_columns(original[SOURCE_SHEET])
    original.close()
    references = {reference: column for column in columns for reference in column['sourceRefs']}
    missing = {reference for package in PACKAGES for reference in package.actions} - references.keys()
    if missing:
        raise ValueError(f'Source work headers missing: {sorted(missing)}')
    if len({package.code for package in PACKAGES}) != len(PACKAGES):
        raise ValueError('Duplicate work-package identifiers')
    unsupported = set(references) - {reference for package in PACKAGES for reference in package.actions}
    uncovered = [column['label'] for column in columns if set(column['sourceRefs']) <= unsupported]
    if uncovered:
        raise ValueError(f'Work types without decomposition: {uncovered}')
    evidence = _evidence(data)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = '工作包交叉矩阵'
    sheet.freeze_panes = 'E8'
    sheet.sheet_view.showGridLines = False
    sheet.sheet_view.zoomScale = 70
    first_meta = 5 + len(columns)
    last_column = first_meta + 3
    last_letter = get_column_letter(last_column)
    for row, text in (
        (1, 'DTSV 全工作类型 × 具体工作包交叉矩阵'),
        (2, f'原表：{source_path.name} / {SOURCE_SHEET}；19 类人员工作 + 23 类管理工作，名称完全相同者合并，其余职责保留。'),
        (3, '同一 WP 的非空交叉格同色；不同 WP 使用不同色。主项/协同/条件为建议适用关系，不是人员 RACI。空白=本次未建议关联；可多对多，但同一分钟只能记一个主包。'),
        (4, f'依据：ATC 主策略与 CW34/CW36；主策略正文 {main_status}。数字为原表规划份额或本地历史证据信号，不是工时、有效缺陷率或分配权重。缺乏数据的活动明确标注未量化。'),
    ):
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=last_column)
        cell = sheet.cell(row, 1, text)
        cell.font = Font(name='Microsoft YaHei', size=16 if row == 1 else 11, bold=row == 1, color='173D35')
        cell.alignment = Alignment(vertical='center', wrap_text=True)
        sheet.row_dimensions[row].height = 30
    sheet['A2'].comment = Comment(f'Source SHA256: {source_hash}\nNo original workbook content was changed.', 'DTSV evidence')
    source_note = '\n'.join(f"{page['title']} v{page['version']} {page['when']}\n{page['url']}" for page in pages)
    source_note += f'\n\nLocal ticket evidence: {evidence_path}\nEvidence SHA256: {sha256(evidence_path.read_bytes()).hexdigest()}\nNo upstream synchronization or new effort measurement was performed.'
    sheet['A4'].comment = Comment(source_note, 'ATC evidence')
    for row, title in ((5, '原表表头位置'), (6, '原表份额合计：规划值，非工时')):
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
        sheet.cell(row, 1, title)
        sheet.row_dimensions[row].height = 30 if row == 5 else 36
    headers = ['WP 编号', '工作域', '具体工作包', '交付物 / 可审查产出']
    headers.extend(column['label'] for column in columns)
    headers.extend(['Confluence / 来源依据', '数据证据与缺口', '交叉边界 / 不重复计时', '计量单位（非工时）'])
    for column, title in enumerate(headers, 1):
        cell = sheet.cell(7, column, _literal(title))
        cell.fill = PatternFill('solid', fgColor='245B50')
        cell.font = Font(name='Microsoft YaHei', size=10, bold=True, color='FFFFFF')
        cell.alignment = Alignment(vertical='center', horizontal='center', wrap_text=True)
    sheet.row_dimensions[7].height = 105
    for column in columns:
        position = column['column']
        sheet.cell(5, position, ' / '.join(column['sourceRefs']))
        sheet.cell(6, position, '\n'.join(column['allocations']))
        note = f"Source: {SOURCE_SHEET}!{' / '.join(column['sourceRefs'])}\nOriginal heading: {column['label']}\nIdentical headings share a column; review, approval and execution remain different work types.\nPlanning allocation sums are not measured hours or verified personnel FTE."
        sheet.cell(7, position).comment = Comment(note, 'Source workbook')
    line = Side(style='hair', color='D8DEDD')
    shared_count = 0
    for index, package in enumerate(PACKAGES):
        row = 8 + index
        mapped = {}
        for reference, action in package.actions.items():
            position = references[reference]['column']
            mapped.setdefault(position, [])
            if action not in mapped[position]:
                mapped[position].append(action)
        shared = len(mapped) > 1
        shared_count += shared
        color = _overlap_color(index) if shared else 'F2F4F3'
        for position, value in enumerate((package.code, package.domain, package.name, package.output), 1):
            sheet.cell(row, position, value)
            sheet.cell(row, position).fill = PatternFill('solid', fgColor=color)
        strategy_note = _strategy_note(package, pages)
        sheet.cell(row, 3).comment = Comment(strategy_note, 'ATC strategy')
        for position, actions in mapped.items():
            cell = sheet.cell(row, position, '\n'.join(actions))
            cell.fill = PatternFill('solid', fgColor=color)
            cell.comment = Comment(f'Proposed functional applicability, not approved ownership or measured effort.\n{package.code}: {package.name}\nOutput: {package.output}\n{package.boundary}\n\n{strategy_note}', 'Work-package mapping')
        for position, value in enumerate((package.clauses, evidence[package.evidence], package.boundary, package.unit), first_meta):
            sheet.cell(row, position, value)
        if re.search(r'\d', package.clauses):
            sheet.cell(row, first_meta).hyperlink = next(page['url'] for page in pages if page['id'] == '4447601857')
        sheet.row_dimensions[row].height = 88
    for row in sheet.iter_rows(min_row=5):
        for cell in row:
            if cell.row != 7:
                cell.font = Font(name='Microsoft YaHei', size=10, bold=cell.column == 1, color='263B38')
                cell.alignment = Alignment(vertical='top', wrap_text=True)
            cell.border = Border(left=line, right=line, top=line, bottom=line)
    for column, width in ((1, 13), (2, 19), (3, 37), (4, 42)):
        sheet.column_dimensions[get_column_letter(column)].width = width
    for column in columns:
        sheet.column_dimensions[get_column_letter(column['column'])].width = 20
    for offset, width in enumerate((28, 64, 61, 24), first_meta):
        sheet.column_dimensions[get_column_letter(offset)].width = width
    sheet.auto_filter.ref = f'A7:{last_letter}{sheet.max_row}'
    sheet.print_title_rows = '1:7'
    sheet.print_title_cols = 'A:D'
    sheet.print_area = f'A1:{last_letter}{sheet.max_row}'
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)
    workbook.close()
    if sha256(source_path.read_bytes()).hexdigest() != source_hash:
        raise RuntimeError('Source workbook changed while the matrix was generated')
    return {'outputPath': str(output_path), 'sourceSha256': source_hash,
            'workTypeCount': len(columns), 'sourceHeaderCount': len(references),
            'packageCount': len(PACKAGES), 'sharedPackageCount': shared_count,
            'headerRow': 7, 'firstPackageRow': 8, 'workColumns': columns,
            'strategyVersions': {page['id']: page['version'] for page in pages}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--evidence', required=True)
    parser.add_argument('--strategy-page', action='append', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = build_work_package_matrix(args.source, args.evidence, args.strategy_page, args.output)
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
