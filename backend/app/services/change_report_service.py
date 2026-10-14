from __future__ import annotations

import base64
import html
from datetime import datetime
from pathlib import Path

from ..db.models import ChangeAnalysis
from .report_service import render_html_pdf


def _pct(value: float | None) -> str:
    return "--" if value is None else f"{value * 100:.2f}%"


def _num(value: int | None) -> str:
    return "--" if value is None else f"{value:,}"


def generate_change_report_html(row: ChangeAnalysis) -> str:
    image = ""
    if row.change_png_path and Path(row.change_png_path).is_file():
        encoded = base64.b64encode(Path(row.change_png_path).read_bytes()).decode("ascii")
        image = f'<img src="data:image/png;base64,{encoded}" alt="积水变化分布图">'
    title = html.escape(row.name)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    metric_rows = "".join(
        f"<tr><td>{label}</td><td>{_num(count)}</td><td>{_pct(ratio)}</td></tr>"
        for label, count, ratio in (
            ("新增积水", row.new_water_pixels, row.new_water_ratio),
            ("持续积水", row.persistent_water_pixels, row.persistent_water_ratio),
            ("消退积水", row.receded_water_pixels, row.receded_water_ratio),
            ("持续非积水", row.stable_non_water_pixels, row.stable_non_water_ratio),
        )
    )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>{title}</title>
<style>@page{{size:A4;margin:18mm}}body{{font-family:'Microsoft YaHei',sans-serif;color:#253746;line-height:1.65}}h1{{color:#173a52}}.brand{{color:#2d7195}}.meta{{background:#f3f7f9;padding:12px}}table{{width:100%;border-collapse:collapse;margin:16px 0}}th,td{{border:1px solid #d8e2e8;padding:8px;text-align:left}}th{{background:#edf3f6}}img{{width:100%;max-height:520px;object-fit:contain}}.note{{font-size:12px;color:#687985}}</style></head>
<body><div class="brand">工程水害管理系统 · 时空变化分析</div><h1>{title}</h1>
<div class="meta">分析编号：{row.id}<br>T1：{html.escape(row.dataset_t1_name)}（{row.finished_t1_at or '--'}，{html.escape(row.model_t1_name)}，阈值 {row.threshold_t1}）<br>T2：{html.escape(row.dataset_t2_name)}（{row.finished_t2_at or '--'}，{html.escape(row.model_t2_name)}，阈值 {row.threshold_t2}）<br>空间一致性：已通过 CRS、Transform、Bounds、宽高和 NoData 检查<br>生成时间：{generated}　算法版本：{html.escape(row.algorithm_version)}</div>
<h2>变化概览</h2><p>T1 积水占比 {_pct(row.t1_water_ratio)}，T2 积水占比 {_pct(row.t2_water_ratio)}，净变化 {_pct(row.net_change_ratio)}。</p>
<table><thead><tr><th>变化类别</th><th>像素数</th><th>有效区域占比</th></tr></thead><tbody>{metric_rows}</tbody></table>
<h2>空间变化分布</h2>{image or '<p>变化图当前不可用。</p>'}
<p class="note">本报告基于两个空间一致的遥感积水识别结果进行时空变化分析，用于辅助理解施工区域积水空间变化过程。分析结果不直接代表积水深度、道路承载能力或工程机械绝对安全通行条件，应结合现场核查及施工安全管理制度综合使用。</p></body></html>"""


def generate_change_report_pdf(row: ChangeAnalysis) -> bytes:
    return render_html_pdf(generate_change_report_html(row))
