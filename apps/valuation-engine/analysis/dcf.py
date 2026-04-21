#!/usr/bin/env python3
"""
DCF Analysis — Claude API を使って DCF バリュエーションを実行する
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "dcf_prompt.md"

# Claude API に一度に渡すテキストの上限（トークン節約のため）
MAX_DOC_CHARS = 60_000


@dataclass
class DCFResult:
    company_name: str
    ticker: Optional[str]
    fiscal_year: str
    judgment: str           # 割安 / 適正 / 割高 / 判定不能
    current_price: Optional[float]
    fair_value_low: Optional[float]
    fair_value_mid: Optional[float]
    fair_value_high: Optional[float]
    report_markdown: str    # Claude が生成したフルレポート
    model_used: str = ""

    def summary(self) -> str:
        price_str = f"¥{self.current_price:,.0f}" if self.current_price else "不明"
        low  = f"¥{self.fair_value_low:,.0f}"  if self.fair_value_low  else "―"
        mid  = f"¥{self.fair_value_mid:,.0f}"  if self.fair_value_mid  else "―"
        high = f"¥{self.fair_value_high:,.0f}" if self.fair_value_high else "―"
        return (
            f"[DCFResult]\n"
            f"  企業名    : {self.company_name}\n"
            f"  コード    : {self.ticker or '不明'}\n"
            f"  決算期    : {self.fiscal_year}\n"
            f"  現在株価  : {price_str}\n"
            f"  適正株価  : {low} 〜 {mid} 〜 {high}\n"
            f"  判定      : {self.judgment}\n"
            f"  使用モデル: {self.model_used}"
        )


class DCFAnalyzer:
    """Claude API を使って DCF 分析を実行するクラス"""

    MODEL = "claude-sonnet-4-6"

    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY が設定されていません。.env ファイルを確認してください。"
            )
        self.client = anthropic.Anthropic(api_key=api_key)
        self.system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

    def analyze(
        self,
        parsed_markdown: str,
        company_name: str,
        ticker: Optional[str],
        fiscal_year: str,
        current_price: Optional[float] = None,
    ) -> DCFResult:
        """
        パース済みの財務書類 Markdown を受け取り、DCF 分析を実行して DCFResult を返す。

        Args:
            parsed_markdown: parser.py が出力した Markdown テキスト
            company_name:    企業名
            ticker:          証券コード
            fiscal_year:     決算期
            current_price:   現在株価（指定があれば判定に使用）
        """
        doc_text = self._trim_document(parsed_markdown)
        user_message = self._build_user_message(
            doc_text, company_name, ticker, fiscal_year, current_price
        )

        print(f"  Claude API ({self.MODEL}) に送信中...")
        print(f"  ドキュメント文字数: {len(doc_text):,}")

        message = self.client.messages.create(
            model=self.MODEL,
            max_tokens=4096,
            system=self.system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        report_markdown = message.content[0].text
        judgment, low, mid, high = self._parse_judgment(report_markdown, current_price)

        return DCFResult(
            company_name=company_name,
            ticker=ticker,
            fiscal_year=fiscal_year,
            judgment=judgment,
            current_price=current_price,
            fair_value_low=low,
            fair_value_mid=mid,
            fair_value_high=high,
            report_markdown=report_markdown,
            model_used=self.MODEL,
        )

    # ── 内部メソッド ──────────────────────────────────────────────────────

    def _trim_document(self, text: str) -> str:
        """長すぎる文書を上限文字数に収める"""
        if len(text) <= MAX_DOC_CHARS:
            return text
        trimmed = text[:MAX_DOC_CHARS]
        return trimmed + "\n\n[... 文書が長いため省略されました ...]"

    def _build_user_message(
        self,
        doc_text: str,
        company_name: str,
        ticker: Optional[str],
        fiscal_year: str,
        current_price: Optional[float],
    ) -> str:
        price_line = (
            f"**現在株価**: ¥{current_price:,.0f}（この価格と比較して割安/適正/割高を判定してください）"
            if current_price
            else "**現在株価**: 未提供（適正株価レンジのみ算出してください）"
        )
        return (
            f"以下の財務書類データを基に、DCFバリュエーション分析レポートを作成してください。\n\n"
            f"**対象企業**: {company_name}\n"
            f"**証券コード**: {ticker or '不明'}\n"
            f"**決算期**: {fiscal_year}\n"
            f"{price_line}\n\n"
            f"---\n\n"
            f"{doc_text}"
        )

    def _parse_judgment(
        self,
        report: str,
        current_price: Optional[float],
    ) -> tuple[str, Optional[float], Optional[float], Optional[float]]:
        """
        Claude のレポートテキストから判定結果と適正株価を抽出する。
        正規表現で拾えない場合は None を返す（レポート本文は保持する）。
        """
        import re

        # 判定キーワードを探す
        judgment = "判定不能"
        for keyword in ["割安", "適正", "割高"]:
            if keyword in report:
                judgment = keyword
                break

        # 適正株価レンジを「¥X,XXX〜¥X,XXX」のパターンで探す
        low = mid = high = None
        price_pattern = re.compile(r"[¥￥](\d[\d,]+)")
        prices = [
            float(m.group(1).replace(",", ""))
            for m in price_pattern.finditer(report)
            if float(m.group(1).replace(",", "")) > 100  # 株価らしい値のみ
        ]
        if len(prices) >= 2:
            prices_sorted = sorted(set(prices))
            low  = prices_sorted[0]
            high = prices_sorted[-1]
            mid  = prices_sorted[len(prices_sorted) // 2]

        return judgment, low, mid, high
