#!/usr/bin/env python3
"""
J-Quants API クライアント
現在株価（最新終値）を証券コードから自動取得する

認証の優先順位:
  1. JQUANTS_API_KEY       → APIキーとして Authorization ヘッダーに設定
  2. JQUANTS_REFRESH_TOKEN → リフレッシュトークンから idToken を取得
  3. JQUANTS_MAIL_ADDRESS + JQUANTS_PASSWORD → メール・パスワードから認証
"""

import os
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

JQUANTS_BASE = "https://api.jquants.com/v1"


class JQuantsClient:

    def __init__(self):
        # 優先順位順に認証情報を読み込む
        self._api_key: str | None = os.getenv("JQUANTS_API_KEY") or None
        self._refresh_token: str | None = os.getenv("JQUANTS_REFRESH_TOKEN") or None
        self.mail = os.getenv("JQUANTS_MAIL_ADDRESS")
        self.password = os.getenv("JQUANTS_PASSWORD")
        self._id_token: str | None = None
        self.session = requests.Session()

        if self._api_key:
            print("  J-Quants: APIキー認証を使用します")
        elif self._refresh_token:
            print("  J-Quants: リフレッシュトークン認証を使用します")
        elif self.mail and self.password:
            print("  J-Quants: メール・パスワード認証を使用します")
        else:
            raise EnvironmentError(
                "J-Quants の認証情報が設定されていません。\n"
                ".env に JQUANTS_API_KEY を設定してください。"
            )

    # ── 公開 API ─────────────────────────────────────────────────────────

    def get_current_price(self, ticker: str) -> float | None:
        """
        証券コードの最新終値を返す。
        取得できない場合は None を返す。
        """
        self._ensure_authenticated()

        from_date = (date.today() - timedelta(days=14)).strftime("%Y%m%d")
        to_date = date.today().strftime("%Y%m%d")

        # J-Quants は 4桁と5桁（末尾0付き）の両形式が存在する
        for code in [ticker, ticker + "0"]:
            price = self._fetch_latest_close(code, from_date, to_date)
            if price is not None:
                return price

        return None

    def get_company_info(self, ticker: str) -> dict | None:
        """証券コードから会社情報（会社名・業種等）を返す"""
        self._ensure_authenticated()
        url = f"{JQUANTS_BASE}/listed/info"
        params = {"code": ticker}

        try:
            res = self.session.get(url, params=params, headers=self._auth_headers(), timeout=15)
            res.raise_for_status()
            infos = res.json().get("info", [])
            return infos[0] if infos else None
        except (requests.RequestException, ValueError, IndexError):
            return None

    # ── 認証 ─────────────────────────────────────────────────────────────

    def _ensure_authenticated(self):
        """APIキー認証の場合はスキップ、それ以外は idToken を取得する"""
        if self._api_key:
            return  # APIキーがあれば idToken は不要
        if not self._id_token:
            self._authenticate()

    def _auth_headers(self) -> dict:
        """認証ヘッダーを返す（APIキー / idToken どちらにも対応）"""
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {"Authorization": f"Bearer {self._id_token}"}

    def _authenticate(self):
        """
        優先順位:
          A. JQUANTS_REFRESH_TOKEN が設定済み → idToken 取得
          B. メール・パスワード → refreshToken → idToken 取得
        """
        if not self._refresh_token:
            # パターンB: メール・パスワードから refreshToken を取得
            res = self.session.post(
                f"{JQUANTS_BASE}/token/auth_user",
                json={"mailaddress": self.mail, "password": self.password},
                timeout=15,
            )
            if res.status_code != 200:
                raise RuntimeError(
                    f"J-Quants 認証失敗: {res.status_code} {res.text}\n"
                    "メール・パスワードが正しくない場合は JQUANTS_API_KEY を .env に設定してください。"
                )
            self._refresh_token = res.json().get("refreshToken")
            if not self._refresh_token:
                raise RuntimeError("J-Quants: refreshToken の取得に失敗しました。")

        # パターンA・B共通: refreshToken → idToken
        res = self.session.post(
            f"{JQUANTS_BASE}/token/auth_refresh",
            params={"refreshtoken": self._refresh_token},
            timeout=15,
        )
        res.raise_for_status()
        self._id_token = res.json().get("idToken")
        if not self._id_token:
            raise RuntimeError("J-Quants: idToken の取得に失敗しました。")

    # ── 内部メソッド ──────────────────────────────────────────────────────

    def _fetch_latest_close(self, code: str, from_date: str, to_date: str) -> float | None:
        """指定期間の日次株価を取得し、最新の終値を返す"""
        url = f"{JQUANTS_BASE}/prices/daily_quotes"
        params = {"code": code, "from": from_date, "to": to_date}

        try:
            res = self.session.get(url, params=params, headers=self._auth_headers(), timeout=15)
            if res.status_code == 404:
                return None
            res.raise_for_status()
            quotes = res.json().get("daily_quotes", [])
            if not quotes:
                return None
            latest = sorted(quotes, key=lambda q: q["Date"], reverse=True)[0]
            return float(latest.get("Close") or latest.get("AdjustmentClose") or 0) or None
        except (requests.RequestException, ValueError, KeyError):
            return None
