import html
from datetime import datetime, timezone

from .report_service import render_html_pdf


BOUNDARY = "本记录用于汇总施工区域水害识别、辅助研判及现场核查信息，不构成工程机械安全通行许可、停复工审批或施工安全鉴定。最终作业决策应由项目现场管理人员依据实际工况和相关安全制度作出。"


def _h(value): return html.escape(str(value if value not in (None, "") else "--"))


def assistance_report_html(data: dict) -> str:
    evidence = data.get("evidence") or {}
    evidence_rows = "".join(f"<tr><th>{_h(key)}</th><td><pre>{_h(value)}</pre></td></tr>" for key,value in evidence.items()) or "<tr><td colspan='2'>暂无相关数据</td></tr>"
    checklist = "".join(f"<li>□ {_h(item)}</li>" for item in data.get("inspection_checklist",[]))
    checks = "".join(f"<tr><td>{_h(x.get('verified_at'))}</td><td>{_h(x.get('verification_result_label'))}</td><td>{_h(x.get('finding_text'))}</td><td>{_h(x.get('action_note'))}</td></tr>" for x in data.get("verifications",[])) or "<tr><td colspan='4'>暂无人工核查记录</td></tr>"
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>施工水害作业辅助与现场核查记录</title><style>
    body{{font-family:'Microsoft YaHei',sans-serif;color:#24333d;margin:30px;line-height:1.65}}h1{{font-size:24px}}h2{{font-size:17px;border-bottom:1px solid #ccd7dd;padding-bottom:6px}}table{{border-collapse:collapse;width:100%;margin:10px 0}}th,td{{border:1px solid #cbd5dc;padding:7px;text-align:left;vertical-align:top}}th{{background:#f1f5f7;width:22%}}pre{{white-space:pre-wrap;margin:0;font:12px sans-serif}}.note{{background:#f6f8f9;padding:12px;border-left:4px solid #557a8b}}li{{list-style:none;margin:5px 0}}</style></head><body>
    <h1>施工水害作业辅助与现场核查记录</h1><p>生成时间：{_h(datetime.now(timezone.utc).isoformat())}</p>
    <h2>事项基本信息</h2><table><tr><th>事项ID</th><td>{_h(data.get('id'))}</td></tr><tr><th>项目</th><td>{_h(data.get('project_name') or '未关联项目')}</td></tr><tr><th>事项</th><td>{_h(data.get('title'))}</td></tr><tr><th>区域 / 设备</th><td>{_h(data.get('work_area_name'))} / {_h(data.get('equipment_name'))}</td></tr><tr><th>类型 / 优先级 / 状态</th><td>{_h(data.get('assistance_type_label'))} / {_h(data.get('priority_label'))} / {_h(data.get('status_label'))}</td></tr><tr><th>来源 / 规则版本</th><td>{_h(data.get('source_label'))} / {_h(data.get('rule_version'))}</td></tr></table>
    <h2>生成依据与多源证据</h2><p>{_h(data.get('source_summary'))}</p><table>{evidence_rows}</table>
    <h2>系统辅助建议</h2><p>{_h(data.get('recommendation_text'))}</p>
    <h2>现场核查清单</h2><ul>{checklist}</ul>
    <h2>人工核查结果</h2><table><tr><th>核查时间</th><th>核查结果</th><th>现场发现</th><th>后续记录</th></tr>{checks}</table>
    <h2>闭环记录</h2><p>闭环时间：{_h(data.get('closed_at'))}<br>闭环说明：{_h(data.get('closed_by_note'))}</p>
    <p class="note">{BOUNDARY}</p></body></html>"""


def assistance_report_pdf(data: dict) -> bytes:
    return render_html_pdf(assistance_report_html(data))
