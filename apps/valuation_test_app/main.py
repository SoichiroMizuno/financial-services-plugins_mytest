#!/usr/bin/env python3
"""
Valuation Engine — エントリーポイント

使い方:
  # PDFを手動指定（Phase 1 と同じ）
  python main.py --file 有価証券報告書.pdf --ticker 7203

  # ティッカーだけで全自動（Phase 2: EDINET + J-Quants 連携）
  python main.py --ticker 7203
"""

import argparse
import sys
from pathlib import Path

from ingestion.parser import DocumentParser
from analysis.dcf import DCFAnalyzer


def main():
    parser = argparse.ArgumentParser(
        description="企業バリュエーションエンジン",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
例:
  python main.py --ticker 7203               # 全自動（EDINET + J-Quants）
  python main.py --file 報告書.pdf           # PDF を手動指定
  python main.py --file 報告書.pdf --price 3500  # 株価も手動指定
        """,
    )
    parser.add_argument("--ticker", default=None, help="証券コード（例: 7203）")
    parser.add_argument("--file",   default=None, help="PDF ファイルパス（省略時は EDINET から自動取得）")
    parser.add_argument("--price",  type=float,   help="現在株価（省略時は J-Quants から自動取得）")
    parser.add_argument("--output-dir", default="output", help="出力先ディレクトリ")
    args = parser.parse_args()

    if not args.ticker and not args.file:
        parser.error("--ticker または --file のいずれかを指定してください。")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = args.file
    current_price = args.price
    ticker = args.ticker

    # ── Step 1: PDF を用意する ─────────────────────────────────────────
    print("=" * 50)
    print("Step 1: 有価証券報告書を用意しています...")
    print("=" * 50)

    if pdf_path:
        print(f"  手動指定ファイルを使用: {pdf_path}")
    else:
        # EDINET から自動取得
        pdf_path = _fetch_from_edinet(ticker, input_dir="input")
        if not pdf_path:
            print("エラー: EDINET からの取得に失敗しました。--file でPDFを直接指定してください。")
            sys.exit(1)

    # ── Step 2: 株価を取得する ─────────────────────────────────────────
    if ticker and current_price is None:
        print("")
        print("=" * 50)
        print("Step 2: 現在株価を取得しています...")
        print("=" * 50)
        current_price = _fetch_price_from_jquants(ticker)

    # ── Step 3: PDF をパースする ───────────────────────────────────────
    print("")
    print("=" * 50)
    print("Step 3: PDF を解析しています...")
    print("=" * 50)

    doc_parser = DocumentParser()
    try:
        doc = doc_parser.parse(pdf_path)
    except FileNotFoundError as e:
        print(f"エラー: {e}")
        sys.exit(1)

    if ticker:
        doc.ticker = ticker

    print(doc.summary())

    parsed_md_path = output_dir / "parsed.md"
    parsed_markdown = doc.to_markdown()
    parsed_md_path.write_text(parsed_markdown, encoding="utf-8")
    print(f"\nパース結果: {parsed_md_path}（{len(parsed_markdown):,} 文字）")

    # ── Step 4: DCF 分析を実行する ─────────────────────────────────────
    print("")
    print("=" * 50)
    print("Step 4: DCF バリュエーションを実行しています...")
    print("=" * 50)

    if current_price:
        print(f"  現在株価: ¥{current_price:,.0f}")
    else:
        print("  現在株価: 未取得（適正株価レンジのみ算出します）")

    try:
        analyzer = DCFAnalyzer()
    except EnvironmentError as e:
        print(f"エラー: {e}")
        sys.exit(1)

    result = analyzer.analyze(
        parsed_markdown=parsed_markdown,
        company_name=doc.company_name,
        ticker=doc.ticker,
        fiscal_year=doc.fiscal_year,
        current_price=current_price,
    )

    print(result.summary())

    # ── Step 5: レポートを保存する ────────────────────────────────────
    print("")
    print("=" * 50)
    print("Step 5: レポートを保存しています...")
    print("=" * 50)

    report_path = output_dir / "dcf_report.md"
    report_path.write_text(result.report_markdown, encoding="utf-8")
    print(f"DCFレポート: {report_path}")
    print("\n完了しました。")


# ── ヘルパー関数 ─────────────────────────────────────────────────────────

def _fetch_from_edinet(ticker: str, input_dir: str) -> str | None:
    """EDINET から有価証券報告書 PDF を取得する"""
    try:
        from ingestion.edinet import EDINETClient
        client = EDINETClient()
        return client.fetch_annual_report_pdf(ticker, output_dir=input_dir)
    except EnvironmentError as e:
        print(f"  EDINET: {e}")
        return None
    except Exception as e:
        print(f"  EDINET: 取得中にエラーが発生しました: {e}")
        return None


def _fetch_price_from_jquants(ticker: str) -> float | None:
    """J-Quants から現在株価を取得する"""
    try:
        from ingestion.jquants import JQuantsClient
        client = JQuantsClient()
        price = client.get_current_price(ticker)
        if price:
            print(f"  J-Quants: ¥{price:,.0f} を取得しました。")
        else:
            print(f"  J-Quants: 株価を取得できませんでした（--price で手動指定してください）。")
        return price
    except EnvironmentError as e:
        print(f"  J-Quants: {e}")
        return None
    except Exception as e:
        print(f"  J-Quants: 取得中にエラーが発生しました: {e}")
        return None


if __name__ == "__main__":
    main()
