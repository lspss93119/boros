from __future__ import annotations

from datetime import date

from boros_research.crossex_client import (
    CrossExCosts,
    CrossExLeg,
    CrossExPair,
)
from boros_research.live_benchmark import LiveBenchmarkResult
from boros_research.telegram import (
    AlertMessage,
    SizeMessage,
    TelegramClient,
    format_opportunity_message,
)


def pair(notional: float, spread: float = 0.071):
    return CrossExPair(
        base="HYPE",
        short_leg=CrossExLeg(190, "Hyperliquid", "HYPERLIQUID", "HYPEUSDT", "HYPE", 0.11, 0.109),
        long_leg=CrossExLeg(192, "Bybit", "BYBIT", "HYPEUSDT", "HYPE", 0.06, 0.062),
        gross_spread_apr=spread + 0.003,
        exec_spread_apr=spread,
        boros_impact_apr=0.003,
        maker_leg=None,
        costs=CrossExCosts(1.0, 0.8, 2.0, 0.5, 1.0, 0.2, 5.5, 0.001),
        capital_usd=6_000.0,
        net_fixed_apr=0.06,
        net_fixed_apr_on_capital=0.0642,
        effective_leverage=1.666,
        est_profit_usd=384.0,
        seconds_to_maturity=2_000_000,
        reasons=(),
    )


def benchmark(percentile=96.8):
    return LiveBenchmarkResult(
        candidate_id="hype",
        benchmark_level="dte",
        dte_bucket="22-45",
        percentile_30d=94.2,
        percentile_90d=percentile,
        percentile_lifetime=91.5,
        sample_count_30d=100,
        sample_count_90d=4_821,
        sample_count_lifetime=10_000,
        historical_max_timestamp=1_800_000_000,
        benchmark_age_seconds=3_600,
        benchmark_fresh=True,
    )


def test_chinese_message_contains_precise_economic_labels_and_no_nulls():
    message = format_opportunity_message(
        AlertMessage(
            severity="NORMAL",
            asset="HYPE",
            short_venue="HYPERLIQUID",
            long_venue="BYBIT",
            maturity=date(2026, 9, 25),
            remaining_days=32,
            primary=SizeMessage(10_000, pair(10_000), benchmark()),
            sizes=(
                SizeMessage(10_000, pair(10_000), benchmark()),
                SizeMessage(25_000, pair(25_000, 0.068), benchmark(95.2)),
                SizeMessage(50_000, None, None),
            ),
            live_detected_minutes=20,
            warnings=("fee tier VIP0 assumption",),
        )
    )

    assert "HYPE" in message
    assert "Hyperliquid → Bybit" in message
    assert "2026-09-25" in message
    assert "剩餘天數：32 天" in message
    assert "$10,000" in message and "$25,000" in message and "$50,000" in message
    assert "可成交利差：7.10%" in message
    assert "90天歷史排名：P96.8" in message
    assert "90D P96.8" in message
    assert "淨資本年化" in message
    assert "模型最低資本" in message
    assert "預估淨收益" in message
    assert "預估單次資本報酬" in message
    assert "樣本：4,821 筆" in message
    assert "DTE 22–45 天" in message
    assert "總成本" in message
    assert "本機已連續偵測：20 分鐘" in message
    assert "VIP0" in message
    assert "None" not in message
    assert "null" not in message
    assert "0.0642" not in message
    assert "6.42%" in message
    assert "不可完整執行" in message or "CrossEx 資料不足" in message


def test_telegram_client_posts_once_without_leaking_token():
    calls = []

    def requester(url, payload, timeout):
        calls.append((url, payload, timeout))
        return {"ok": True, "result": {"message_id": 1}}

    client = TelegramClient("secret-token", "chat-id", request_json=requester)
    client.send_message("測試")

    assert calls == [
        (
            "https://api.telegram.org/bot<redacted>/sendMessage",
            {"chat_id": "chat-id", "text": "測試"},
            15.0,
        )
    ]
