#!/usr/bin/env python3
"""
PDF Document Parser for Japanese Financial Documents
Supports: 有価証券報告書, 決算短信, 統合報告書
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
import pdfplumber


# ── データ構造 ──────────────────────────────────────────────────────────────

@dataclass
class FinancialSection:
    title: str
    content: str
    page_start: int
    page_end: int


@dataclass
class ParsedDocument:
    file_path: str
    doc_type: str           # 有価証券報告書 / 決算短信 / 統合報告書 / 不明
    company_name: str
    fiscal_year: str
    ticker: Optional[str]
    sections: list[FinancialSection] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    full_text: str = ""

    def to_markdown(self) -> str:
        """Claude API に渡す Markdown 形式に変換する"""
        lines = [
            f"# {self.doc_type} — {self.company_name}",
            f"**決算期**: {self.fiscal_year}",
            f"**証券コード**: {self.ticker or '不明'}",
            "",
        ]

        for section in self.sections:
            lines.append(f"## {section.title}")
            lines.append(section.content.strip())
            lines.append("")

        if self.tables:
            lines.append("## 財務データ（表）")
            for tbl in self.tables:
                lines.append(f"### {tbl['title']}")
                lines.append(_table_to_markdown(tbl["rows"]))
                lines.append("")

        return "\n".join(lines)

    def summary(self) -> str:
        """パース結果の概要を返す（デバッグ用）"""
        return (
            f"[ParsedDocument]\n"
            f"  種別    : {self.doc_type}\n"
            f"  企業名  : {self.company_name}\n"
            f"  決算期  : {self.fiscal_year}\n"
            f"  コード  : {self.ticker or '不明'}\n"
            f"  セクション数: {len(self.sections)}\n"
            f"  テーブル数  : {len(self.tables)}\n"
            f"  本文文字数  : {len(self.full_text):,}"
        )


# ── パーサー本体 ─────────────────────────────────────────────────────────────

class DocumentParser:
    """日本語財務書類 PDF のパーサー"""

    # 書類種別を判定するキーワード
    _DOC_TYPE_PATTERNS: dict[str, list[str]] = {
        "有価証券報告書": ["有価証券報告書"],
        "決算短信":       ["決算短信", "決算発表"],
        "統合報告書":     ["統合報告書", "アニュアルレポート", "annual report"],
    }

    # 抽出対象セクションのキーワード（表示名: [検索キーワード...]）
    _SECTION_PATTERNS: dict[str, list[str]] = {
        "事業概況":       ["事業の概況", "業績の概況", "経営成績等の状況", "経営成績"],
        "経営方針":       ["経営方針", "経営戦略", "中期経営計画", "事業方針"],
        "損益計算書":     ["損益計算書", "連結損益計算書", "純損益"],
        "貸借対照表":     ["貸借対照表", "連結貸借対照表", "資産の部"],
        "キャッシュフロー": ["キャッシュ・フロー計算書", "連結キャッシュ・フロー", "営業活動による"],
        "セグメント情報": ["セグメント情報", "セグメント別"],
        "業績予想":       ["業績予想", "次期見通し", "今後の見通し"],
    }

    # 財務表タイトルの検出パターン
    _TABLE_TITLE_PATTERNS = [
        r"連結?損益計算書",
        r"連結?貸借対照表",
        r"連結?キャッシュ[・]?フロー計算書",
        r"連結?包括利益計算書",
        r"セグメント情報",
        r"財務ハイライト",
    ]

    def parse(self, pdf_path: str) -> ParsedDocument:
        """PDF をパースして ParsedDocument を返す"""
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"ファイルが見つかりません: {pdf_path}")

        full_text = self._extract_text(pdf_path)
        doc_type = self._detect_doc_type(full_text)
        company_name = self._extract_company_name(full_text)
        fiscal_year = self._extract_fiscal_year(full_text)
        ticker = self._extract_ticker(full_text)
        sections = self._extract_sections(full_text)
        tables = self._extract_tables(pdf_path)

        return ParsedDocument(
            file_path=str(path.resolve()),
            doc_type=doc_type,
            company_name=company_name,
            fiscal_year=fiscal_year,
            ticker=ticker,
            sections=sections,
            tables=tables,
            full_text=full_text,
        )

    # ── テキスト抽出 ──────────────────────────────────────────────────────

    def _extract_text(self, pdf_path: str) -> str:
        """PyMuPDF でテキストを抽出する（日本語フォント対応）"""
        texts = []
        with fitz.open(pdf_path) as doc:
            for page in doc:
                # "blocks" モードで抽出するとレイアウトが保持されやすい
                text = page.get_text("text")
                texts.append(text)
        raw = "\n".join(texts)
        return self._clean_text(raw)

    def _clean_text(self, text: str) -> str:
        """不要な空白・制御文字を除去する"""
        # 連続する空行を1行に圧縮
        text = re.sub(r"\n{3,}", "\n\n", text)
        # ページ番号のみの行を除去（数字1〜4桁の単独行）
        text = re.sub(r"^\s*\d{1,4}\s*$", "", text, flags=re.MULTILINE)
        # 全角スペースを半角に統一
        text = text.replace("　", " ")
        return text.strip()

    # ── メタデータ抽出 ────────────────────────────────────────────────────

    def _detect_doc_type(self, text: str) -> str:
        text_lower = text[:3000]  # 先頭部分だけ見る
        for doc_type, keywords in self._DOC_TYPE_PATTERNS.items():
            if any(kw in text_lower for kw in keywords):
                return doc_type
        return "不明"

    def _extract_company_name(self, text: str) -> str:
        """
        先頭付近から企業名を抽出する。
        「提出会社」「会社名」「○○株式会社」などのパターンに対応。
        """
        patterns = [
            r"提出会社[　\s]*[:：]?[　\s]*([^\n]{2,30}(?:株式会社|㈱|有限会社|合同会社))",
            r"会社名[　\s]*[:：]?[　\s]*([^\n]{2,30}(?:株式会社|㈱|有限会社|合同会社))",
            r"^([^\n]{2,30}(?:株式会社|㈱))",
        ]
        for pattern in patterns:
            m = re.search(pattern, text[:5000], re.MULTILINE)
            if m:
                return m.group(1).strip()
        return "不明"

    def _extract_fiscal_year(self, text: str) -> str:
        """
        決算期を抽出する。
        「2024年3月期」「第○○期」などに対応。
        """
        patterns = [
            r"(\d{4}年\d{1,2}月期)",
            r"(第\s*\d+\s*期)",
            r"(\d{4}/\d{1,2}期)",
            r"自\s*\d{4}年.*?至\s*(\d{4}年\d{1,2}月\d{1,2}日)",
        ]
        for pattern in patterns:
            m = re.search(pattern, text[:5000])
            if m:
                return m.group(1).strip()
        return "不明"

    def _extract_ticker(self, text: str) -> Optional[str]:
        """
        証券コード（4桁数字）を抽出する。
        """
        patterns = [
            r"証券コード[　\s]*[:：]?[　\s]*(\d{4})",
            r"コード番号[　\s]*[:：]?[　\s]*(\d{4})",
            r"[\(（](\d{4})[\)）]",
        ]
        for pattern in patterns:
            m = re.search(pattern, text[:5000])
            if m:
                return m.group(1)
        return None

    # ── セクション抽出 ────────────────────────────────────────────────────

    def _extract_sections(self, full_text: str) -> list[FinancialSection]:
        """主要セクションをテキストから切り出す"""
        sections = []
        lines = full_text.split("\n")

        for section_title, keywords in self._SECTION_PATTERNS.items():
            content = self._find_section_content(lines, keywords)
            if content:
                sections.append(FinancialSection(
                    title=section_title,
                    content=content,
                    page_start=0,
                    page_end=0,
                ))

        return sections

    def _find_section_content(
        self,
        lines: list[str],
        keywords: list[str],
        max_chars: int = 3000,
    ) -> str:
        """
        キーワードが含まれる行を起点に、最大 max_chars 文字のコンテンツを返す。
        次のセクション見出しが現れたら打ち切る。
        """
        for i, line in enumerate(lines):
            if any(kw in line for kw in keywords):
                chunk = []
                char_count = 0
                for subsequent_line in lines[i:]:
                    if char_count > max_chars:
                        break
                    # 別のセクション見出しが始まったら打ち切る
                    if char_count > 200 and self._is_section_heading(subsequent_line):
                        break
                    chunk.append(subsequent_line)
                    char_count += len(subsequent_line)
                return "\n".join(chunk)
        return ""

    def _is_section_heading(self, line: str) -> bool:
        """行がセクション見出しらしいかを判定する"""
        stripped = line.strip()
        if not stripped:
            return False
        # 「第○節」「（○）」など見出し的なパターン
        heading_patterns = [
            r"^第\d+[章節項]",
            r"^[\d１２３４５６７８９]+[．.、]",
            r"^[（(]\d+[)）]",
        ]
        return any(re.match(p, stripped) for p in heading_patterns)

    # ── テーブル抽出 ──────────────────────────────────────────────────────

    def _extract_tables(self, pdf_path: str) -> list[dict]:
        """
        pdfplumber で財務表を抽出する。
        損益計算書・貸借対照表・CF計算書などを対象とする。
        """
        results = []
        title_pattern = re.compile("|".join(self._TABLE_TITLE_PATTERNS))

        with pdfplumber.open(pdf_path) as pdf:
            pending_title = "財務データ"
            for page in pdf.pages:
                # ページのテキストから表タイトルを探す
                page_text = page.extract_text() or ""
                m = title_pattern.search(page_text)
                if m:
                    pending_title = m.group(0)

                tables = page.extract_tables()
                for raw_table in tables:
                    if not raw_table or len(raw_table) < 2:
                        continue
                    cleaned = self._clean_table(raw_table)
                    if cleaned:
                        results.append({
                            "title": pending_title,
                            "rows": cleaned,
                        })

        return results

    def _clean_table(self, raw_table: list[list]) -> list[list[str]]:
        """None や空文字を除去してテーブルを整形する"""
        cleaned = []
        for row in raw_table:
            cleaned_row = [str(cell).strip() if cell is not None else "" for cell in row]
            # 空行は除外
            if any(cell for cell in cleaned_row):
                cleaned.append(cleaned_row)
        return cleaned


# ── ユーティリティ ────────────────────────────────────────────────────────

def _table_to_markdown(rows: list[list[str]]) -> str:
    """テーブルデータを Markdown の表形式に変換する"""
    if not rows:
        return ""

    header = rows[0]
    col_count = max(len(r) for r in rows)

    # 列数を統一する
    def pad(row: list[str]) -> list[str]:
        return row + [""] * (col_count - len(row))

    lines = []
    lines.append("| " + " | ".join(pad(header)) + " |")
    lines.append("|" + "|".join(["---"] * col_count) + "|")
    for row in rows[1:]:
        lines.append("| " + " | ".join(pad(row)) + " |")

    return "\n".join(lines)


# ── CLI エントリーポイント ────────────────────────────────────────────────

def main():
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python parser.py <pdf_file> [--json]")
        print("")
        print("Examples:")
        print("  python parser.py 有価証券報告書.pdf")
        print("  python parser.py 決算短信.pdf --json")
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_json = "--json" in sys.argv

    print(f"パース中: {pdf_path}")
    parser = DocumentParser()

    try:
        doc = parser.parse(pdf_path)
    except FileNotFoundError as e:
        print(f"エラー: {e}")
        sys.exit(1)

    print(doc.summary())
    print("")

    if output_json:
        # JSON形式で出力
        data = {
            "file_path": doc.file_path,
            "doc_type": doc.doc_type,
            "company_name": doc.company_name,
            "fiscal_year": doc.fiscal_year,
            "ticker": doc.ticker,
            "sections": [
                {"title": s.title, "content": s.content[:500]}
                for s in doc.sections
            ],
            "table_count": len(doc.tables),
        }
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        # Markdown形式で出力（Claude APIに渡す形式）
        md = doc.to_markdown()
        output_path = Path(pdf_path).stem + "_parsed.md"
        Path(output_path).write_text(md, encoding="utf-8")
        print(f"出力完了: {output_path}")
        print(f"（文字数: {len(md):,}）")


if __name__ == "__main__":
    main()
