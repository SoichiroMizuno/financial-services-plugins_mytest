#!/usr/bin/env python3
"""
EDINET API クライアント
有価証券報告書を証券コードから自動取得する
"""

import io
import os
import time
from datetime import date, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

EDINET_BASE = "https://disclosure.edinet-api.fsa.go.jp/api/v2"
DOC_TYPE_ANNUAL = "120"   # 有価証券報告書


class EDINETClient:

    def __init__(self):
        self.api_key = os.getenv("EDINET_API_KEY")
        if not self.api_key:
            raise EnvironmentError(
                "EDINET_API_KEY が設定されていません。.env を確認してください。\n"
                "取得先: https://edinet.fsa.go.jp/api/"
            )
        self.session = requests.Session()

    # ── 公開 API ────────────────────────────────────────────────────────

    def find_latest_annual_report(self, ticker: str) -> dict | None:
        """
        証券コードから最新の有価証券報告書の書類情報を返す。
        見つからない場合は None を返す。
        """
        print(f"  EDINET: 証券コード {ticker} の EDINET コードを検索中...")
        edinet_code = self._get_edinet_code(ticker)
        if not edinet_code:
            print(f"  EDINET: 証券コード {ticker} に対応する EDINET コードが見つかりません。")
            return None

        print(f"  EDINET: {edinet_code} の有価証券報告書を検索中（過去1年分）...")
        doc_info = self._search_annual_report(edinet_code)
        if not doc_info:
            print(f"  EDINET: 有価証券報告書が見つかりませんでした。")
            return None

        print(f"  EDINET: 発見 → {doc_info['docDescription']} （提出日: {doc_info['submitDateTime'][:10]}）")
        return doc_info

    def download_pdf(self, doc_id: str, output_path: str) -> str:
        """
        書類IDを指定して提出本文 PDF をダウンロードする。
        output_path に保存し、そのパスを返す。
        """
        print(f"  EDINET: PDF をダウンロード中 (docID: {doc_id})...")
        url = f"{EDINET_BASE}/documents/{doc_id}"
        params = {"type": 1, "Subscription-Key": self.api_key}   # type=1: 提出本文書PDF

        response = self.session.get(url, params=params, timeout=120, stream=True)
        response.raise_for_status()

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        size_mb = path.stat().st_size / 1024 / 1024
        print(f"  EDINET: ダウンロード完了 → {path} ({size_mb:.1f} MB)")
        return str(path)

    def fetch_annual_report_pdf(self, ticker: str, output_dir: str = "input") -> str | None:
        """
        証券コードを指定して最新の有価証券報告書 PDF を取得し、パスを返す。
        ワンストップ API。
        """
        doc_info = self.find_latest_annual_report(ticker)
        if not doc_info:
            return None

        # ファイル名: {ticker}_{提出日}_{書類名}.pdf
        submit_date = doc_info["submitDateTime"][:10].replace("-", "")
        filename = f"{ticker}_{submit_date}_有価証券報告書.pdf"
        output_path = str(Path(output_dir) / filename)

        # すでにダウンロード済みならスキップ
        if Path(output_path).exists():
            print(f"  EDINET: キャッシュ済みファイルを使用 → {output_path}")
            return output_path

        return self.download_pdf(doc_info["docID"], output_path)

    # ── 内部メソッド ──────────────────────────────────────────────────

    def _get_edinet_code(self, ticker: str) -> str | None:
        """
        提出者一覧 CSV から証券コードに対応する EDINET コードを返す。
        """
        url = f"{EDINET_BASE}/companies.csv"
        params = {"type": 2, "Subscription-Key": self.api_key}

        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"  EDINET: 提出者一覧の取得に失敗しました: {e}")
            return None

        # CSV を行ごとに処理（ヘッダー行をスキップ）
        content = response.content.decode("utf-8", errors="replace")
        for line in content.splitlines()[1:]:
            cols = line.split(",")
            if len(cols) < 9:
                continue
            # 証券コードは9列目（0始まりで8番目）
            code_col = cols[8].strip().strip('"')
            if code_col == ticker:
                edinet_code = cols[0].strip().strip('"')
                return edinet_code

        return None

    def _search_annual_report(self, edinet_code: str, days_back: int = 400) -> dict | None:
        """
        過去 days_back 日間のドキュメント一覧を日付逆順で検索し、
        指定 EDINET コードの有価証券報告書を返す。
        """
        today = date.today()

        for delta in range(0, days_back):
            target_date = today - timedelta(days=delta)
            docs = self._get_documents_on_date(target_date)

            for doc in docs:
                if (
                    doc.get("edinetCode") == edinet_code
                    and doc.get("docTypeCode") == DOC_TYPE_ANNUAL
                ):
                    return doc

            # API レート制限に配慮して少し待機
            time.sleep(0.1)

        return None

    def _get_documents_on_date(self, target_date: date) -> list[dict]:
        """指定日に提出されたすべての書類一覧を返す"""
        url = f"{EDINET_BASE}/documents.json"
        params = {
            "date": target_date.strftime("%Y-%m-%d"),
            "type": 2,                      # type=2: 書類一覧（メタデータ含む）
            "Subscription-Key": self.api_key,
        }

        try:
            response = self.session.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except (requests.RequestException, ValueError):
            return []
