from __future__ import annotations

import base64
import html
import json
from pathlib import Path

from ..db.models import RiskAssessment
from .report_service import render_html_pdf
from .risk_service import BOUNDARY_NOTE


def _pct(value): return "--" if value is None else f"{value * 100:.2f}%"
def _measure(value, unit): return "--" if value is None else f"{float(value):.2f}{unit}"


def risk_report_html(row: RiskAssessment, impacts: list[dict] | None = None, project_name: str | None = None,
                     equipment_links: list[dict] | None = None, terrain: dict | None = None,
                     rainfall: dict | None = None, uav: dict | None = None) -> str:
    reasons = json.loads(row.risk_reason_text or "[]")
    advice = json.loads(row.inspection_advice or "[]")
    image = ""
    if row.risk_png_path and Path(row.risk_png_path).is_file():
        encoded = base64.b64encode(Path(row.risk_png_path).read_bytes()).decode("ascii")
        image = f'<img src="data:image/png;base64,{encoded}" alt="水害关注区域图">'
    impact_section = ""
    if impacts:
        rows = "".join(f"<tr><td>{html.escape(str(x.get('area_name') or '--'))}</td><td>{html.escape(str(x.get('area_type_label') or '--'))}</td><td>{_pct(x.get('high_attention_ratio'))}</td><td>{_pct(x.get('inspection_ratio'))}</td><td>{_pct(x.get('general_ratio'))}</td><td>{html.escape(str(x.get('impact_level') or '--'))}</td><td>{html.escape(str(x.get('advice') or '--'))}</td></tr>" for x in impacts)
        impact_section = f"<h2>施工区域影响分析</h2><p>施工项目：{html.escape(project_name or '--')}</p><table><tr><th>区域名称</th><th>区域类型</th><th>高关注比例</th><th>重点巡检比例</th><th>一般区域比例</th><th>影响等级</th><th>辅助建议</th></tr>{rows}</table>"
    equipment_section = ""
    if equipment_links:
        rows = "".join(f"<tr><td>{html.escape(str(x.get('equipment_code') or '--'))}</td><td>{html.escape(str(x.get('equipment_name') or '--'))}</td><td>{html.escape(str(x.get('equipment_type_label') or '--'))}</td><td>{html.escape(str(x.get('recorded_at') or '--'))}</td><td>{html.escape(str(x.get('primary_work_area_name') or '未命中所属项目区域'))}</td><td>{html.escape(str(x.get('risk_label') or '--'))}</td><td>{html.escape(str(x.get('work_area_impact_level') or '--'))}</td><td>{'位置已过期' if x.get('position_status') == 'stale' else '位置有效'}</td><td>{html.escape(str(x.get('advice_text') or '--'))}</td></tr>" for x in equipment_links)
        equipment_section = f"<h2>工程机械水害关联</h2><table><tr><th>设备编号</th><th>设备名称</th><th>设备类型</th><th>位置时间</th><th>所在施工区域</th><th>当前位置水害类别</th><th>区域影响等级</th><th>位置时效</th><th>辅助建议</th></tr>{rows}</table>"
    terrain_section = ""
    if terrain:
        fusion = terrain.get("fusion") or {}; zones = [("高水害关注区域",fusion.get("high_attention") or {}),("重点巡检区域",fusion.get("inspection") or {}),("一般区域",fusion.get("general") or {})]
        zone_rows = "".join(f"<tr><td>{label}</td><td>{_measure(values.get('mean_elevation'),' m')}</td><td>{_measure(values.get('mean_slope'),'°')}</td><td>{_pct(values.get('low_elevation_ratio'))}</td></tr>" for label,values in zones)
        profile_rows = "".join(f"<tr><td>{html.escape(str(x.get('area_name') or '--'))}</td><td>{x.get('mean_elevation',0):.2f} m</td><td>{x.get('mean_slope',0):.2f}°</td><td>{_pct(x.get('low_elevation_ratio'))}</td></tr>" for x in terrain.get("profiles",[]) if x.get("mean_elevation") is not None)
        equipment_rows = "".join(f"<tr><td>{html.escape(str(x.get('equipment_name') or '--'))}</td><td>{_measure(x.get('elevation'),' m')}</td><td>{_measure(x.get('slope'),'°')}</td><td>{'是' if x.get('is_low_elevation') else '否' if x.get('is_low_elevation') is False else '--'}</td></tr>" for x in terrain.get("equipment",[]))
        terrain_section = f"<h2>地形辅助分析</h2><p>DEM：{html.escape(str(terrain.get('dem_name') or '--'))}；来源：{html.escape(str(terrain.get('source_name') or '未填写'))}；规则版本：{html.escape(str(fusion.get('algorithm_version') or 'terrain-analysis-v1'))}</p><table><tr><th>水害区域</th><th>平均高程</th><th>平均坡度</th><th>低高程重叠比例</th></tr>{zone_rows}</table>{('<h3>施工区域地形指标</h3><table><tr><th>区域</th><th>平均高程</th><th>平均坡度</th><th>低高程比例</th></tr>'+profile_rows+'</table>') if profile_rows else ''}{('<h3>设备位置地形快照</h3><table><tr><th>设备</th><th>高程</th><th>坡度</th><th>相对低高程</th></tr>'+equipment_rows+'</table>') if equipment_rows else ''}<p class='note'>地形分析结果用于辅助解释水害空间分布，不直接表示积水深度、地基承载能力、边坡稳定性或工程机械通行安全性。</p>"
    rainfall_section = ""
    if rainfall:
        sources="、".join(x for x in rainfall.get("source_names",[]) if x) or "未填写"
        total="--" if rainfall.get("total_rainfall_mm") is None else f"{float(rainfall['total_rainfall_mm']):.2f} mm"
        peak="--" if rainfall.get("peak_rainfall_rate_mm_h") is None else f"{float(rainfall['peak_rainfall_rate_mm_h']):.2f} mm/h"
        rainfall_section=f"<h2>降雨背景分析</h2><p>数据来源：{html.escape(sources)}；雨量站：{rainfall.get('station_count',0)}；规则版本：{html.escape(str(rainfall.get('rule_version') or 'rainfall-event-v1'))}</p><table><tr><th>分析时间范围</th><th>累计降雨</th><th>降雨事件</th><th>峰值时段平均雨强</th><th>数据完整状态</th></tr><tr><td>{html.escape(str(rainfall.get('window_start') or '--'))} 至 {html.escape(str(rainfall.get('window_end') or '--'))}</td><td>{total}</td><td>{rainfall.get('rainfall_event_count',0)}</td><td>{peak}</td><td>{html.escape(str(rainfall.get('coverage_label') or rainfall.get('coverage_status') or '--'))}</td></tr></table><p>时间序列摘要：共 {rainfall.get('observation_count',0)} 条观测；积水变化时段与降雨记录仅作同期背景关联。</p><p class='note'>降雨数据用于辅助说明水害分析时段内的环境背景。降雨与积水变化之间的时间关联不等同于已验证的因果关系，本系统当前不进行降雨径流、水深或洪水动力学预测。</p>"
    uav_section = ""
    if uav and (uav.get("overlays") or uav.get("verifications")):
        overlay_rows = "".join(f"<tr><td>{html.escape(str(x.get('uav_name') or '--'))}</td><td>{html.escape(str(x.get('uav_capture_time') or '采集时间未填写'))}</td><td>{_pct(x.get('high_attention_coverage_ratio'))}</td><td>{_pct(x.get('inspection_coverage_ratio'))}</td><td>{html.escape(str(x.get('time_difference_hours') if x.get('time_difference_hours') is not None else '时间参考受限'))}</td><td>{html.escape(str(x.get('analysis_version') or 'uav-spatial-v1'))}</td></tr>" for x in uav.get("overlays", []))
        verification_rows = "".join(f"<tr><td>{html.escape(str(x.get('work_area_name') or '未指定区域'))}</td><td>{html.escape(str(x.get('uav_name') or '--'))}</td><td>{html.escape(str(x.get('verification_status_label') or '--'))}</td><td>{html.escape(str(x.get('observation_type_label') or '--'))}</td><td>{html.escape(str(x.get('finding_text') or '未填写'))}</td><td>{html.escape(str(x.get('recommendation_text') or '未填写'))}</td></tr>" for x in uav.get("verifications", []))
        crop_html = ""
        for item in uav.get("verifications", []):
            path = item.get("image_crop_path")
            if path and Path(path).is_file():
                encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
                crop_html += f'<img src="data:image/png;base64,{encoded}" alt="无人机局部复核图">'
                break
        uav_section = f"<h2>无人机精细复核</h2>{('<table><tr><th>影像名称</th><th>采集时间</th><th>高关注覆盖率</th><th>重点巡检覆盖率</th><th>与研判时间差（小时）</th><th>算法版本</th></tr>'+overlay_rows+'</table>') if overlay_rows else ''}{crop_html}{('<h3>人工复核记录</h3><table><tr><th>施工区域</th><th>无人机影像</th><th>复核状态</th><th>人工观察</th><th>复核说明</th><th>辅助建议</th></tr>'+verification_rows+'</table>') if verification_rows else ''}<p class='note'>无人机正射影像在本系统中用于施工区域局部精细查看和人工复核。当前版本不使用无人机影像自动生成正式水害等级，人工观察结果也不替代现场安全检查和施工决策。</p>"
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>施工区域水害风险辅助研判报告</title><style>@page{{size:A4;margin:18mm}}body{{font-family:'Microsoft YaHei',sans-serif;color:#253746;line-height:1.65}}h1{{color:#173a52}}.meta,.level{{background:#f3f7f9;padding:12px}}.level{{font-size:22px;color:#b33b3b}}table{{width:100%;border-collapse:collapse}}td,th{{border:1px solid #d8e2e8;padding:8px}}img{{width:100%;max-height:500px;object-fit:contain}}.note{{font-size:12px;color:#687985}}</style></head><body>
<div>工程水害管理系统 · 基础水害研判</div><h1>施工区域水害风险辅助研判报告</h1><div class="meta">研判名称：{html.escape(row.assessment_name)}<br>研判编号：{row.id}<br>来源变化分析：{row.source_change_analysis_id}<br>T1：{html.escape(row.source_t1_name)}<br>T2：{html.escape(row.source_t2_name)}<br>规则版本：{html.escape(row.rule_version)}<br>研判时间：{row.completed_at or '--'}</div>
<h2>基础水害关注等级</h2><div class="level">{row.attention_level}</div><h2>积水变化摘要</h2><table><tr><th>当前积水</th><th>新增积水</th><th>持续积水</th><th>消退积水</th><th>净变化</th></tr><tr><td>{_pct(row.current_water_ratio)}</td><td>{_pct(row.new_water_ratio)}</td><td>{_pct(row.persistent_water_ratio)}</td><td>{_pct(row.receded_water_ratio)}</td><td>{_pct(row.net_change_ratio)}</td></tr></table>
<h2>研判依据</h2><ul>{''.join(f'<li>{html.escape(x)}</li>' for x in reasons)}</ul><h2>水害关注区域图</h2>{image or '<p>图像不可用</p>'}<p>高水害关注区域：新增积水与持续积水；重点巡检区域：消退积水；一般区域：当前两期遥感水害识别中未发现明显积水异常，不代表绝对安全。</p>
<h2>辅助建议</h2><ul>{''.join(f'<li>{html.escape(x)}</li>' for x in advice)}</ul>{impact_section}{equipment_section}{terrain_section}{rainfall_section}{uav_section}<h2>成果文件</h2><p>水害关注区域 PNG、GeoTIFF、结构化摘要 JSON、CSV、HTML/PDF 报告。</p><h2>使用边界</h2><p>{html.escape(BOUNDARY_NOTE)}</p><p class="note">本报告基于遥感积水识别与双时相变化分析结果形成，不替代现场勘察、工程安全鉴定、道路承载评价、边坡稳定评价及施工单位正式停复工决策。设备位置为最近位置快照，不是实时定位；设备水害类别仅反映位置对应的遥感水害关注类别，不代表车辆安全状态、可通行性或自动控制结论。</p></body></html>"""


def risk_report_pdf(row: RiskAssessment, impacts: list[dict] | None = None, project_name: str | None = None,
                    equipment_links: list[dict] | None = None, terrain: dict | None = None,
                    rainfall: dict | None = None, uav: dict | None = None) -> bytes:
    return render_html_pdf(risk_report_html(row, impacts, project_name, equipment_links, terrain, rainfall, uav))
