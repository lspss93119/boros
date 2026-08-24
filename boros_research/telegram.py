"""Chinese Telegram presentation and a small HTTPS sender."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.request import Request, urlopen

from .crossex_client import CrossExPair
from .live_benchmark import LiveBenchmarkResult


@dataclass(frozen=True)
class SizeMessage:
    notional_usd: int
    pair: CrossExPair | None
    benchmark: LiveBenchmarkResult | None


@dataclass(frozen=True)
class AlertMessage:
    severity: str | None
    asset: str
    short_venue: str
    long_venue: str
    maturity: date
    remaining_days: int
    primary: SizeMessage
    sizes: tuple[SizeMessage, ...]
    live_detected_minutes: int
    warnings: tuple[str, ...] = ()
    p95_live_detected_minutes: int | None = None
    high_yield_classification: str | None = None
    reference: SizeMessage | None = None
    dte_seconds: int | None = None


def _apr(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def _display_venue(value: str) -> str:
    return {
        "HYPERLIQUID": "Hyperliquid",
        "BINANCE": "Binance",
        "BYBIT": "Bybit",
        "GATE": "Gate",
        "OKX": "OKX",
        "KRAKEN": "Kraken",
    }.get(value, value)


def _percentile(value: float | None) -> str:
    return "—" if value is None else f"P{value:.1f}"


def _money(value: float | None, *, signed: bool = False) -> str:
    if value is None or not math.isfinite(float(value)):
        return "—"
    if signed:
        return f"{value:+,.0f}"
    return f"{value:,.0f}"


def holding_period_return(pair: CrossExPair | None) -> float | None:
    """Return the modelled capital return through maturity, as a fraction."""
    if pair is None:
        return None
    if (
        pair.est_profit_usd is not None
        and pair.capital_usd is not None
        and math.isfinite(float(pair.est_profit_usd))
        and math.isfinite(float(pair.capital_usd))
        and pair.capital_usd > 0
    ):
        return pair.est_profit_usd / pair.capital_usd
    if (
        pair.net_fixed_apr_on_capital is not None
        and math.isfinite(float(pair.net_fixed_apr_on_capital))
        and pair.seconds_to_maturity >= 0
    ):
        return pair.net_fixed_apr_on_capital * pair.seconds_to_maturity / (365 * 86400)
    return None


def _pair_return(pair: CrossExPair | None) -> str:
    return _apr(holding_period_return(pair))


def _size_line(size: SizeMessage) -> str:
    label = f"${size.notional_usd:,.0f}"
    if size.pair is None:
        return f"{label}｜CrossEx 資料不足"
    if size.pair.exec_spread_apr is None:
        return f"{label}｜不可完整執行"
    percentile = "—" if size.benchmark is None else f"90D {_percentile(size.benchmark.percentile_90d)}"
    net_apr = _apr(size.pair.net_fixed_apr_on_capital)
    return f"{label}｜利差 {_apr(size.pair.exec_spread_apr)}｜{percentile}｜淨APR {net_apr}"


def _high_yield_size_line(size: SizeMessage) -> str:
    label = f"${size.notional_usd:,.0f}"
    if size.pair is None:
        return f"{label}｜—"
    return f"{label}｜淨資本 APR：{_apr(size.pair.net_fixed_apr_on_capital)}"


def _duration_text(minutes: int) -> str:
    minutes = max(0, minutes)
    if minutes < 60:
        return f"{minutes} 分鐘"
    hours, remainder = divmod(minutes, 60)
    return f"{hours} 小時" if remainder == 0 else f"{hours} 小時 {remainder} 分"


def format_opportunity_message(alert: AlertMessage) -> str:
    historical_severity = alert.severity if alert.severity in {"NORMAL", "URGENT"} else None
    high_yield_classification = alert.high_yield_classification
    if high_yield_classification is None and alert.severity in {"HIGH_YIELD", "EXCEPTIONAL"}:
        high_yield_classification = alert.severity
    if historical_severity is None and high_yield_classification is None:
        raise ValueError("an historical or high-yield classification is required")

    if high_yield_classification is not None:
        historical_text = ""
        if historical_severity is not None:
            historical_value = (
                None
                if alert.primary.benchmark is None
                else alert.primary.benchmark.percentile_90d
            )
            historical_text = f"｜歷史 {_percentile(historical_value)}"
        heading = (
            "🔥 EXCEPTIONAL" if high_yield_classification == "EXCEPTIONAL" else "🔥 HIGH YIELD"
        ) + historical_text
        primary_pair = alert.primary.pair
        reference_pair = None if alert.reference is None else alert.reference.pair
        dte_seconds = alert.dte_seconds
        if dte_seconds is None and primary_pair is not None:
            dte_seconds = primary_pair.seconds_to_maturity
        dte_days = 0 if dte_seconds is None else max(0, math.ceil(dte_seconds / 86400))
        lines = [
            heading,
            "",
            f"{alert.asset}｜{_display_venue(alert.short_venue)} → {_display_venue(alert.long_venue)}",
            f"到期：{alert.maturity.isoformat()}｜DTE {dte_days} 天",
            "",
            "Limit + Hedge（掛單成交未保證）",
            f"淨資本 APR：{_apr(None if primary_pair is None else primary_pair.net_fixed_apr_on_capital)}",
            "",
            "立即成交參考",
            f"淨資本 APR：{_apr(None if reference_pair is None else reference_pair.net_fixed_apr_on_capital)}",
            "",
            f"單期資本報酬：{_pair_return(primary_pair)}",
            f"預估淨收益：${_money(None if primary_pair is None else primary_pair.est_profit_usd, signed=True)}",
            f"模型最低資本：${_money(None if primary_pair is None else primary_pair.capital_usd)}",
            f"有效槓桿：{('—' if primary_pair is None or primary_pair.effective_leverage is None else f'{primary_pair.effective_leverage:.2f}x')}",
            "",
            "容量",
            *(_high_yield_size_line(size) for size in alert.sizes),
        ]
        primary_benchmark = alert.primary.benchmark
        if primary_benchmark is not None:
            lines.extend(
                [
                    "",
                    f"90D：{_percentile(primary_benchmark.percentile_90d)}",
                    f"30D：{_percentile(primary_benchmark.percentile_30d)}",
                    f"全歷史：{_percentile(primary_benchmark.percentile_lifetime)}",
                ]
            )
        return "\n".join(lines)

    assert historical_severity is not None
    heading = "🟠 Boros 套利｜歷史前 5%" if historical_severity == "NORMAL" else "🔴 Boros 極佳套利｜歷史前 1%"
    primary_pair = alert.primary.pair
    primary_benchmark = alert.primary.benchmark
    lines = [
        heading,
        "",
        f"{alert.asset}｜{_display_venue(alert.short_venue)} → {_display_venue(alert.long_venue)}",
        f"到期日：{alert.maturity.isoformat()}",
        f"剩餘天數：{max(0, alert.remaining_days)} 天",
        "",
        f"【主要條件｜${alert.primary.notional_usd:,.0f}】",
        f"可成交利差：{_apr(None if primary_pair is None else primary_pair.exec_spread_apr)}",
        f"90天歷史排名：{_percentile(None if primary_benchmark is None else primary_benchmark.percentile_90d)}",
        f"淨資本年化：{_apr(None if primary_pair is None else primary_pair.net_fixed_apr_on_capital)}",
        f"模型最低資本：${_money(None if primary_pair is None else primary_pair.capital_usd)}",
        f"預估淨收益：${_money(None if primary_pair is None else primary_pair.est_profit_usd, signed=True)}",
        f"預估單次資本報酬：{_pair_return(primary_pair)}",
        f"有效槓桿：{('—' if primary_pair is None else ('—' if primary_pair.effective_leverage is None else f'{primary_pair.effective_leverage:.2f}x'))}",
        "",
        "【可執行規模】",
        *(_size_line(size) for size in alert.sizes),
    ]
    if primary_benchmark is not None:
        lines.extend(
            [
                "",
                "【歷史比較】",
                f"30天：{_percentile(primary_benchmark.percentile_30d)}",
                f"90天：{_percentile(primary_benchmark.percentile_90d)}",
                f"全歷史：{_percentile(primary_benchmark.percentile_lifetime)}",
                f"比較區間：DTE {primary_benchmark.dte_bucket.replace('-', '–')} 天",
                f"樣本：{primary_benchmark.sample_count_90d:,} 筆",
            ]
        )
    if primary_pair is not None:
        costs = primary_pair.costs
        boros_fee = None
        if costs.boros_taker_fee_usd is not None and costs.boros_settle_fee_usd is not None:
            boros_fee = costs.boros_taker_fee_usd + costs.boros_settle_fee_usd
        perp_fee = None
        if costs.perp_entry_fees_usd is not None and costs.perp_exit_fees_usd is not None:
            perp_fee = costs.perp_entry_fees_usd + costs.perp_exit_fees_usd
        perp_slippage = None
        if costs.perp_entry_slippage_usd is not None and costs.perp_exit_slippage_usd is not None:
            perp_slippage = costs.perp_entry_slippage_usd + costs.perp_exit_slippage_usd
        lines.extend(
            [
                "",
                "【CrossEx 成本】",
                f"總成本：${_money(costs.total_usd)}",
                f"Boros 費用：${_money(boros_fee)}",
                f"Perp 進出費用：${_money(perp_fee)}",
                f"Perp 滑價：${_money(perp_slippage)}",
            ]
        )
    p95_minutes = (
        alert.live_detected_minutes
        if alert.p95_live_detected_minutes is None
        else alert.p95_live_detected_minutes
    )
    if p95_minutes > 0:
        lines.extend(["", f"前 5% 已持續：{_duration_text(p95_minutes)}"])
        if historical_severity == "URGENT":
            lines.append("本機狀態：剛進入歷史前 1%")
        else:
            lines.append("本機狀態：已在歷史前 5%")
    else:
        status = "5%" if historical_severity == "NORMAL" else "1%"
        lines.extend(["", f"本機狀態：剛進入歷史前 {status}"])
    return "\n".join(lines)


class TelegramClient:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        timeout: float = 15.0,
        request_json: Callable[[str, dict[str, Any], float], Any] | None = None,
    ) -> None:
        if not bot_token or not chat_id:
            raise ValueError("Telegram credentials are required")
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._timeout = timeout
        self._request_json = request_json

    def send_message(self, text: str) -> None:
        if not text:
            raise ValueError("Telegram message must not be empty")
        payload = {"chat_id": self._chat_id, "text": text}
        if self._request_json is not None:
            # Keep test/instrumentation seams sanitized by construction.
            response = self._request_json(
                "https://api.telegram.org/bot<redacted>/sendMessage",
                payload,
                self._timeout,
            )
        else:
            url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
            request = Request(
                url,
                method="POST",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            try:
                with urlopen(request, timeout=self._timeout) as response_obj:
                    response = json.loads(response_obj.read().decode("utf-8"))
            except Exception as exc:
                raise RuntimeError("Telegram send failed") from exc
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise RuntimeError("Telegram API rejected the message")


def telegram_test_message() -> str:
    return "✅ Boros 監控通知測試成功\n\nTelegram 連線正常。\n這是一則測試訊息，不代表目前存在套利機會。"
