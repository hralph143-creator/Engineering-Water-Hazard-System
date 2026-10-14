"""生成 A4 排版的遥感识别专业报告（HTML + PDF）。

报告数据只来自识别结果文件、影像元数据和 SQLite 持久化统计，不读取 AI 对话内容。
HTML 与 PDF 共用同一份结构和样式；PDF 由系统 Edge/Chromium 无头打印，并由
PyMuPDF 补充页码，避免浏览器默认页眉、URL 和日期进入正式报告。
"""
from __future__ import annotations

import base64
import io
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..db.models import InferenceResult

logger = logging.getLogger(__name__)

APP_TITLE = "工程水害管理系统"
APP_NAME = "工程水害管理系统"
REPORT_TITLE = "施工区域水害智能分析报告"
REPORT_TAG = "积水识别成果"
FOOTER_TEXT = "Sentinel-1 SAR 施工区域积水识别成果"

# ── 统一配色（深蓝灰 + 遥感蓝 + 中性纸白，颜色总数受控）──
C_DEEP = "#173A52"    # 深蓝灰：主标题 / 页眉深色带 / 指标数值
C_BLUE = "#2D7195"    # 专业蓝：强调 / 竖线 / 页眉浅色带文字
C_SKY = "#88AFC4"     # 中浅蓝灰：页眉浅色带
C_TEXT = "#263746"    # 正文
C_SUB = "#6D7D88"     # 次级文字
C_FOOT = "#71808A"    # 页脚文字
C_PAPER = "#FAFBF9"   # 纸白（略偏暖中性，非纯白非灰）
C_PANEL = "#F5F7F8"   # 极浅信息区（指标带 / 说明区）
C_LINE = "#D9E2E8"    # 分隔线 / 表格细线
C_SCREEN_BG = "#EDF1F4"  # 在线查看时的外层背景

_RISK_TONE = {
    "高风险": "#C0564F", "中风险": "#B07A2E",
    "低风险": "#3C8D73", "安全": "#3E7FA0",
}


def _fmt(value: float | None, digits: int = 4) -> str:
    return "--" if value is None else f"{value:.{digits}f}"


def _pct(value: float | None, digits: int = 2) -> str:
    return "--" if value is None else f"{value * 100:.{digits}f}%"


def _int(value: int | None) -> str:
    return "--" if value is None else f"{int(value):,}"


def _ts(value) -> str:
    """统一报告时间格式：去除微秒并保留可读的本地日期时间。"""
    if value is None:
        return "--"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)[:19]


def _img_data_url(path: Path) -> str | None:
    if not path.is_file():
        return None
    mime = "image/png" if path.suffix.lower() == ".png" else "image/tiff"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _risk_tone(level: str) -> str:
    return _RISK_TONE.get(level, C_BLUE)


# ─────────────────────────────── 样式 ───────────────────────────────
BASE_CSS = """
  :root {
    --font-cn: "Microsoft YaHei","Microsoft YaHei UI","PingFang SC","Noto Sans CJK SC","SimHei",sans-serif;
    --font-latin: "Arial","Helvetica","Inter","Segoe UI",sans-serif;
    --space-section: 20px;
    --space-title-content: 12px;
    --space-group-title: 8px;
    --space-caption: 6px;
  }
  @page { size: A4; margin: 24mm 19mm 20mm 19mm; }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body { font-family: var(--font-cn); color: #263746; font-size: 11.5px; line-height: 1.6; }

  /* ── 每页页眉：极薄双色蓝灰横带（position:fixed 打印时每页重复）── */
  .pg-head { position: fixed; top: 0; left: 0; right: 0; height: 7mm; z-index: 10; }
  .pg-head table { width: 100%; height: 100%; border-collapse: collapse; table-layout: fixed; }
  .pg-head td { padding: 0 12px; font-size: 9.5px; vertical-align: middle; }
  .pg-head .shade-l { width: 35%; background: #88AFC4; color: #17324a; font-weight: 600; }
  .pg-head .shade-r { background: #173A52; color: #EAF1F5; text-align: right; }

  /* ── 每页页脚：细线 + 左侧蓝色强调条 + 中央页码 + 右侧报告时间 ── */
  .pg-foot { position: fixed; bottom: 0; left: 0; right: 0; z-index: 10; }
  .pg-foot .rule { border-top: 0.4px solid #71808A; }
  .pg-foot table { width: 100%; border-collapse: collapse; table-layout: fixed; }
  .pg-foot td { padding-top: 3px; font-size: 8.5px; color: #85919A; vertical-align: top; }
  .pg-foot .bl { width: 26%; }
  .pg-foot .pg { width: 48%; text-align: center; }
  .pg-foot .tm { width: 26%; text-align: right; }
  .pg-foot .bar { display: inline-block; width: 14px; height: 3px; background: #2D7195;
                  vertical-align: middle; margin-right: 6px; }

  /* ── 统一报告头（一套整体，非多层横框）── */
  .report-head { padding-top: 10mm; }
  .report-head-top { overflow: hidden; margin-bottom: 9px; }
  .report-head-top .sys { float: left; font-size: 10px; color: #2D7195; font-weight: 600;
                          letter-spacing: .15px; }
  .report-head-top .tag { float: right; font-size: 8.5px; color: #9AA7B0; letter-spacing: 1.1px;
                          font-family: var(--font-latin); }
  h1.report-title { font-size: 25px; color: #173A52; font-weight: 600; margin: 0 0 8px;
                    letter-spacing: 0; line-height: 1.25; }
  .report-meta { font-size: 11px; color: #6D7D88; }
  .report-meta b { color: #263746; font-weight: 600; }
  .head-rule { border: none; border-top: 1px solid #D9E2E8; margin: 14px 0 0; }

  /* ── 章节标题：左侧蓝色短竖线 + 编号，标题下方极细分隔线 ── */
  .section { margin: var(--space-section) 0 0; }
  .section.page2 { page-break-before: always; break-before: page; margin-top: 0; padding-top: 10mm; }
  .section-title { font-size: 15px; color: #173A52; font-weight: 600; padding-left: 8px;
                   border-left: 2.5px solid #2D7195; line-height: 1.35; }
  .section-title .no { font-family: var(--font-latin); color: #2D7195; margin-right: 7px; font-weight: 600; }
  .section-rule { border: none; border-top: 0.5px solid #D9E2E8;
                  margin: 6px 0 var(--space-title-content); }

  /* ── 核心指标带：同一背景、仅 3 条极淡纵向分隔线、压缩高度 ── */
  table.metrics { width: 100%; border-collapse: collapse; background: #F5F7F8;
                  border: 0.5px solid #D8E0E5; }
  table.metrics td { width: 25%; text-align: center; padding: 7px 4px 9px; }
  table.metrics td.sep { border-left: 0.5px solid #D8E0E5; }
  .m-label { font-size: 10.5px; color: #6D7D88; font-weight: 500; }
  .m-value { font-size: 25px; color: #173A52; font-weight: 600; line-height: 1.15; margin-top: 3px;
             font-family: var(--font-latin); }
  .m-value.risk { font-size: 20px; font-family: var(--font-cn); }
  .risk-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%;
              margin-right: 5px; vertical-align: 3px; }

  /* ── 双栏字段表（数据/识别参数、像素/概率统计）── */
  table.cols { width: 100%; border-collapse: collapse; }
  table.cols td.col { width: 50%; vertical-align: top; padding: 0 14px 0 0; }
  table.cols td.col + td.col { padding: 0 0 0 14px; }
  .sub-title { font-size: 12px; color: #173A52; font-weight: 600;
               margin: 0 0 var(--space-group-title); }
  table.kv { width: 100%; border-collapse: collapse; }
  table.kv td { padding: 3px 0; font-size: 11px; vertical-align: top; }
  table.kv td.k { color: #6D7D88; width: 32%; padding-right: 8px; font-weight: 500; }
  table.kv td.v { color: #263746; font-weight: 400; word-break: break-all; overflow-wrap: anywhere; }
  table.kv td.v.right { text-align: right; font-family: var(--font-latin);
                        font-variant-numeric: tabular-nums; }

  /* ── 结果说明 ── */
  .summary { font-size: 11.5px; color: #263746; }
  .summary p { margin: 0 0 6px; }
  .note { background: #F5F7F8; border: 0.5px solid #D8E0E5; border-left: 2px solid #2D7195;
          padding: 8px 12px; font-size: 10.5px; color: #6D7D88; margin-top: 10px; }

  /* ── 图片区：图注全部由 HTML 承担，图片本身不画标题 ── */
  .fig { text-align: center; margin: 0; }
  .fig img { display: block; width: 100%; border: 0.5px solid #DDE3E7; object-fit: contain; }
  table.figure-pair .fig img { aspect-ratio: 1 / 1; }
  .figcap { text-align: center; font-size: 10px; color: #778690;
            margin: var(--space-caption) 0 18px; }
  .figcap .fig-no { color: #526875; font-weight: 500; margin-right: 7px; }
  .fig-missing { text-align: center; font-size: 11px; color: #6D7D88;
                 border: 0.5px dashed #D8E0E5; padding: 18px 8px; margin: 2px 0 12px; }
  @media screen {
    body { background: #EDF1F4; padding: 34px 16px 40px; }
    .page { max-width: 190mm; margin: 0 auto; background: #FAFBF9; padding: 14mm 18mm;
            box-shadow: 0 2px 18px rgba(23,58,82,.12); }
  }
  @media print {
    body { background: #fff; padding: 0; }
    .page { padding: 0; }
  }
"""


# ─────────────────────────────── 区块构建 ───────────────────────────────
def _pg_header() -> str:
    return f"""
  <div class="pg-head">
    <table><tr>
      <td class="shade-l">{APP_TITLE}</td>
      <td class="shade-r">{APP_NAME} · {FOOTER_TEXT}</td>
    </tr></table>
  </div>"""


def _pg_footer(generated_at: str) -> str:
    return f"""
  <div class="pg-foot">
    <div class="rule"></div>
    <table><tr>
      <td class="bl"><span class="bar"></span>{APP_TITLE}</td>
      <td class="pg" id="pg-no"></td>
      <td class="tm">报告时间：{generated_at}</td>
    </tr></table>
  </div>"""


def _build_header(dataset, task) -> str:
    meta = (f"<b>{dataset.name}</b>　·　{dataset.source_type or '--'}　·　"
            f"识别时间 {_ts(task.finished_at)}")
    return f"""
  <div class="report-head">
    <div class="report-head-top">
      <span class="sys">{APP_TITLE}</span>
      <span class="tag">{REPORT_TAG}</span>
    </div>
    <h1 class="report-title">{REPORT_TITLE}</h1>
    <div class="report-meta">{meta}</div>
    <hr class="head-rule"/>
  </div>"""


def _section(num: str, title: str, body: str, *, page_break: bool = False) -> str:
    classes = "section page2" if page_break else "section"
    return f"""
  <div class="{classes}">
    <div class="section-title"><span class="no">{num}</span>{title}</div>
    <hr class="section-rule"/>
    {body}
  </div>"""


def _build_metrics(result, ratio, mean_conf, high_ratio) -> str:
    tone = _risk_tone(result.risk_level)
    risk_html = f'<span class="risk-dot" style="background:{tone}"></span>{result.risk_level}'
    body = f"""
    <table class="metrics">
      <tr>
        <td><div class="m-label">积水占比</div><div class="m-value">{ratio}</div></td>
        <td class="sep"><div class="m-label">平均置信度</div><div class="m-value">{mean_conf}</div></td>
        <td class="sep"><div class="m-label">高置信积水比例</div><div class="m-value">{_pct(high_ratio)}</div></td>
        <td class="sep"><div class="m-label">基础水害提示</div><div class="m-value risk" style="color:{tone}">{risk_html}</div></td>
      </tr>
    </table>"""
    return _section("01", "识别概览", body)


def _build_tech_info(dataset, task) -> str:
    left = f"""
      <div class="sub-title">数据概况</div>
      <table class="kv">
        <tr><td class="k">数据来源</td><td class="v">{dataset.source_type or '--'}</td></tr>
        <tr><td class="k">影像尺寸</td><td class="v">{dataset.width} × {dataset.height} px</td></tr>
        <tr><td class="k">波段</td><td class="v">{dataset.bands}（VV / VH）</td></tr>
        <tr><td class="k">坐标参考系统</td><td class="v">{dataset.crs or '--'}</td></tr>
      </table>"""
    right = f"""
      <div class="sub-title">识别参数</div>
      <table class="kv">
        <tr><td class="k">识别阈值</td><td class="v">{task.threshold:.2f}</td></tr>
        <tr><td class="k">输入波段</td><td class="v">VV + VH</td></tr>
        <tr><td class="k">输出内容</td><td class="v">积水概率 / 积水分布</td></tr>
        <tr><td class="k">识别模型</td><td class="v">{task.model_name}</td></tr>
        <tr><td class="k">统计口径</td><td class="v">影像有效像元</td></tr>
      </table>"""
    body = f'<table class="cols"><tr><td class="col">{left}</td><td class="col">{right}</td></tr></table>'
    return _section("02", "数据与识别参数", body)


def _build_statistics(result, ratio, mean_conf) -> str:
    left = f"""
      <div class="sub-title">像素统计</div>
      <table class="kv">
        <tr><td class="k">有效像素</td><td class="v right">{_int(result.valid_pixel_count)}</td></tr>
        <tr><td class="k">积水像素</td><td class="v right">{_int(result.flood_pixel_count)}</td></tr>
        <tr><td class="k">积水占比</td><td class="v right">{ratio}</td></tr>
        <tr><td class="k">高置信积水像素</td><td class="v right">{_int(result.high_confidence_flood_pixel_count)}</td></tr>
        <tr><td class="k">高置信积水比例</td><td class="v right">{_pct(result.high_confidence_flood_ratio)}</td></tr>
      </table>"""
    right = f"""
      <div class="sub-title">概率统计</div>
      <table class="kv">
        <tr><td class="k">概率最小值</td><td class="v right">{_fmt(result.probability_min)}</td></tr>
        <tr><td class="k">概率最大值</td><td class="v right">{_fmt(result.probability_max)}</td></tr>
        <tr><td class="k">概率均值</td><td class="v right">{_fmt(result.probability_mean)}</td></tr>
        <tr><td class="k">概率中位数</td><td class="v right">{_fmt(result.probability_median)}</td></tr>
        <tr><td class="k">平均积水置信度</td><td class="v right">{mean_conf}</td></tr>
      </table>"""
    body = f'<table class="cols"><tr><td class="col">{left}</td><td class="col">{right}</td></tr></table>'
    return _section("03", "统计结果", body)


def _build_explanation(result, ratio, mean_conf, high_ratio) -> str:
    sentence = (
        f"该影像共包含 {_int(result.valid_pixel_count)} 个有效像素，其中识别积水像素 "
        f"{_int(result.flood_pixel_count)} 个，占有效区域的 {ratio}。识别积水区域平均置信度为 {mean_conf}"
    )
    if result.high_confidence_flood_pixel_count is not None and high_ratio is not None:
        sentence += f"，高置信积水像素占识别积水像素的 {_pct(high_ratio)}"
    sentence += "。"
    body = f"""
      <div class="summary">
        <p>{sentence}</p>
        <p>按照系统当前积水占比阈值规则，本景识别结果的基础水害提示为“{result.risk_level}”。</p>
      </div>
      <div class="note">基础水害提示依据积水识别占比规则确定，仅用于辅助分析，不代表真实水深，也不直接等同于现场安全风险等级。</div>"""
    return _section("04", "结果说明", body)


def _image_block(data_url: str | None, number: int, title: str) -> str:
    caption = f"图 {number} {title}"
    if data_url:
        img = f'<div class="fig"><img src="{data_url}" alt="{caption}"/></div>'
    else:
        img = '<div class="fig-missing">该结果图文件当前不可用</div>'
    return (f'{img}<div class="figcap"><span class="fig-no">图 {number}</span>'
            f'<span>{title}</span></div>')


def _build_result_images(prob_img, mask_img, comp_img) -> str:
    pair = f"""<table class="cols figure-pair"><tr>
      <td class="col">{_image_block(prob_img, 1, "积水概率分布")}</td>
      <td class="col">{_image_block(mask_img, 2, "积水识别分布")}</td>
    </tr></table>"""
    full = _image_block(comp_img, 3, "Sentinel-1 VV/VH 与识别结果对照")
    body = f"""{pair}
    {full}"""
    return _section("05", "积水识别结果", body, page_break=True)


def _build_artifacts_and_usage() -> str:
    body = """
      <table class="cols"><tr>
        <td class="col">
          <div class="sub-title">成果文件</div>
          <table class="kv">
            <tr><td class="k">空间成果</td><td class="v">积水概率 GeoTIFF / 积水分布 GeoTIFF</td></tr>
            <tr><td class="k">图像成果</td><td class="v">结果对比 PNG</td></tr>
            <tr><td class="k">统计成果</td><td class="v">积水识别统计 CSV</td></tr>
            <tr><td class="k">文档成果</td><td class="v">HTML / PDF 分析报告</td></tr>
          </table>
        </td>
        <td class="col">
          <div class="sub-title">使用说明</div>
          <div class="summary"><p>识别结果反映遥感影像中模型判定的积水或水体空间范围，不代表真实水深。使用时应结合施工现场条件、巡检记录和安全管理要求进行复核。</p></div>
        </td>
      </tr></table>
      <div class="note">本报告基于遥感影像和人工智能模型生成，用于施工区域水害识别与辅助研判，不替代现场勘察、工程安全鉴定及施工单位正式安全决策。</div>"""
    return _section("06", "成果文件与使用说明", body)


# ─────────────────────────────── 组装 ───────────────────────────────
def generate_report_html(result: InferenceResult) -> str:
    task = result.task
    dataset = task.dataset
    out_dir = Path(result.comparison_path).parent
    prob_img = _img_data_url(out_dir / "probability.png")
    mask_img = _img_data_url(out_dir / "mask.png")
    comp_img = _img_data_url(out_dir / "comparison.png")
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    ratio = _pct(result.flood_ratio)
    mean_conf = _pct(result.mean_confidence)
    high_ratio = result.high_confidence_flood_ratio

    header = _build_header(dataset, task)
    metrics = _build_metrics(result, ratio, mean_conf, high_ratio)
    tech = _build_tech_info(dataset, task)
    stats = _build_statistics(result, ratio, mean_conf)
    explanation = _build_explanation(result, ratio, mean_conf, high_ratio)
    images = _build_result_images(prob_img, mask_img, comp_img)
    artifacts = _build_artifacts_and_usage()

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>{REPORT_TITLE}</title>
<style>{BASE_CSS}</style>
</head>
<body>
{_pg_header()}
{_pg_footer(generated_at)}
<div class="page">
{header}
{metrics}
{tech}
{stats}
{explanation}
{images}
{artifacts}
</div>
</body>
</html>"""


# ─────────────────────────────── PDF 生成 ───────────────────────────────
_EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)
_PAGE_FONT = r"C:\Windows\Fonts\Deng.ttf"  # 页码用等线（单 ttf，PyMuPDF 可嵌入）


def _render_pdf_with_engine(html: str) -> bytes:
    """用系统 Edge / Chrome 无头模式把 HTML 打印为 PDF（嵌入中文字体子集、无浏览器页眉页脚）。"""
    exe = next((p for p in _EDGE_CANDIDATES if Path(p).exists()), None)
    if exe is None:
        raise RuntimeError("未找到可用的 Edge / Chrome 无头打印引擎")
    tmp_root = Path(tempfile.gettempdir()) / "ZhiyuShield"
    job = tmp_root / f"report_{uuid.uuid4().hex[:10]}"
    job.mkdir(parents=True, exist_ok=True)
    logger.info("PDF 报告生成开始 job=%s", job.name)
    try:
        html_file = job / "report.html"
        pdf_file = job / "report.pdf"
        profile = job / "profile"
        html_file.write_text(html, encoding="utf-8")
        cmd = [
            exe, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
            f"--user-data-dir={profile}",
            f"--print-to-pdf={pdf_file}",
            html_file.resolve().as_uri(),
        ]
        subprocess.run(cmd, timeout=120, capture_output=True)
        if not pdf_file.exists():
            raise RuntimeError("无头打印未产出 PDF")
        logger.info("PDF 报告生成完成 job=%s", job.name)
        return pdf_file.read_bytes()
    except Exception:
        logger.exception("PDF 报告生成失败 job=%s", job.name)
        raise
    finally:
        shutil.rmtree(job, ignore_errors=True)


def _add_page_numbers(pdf_bytes: bytes) -> bytes:
    """PyMuPDF 在每页页脚中央绘制页码（如 01 / 02）。"""
    import fitz

    mm = 72.0 / 25.4
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total = doc.page_count
    for i, page in enumerate(doc):
        text = f"{i + 1:02d} / {total:02d}"
        size = 9.0
        tw = fitz.get_text_length(text, fontname="helv", fontsize=size)
        x = page.rect.width / 2 - tw / 2
        y = page.rect.height - 6.0 * mm
        page.insert_text(
            (x, y), text, fontsize=size,
            fontfile=_PAGE_FONT, fontname="pagefont", color=(0.443, 0.502, 0.541),
        )
    out = io.BytesIO()
    doc.save(out, garbage=3, deflate=True)
    doc.close()
    return out.getvalue()


def generate_report_pdf(result: InferenceResult) -> bytes:
    """生成无浏览器页眉页脚、中文字体正常嵌入的 A4 报告 PDF。"""
    html = generate_report_html(result)
    pdf = _render_pdf_with_engine(html)
    return _add_page_numbers(pdf)


def render_html_pdf(html: str) -> bytes:
    """供其他业务报告复用的统一 A4 PDF 渲染入口。"""
    return _add_page_numbers(_render_pdf_with_engine(html))
