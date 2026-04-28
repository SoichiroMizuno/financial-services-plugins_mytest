#!/usr/bin/env python3
"""
Valuation Engine — Web アプリ（FastAPI）
"""

import sys
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# プロジェクトルートを sys.path に追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from ingestion.parser import DocumentParser
from analysis.dcf import DCFAnalyzer

app = FastAPI(title="企業バリュエーションエンジン")

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

INPUT_DIR = Path(__file__).parent.parent / "input"
OUTPUT_DIR = Path(__file__).parent.parent / "output"
INPUT_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


# ── ページ ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/report", response_class=HTMLResponse)
async def report_page(request: Request):
    report_path = OUTPUT_DIR / "dcf_report.md"
    report_md = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    return templates.TemplateResponse(request, "report.html", {"report_md": report_md})


# ── API ───────────────────────────────────────────────────────────────────

@app.post("/analyze")
async def analyze(
    ticker: str = Form(...),
    price: float = Form(None),
    pdf_file: UploadFile = File(None),
):
    """
    PDF をアップロードして DCF 分析を実行する。
    PDF が省略された場合は input/ の既存ファイルを検索する。
    """
    # ── PDF の準備 ────────────────────────────────────────────────────
    pdf_path = None

    if pdf_file and pdf_file.filename:
        # アップロードされた PDF を保存
        save_path = INPUT_DIR / pdf_file.filename
        with open(save_path, "wb") as f:
            shutil.copyfileobj(pdf_file.file, f)
        pdf_path = str(save_path)
    else:
        # input/ ディレクトリから既存 PDF を探す
        existing = sorted(INPUT_DIR.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
        if existing:
            pdf_path = str(existing[0])

    if not pdf_path:
        return JSONResponse(
            status_code=400,
            content={"error": "PDF ファイルをアップロードするか、input/ フォルダに PDF を置いてください。"}
        )

    # ── PDF パース ────────────────────────────────────────────────────
    try:
        parser = DocumentParser()
        doc = parser.parse(pdf_path)
        if ticker:
            doc.ticker = ticker
        parsed_md = doc.to_markdown()
        (OUTPUT_DIR / "parsed.md").write_text(parsed_md, encoding="utf-8")
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"PDF の解析に失敗しました: {e}"})

    # ── DCF 分析 ──────────────────────────────────────────────────────
    try:
        analyzer = DCFAnalyzer()
        result = analyzer.analyze(
            parsed_markdown=parsed_md,
            company_name=doc.company_name,
            ticker=doc.ticker,
            fiscal_year=doc.fiscal_year,
            current_price=price,
        )
        (OUTPUT_DIR / "dcf_report.md").write_text(result.report_markdown, encoding="utf-8")
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"DCF 分析に失敗しました: {e}"})

    # 判定に応じた色
    color_map = {"割安": "green", "適正": "orange", "割高": "red", "判定不能": "gray"}

    return JSONResponse(content={
        "company_name": result.company_name,
        "ticker": result.ticker,
        "fiscal_year": result.fiscal_year,
        "judgment": result.judgment,
        "judgment_color": color_map.get(result.judgment, "gray"),
        "current_price": result.current_price,
        "fair_value_low": result.fair_value_low,
        "fair_value_mid": result.fair_value_mid,
        "fair_value_high": result.fair_value_high,
        "pdf_used": Path(pdf_path).name,
    })
