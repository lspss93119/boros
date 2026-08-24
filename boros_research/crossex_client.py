"""Read-only adapter for the local Arbitrage with CrossEx opportunity API."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .normalize import normalize_venue


DEFAULT_CROSSEX_BASE_URL = "http://127.0.0.1:6688"
DEFAULT_CROSSEX_TOKEN_FILE = Path("~/.boros-crossex/config/api-token").expanduser()
MONITORED_NOTIONALS = (10_000, 25_000, 50_000)

JsonRequester = Callable[[str, dict[str, str], dict[str, str]], Any]


def _required_mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be an object")
    return value


def _required_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be a non-empty string")
    return value.strip()


def _required_int(value: Any, context: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{context} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{context} must be >= {minimum}")
    return value


def _optional_number(value: Any, context: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{context} must be a finite number or null")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{context} must be a finite number or null") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{context} must be a finite number or null")
    return parsed


def _string_tuple(value: Any, context: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a string list")
    return tuple(_required_string(item, f"{context}[]") for item in value)


@dataclass(frozen=True)
class CrossExLeg:
    market_id: int
    venue: str
    crossex_venue: str
    crossex_symbol: str
    base: str
    mid_apr: float | None
    exec_apr: float | None


@dataclass(frozen=True)
class CrossExCosts:
    boros_taker_fee_usd: float | None
    boros_settle_fee_usd: float | None
    perp_entry_fees_usd: float | None
    perp_entry_slippage_usd: float | None
    perp_exit_fees_usd: float | None
    perp_exit_slippage_usd: float | None
    total_usd: float | None
    annualized_apr: float | None


@dataclass(frozen=True)
class CrossExCapital:
    boros_short_im_usd: float | None
    boros_long_im_usd: float | None
    perp_short_im_usd: float | None
    perp_long_im_usd: float | None
    short_leverage_max: float | None
    long_leverage_max: float | None


@dataclass(frozen=True)
class CrossExPair:
    base: str
    short_leg: CrossExLeg
    long_leg: CrossExLeg
    gross_spread_apr: float | None
    exec_spread_apr: float | None
    boros_impact_apr: float | None
    maker_leg: str | None
    costs: CrossExCosts
    capital_usd: float | None
    net_fixed_apr: float | None
    net_fixed_apr_on_capital: float | None
    effective_leverage: float | None
    est_profit_usd: float | None
    seconds_to_maturity: int
    reasons: tuple[str, ...]
    capital: CrossExCapital | None = None


@dataclass(frozen=True)
class CrossExGroup:
    token_id: int
    collateral: str
    collateral_price_usd: float | None
    maturity_timestamp: int
    seconds_to_maturity: int
    underlying: str
    pairs: tuple[CrossExPair, ...]
    warnings: tuple[str, ...]
    best_pair: CrossExPair | None = None


@dataclass(frozen=True)
class CrossExResponse:
    notional_usd: int
    as_of_timestamp: int
    groups: tuple[CrossExGroup, ...]
    warnings: tuple[str, ...]


def _normalize_leg(value: Any, context: str) -> CrossExLeg:
    raw = _required_mapping(value, context)
    return CrossExLeg(
        market_id=_required_int(raw.get("marketId"), f"{context}.marketId", minimum=1),
        venue=_required_string(raw.get("venue"), f"{context}.venue"),
        crossex_venue=normalize_venue(
            _required_string(raw.get("crossexVenue"), f"{context}.crossexVenue")
        ),
        crossex_symbol=_required_string(raw.get("crossexSymbol"), f"{context}.crossexSymbol"),
        base=_required_string(raw.get("base"), f"{context}.base"),
        mid_apr=_optional_number(raw.get("midApr"), f"{context}.midApr"),
        exec_apr=_optional_number(raw.get("execApr"), f"{context}.execApr"),
    )


def _normalize_costs(value: Any, context: str) -> CrossExCosts:
    raw = _required_mapping(value, context)
    return CrossExCosts(
        boros_taker_fee_usd=_optional_number(
            raw.get("borosTakerFeeUsd"), f"{context}.borosTakerFeeUsd"
        ),
        boros_settle_fee_usd=_optional_number(
            raw.get("borosSettleFeeUsd"), f"{context}.borosSettleFeeUsd"
        ),
        perp_entry_fees_usd=_optional_number(
            raw.get("perpEntryFeesUsd"), f"{context}.perpEntryFeesUsd"
        ),
        perp_entry_slippage_usd=_optional_number(
            raw.get("perpEntrySlippageUsd"), f"{context}.perpEntrySlippageUsd"
        ),
        perp_exit_fees_usd=_optional_number(
            raw.get("perpExitFeesUsd"), f"{context}.perpExitFeesUsd"
        ),
        perp_exit_slippage_usd=_optional_number(
            raw.get("perpExitSlippageUsd"), f"{context}.perpExitSlippageUsd"
        ),
        total_usd=_optional_number(raw.get("totalUsd"), f"{context}.totalUsd"),
        annualized_apr=_optional_number(raw.get("annualizedApr"), f"{context}.annualizedApr"),
    )


def _normalize_capital(value: Any, context: str) -> CrossExCapital:
    raw = _required_mapping(value, context)
    return CrossExCapital(
        boros_short_im_usd=_optional_number(raw.get("borosShortImUsd"), f"{context}.borosShortImUsd"),
        boros_long_im_usd=_optional_number(raw.get("borosLongImUsd"), f"{context}.borosLongImUsd"),
        perp_short_im_usd=_optional_number(raw.get("perpShortImUsd"), f"{context}.perpShortImUsd"),
        perp_long_im_usd=_optional_number(raw.get("perpLongImUsd"), f"{context}.perpLongImUsd"),
        short_leverage_max=_optional_number(raw.get("shortLeverageMax"), f"{context}.shortLeverageMax"),
        long_leverage_max=_optional_number(raw.get("longLeverageMax"), f"{context}.longLeverageMax"),
    )


def _normalize_pair(value: Any, context: str) -> CrossExPair:
    raw = _required_mapping(value, context)
    maker_leg = raw.get("makerLeg")
    if maker_leg is not None:
        maker_leg = _required_string(maker_leg, f"{context}.makerLeg")
    return CrossExPair(
        base=_required_string(raw.get("base"), f"{context}.base"),
        short_leg=_normalize_leg(raw.get("shortLeg"), f"{context}.shortLeg"),
        long_leg=_normalize_leg(raw.get("longLeg"), f"{context}.longLeg"),
        gross_spread_apr=_optional_number(raw.get("grossSpreadApr"), f"{context}.grossSpreadApr"),
        exec_spread_apr=_optional_number(raw.get("execSpreadApr"), f"{context}.execSpreadApr"),
        boros_impact_apr=_optional_number(raw.get("borosImpactApr"), f"{context}.borosImpactApr"),
        maker_leg=maker_leg,
        costs=_normalize_costs(raw.get("costs"), f"{context}.costs"),
        capital_usd=_optional_number(raw.get("capitalUsd"), f"{context}.capitalUsd"),
        net_fixed_apr=_optional_number(raw.get("netFixedApr"), f"{context}.netFixedApr"),
        net_fixed_apr_on_capital=_optional_number(
            raw.get("netFixedAprOnCapital"), f"{context}.netFixedAprOnCapital"
        ),
        effective_leverage=_optional_number(
            raw.get("effectiveLeverage"), f"{context}.effectiveLeverage"
        ),
        est_profit_usd=_optional_number(raw.get("estProfitUsd"), f"{context}.estProfitUsd"),
        seconds_to_maturity=_required_int(
            raw.get("secondsToMaturity"), f"{context}.secondsToMaturity", minimum=0
        ),
        reasons=_string_tuple(raw.get("reasons"), f"{context}.reasons"),
        capital=_normalize_capital(raw.get("capital"), f"{context}.capital"),
    )


def _normalize_group(value: Any, context: str) -> CrossExGroup:
    raw = _required_mapping(value, context)
    markets = raw.get("markets")
    if not isinstance(markets, list):
        raise ValueError(f"{context}.markets must be a list")
    pairs = raw.get("pairs")
    if not isinstance(pairs, list):
        raise ValueError(f"{context}.pairs must be a list")
    return CrossExGroup(
        token_id=_required_int(raw.get("tokenId"), f"{context}.tokenId", minimum=1),
        collateral=_required_string(raw.get("collateral"), f"{context}.collateral"),
        collateral_price_usd=_optional_number(
            raw.get("collateralPriceUsd"), f"{context}.collateralPriceUsd"
        ),
        maturity_timestamp=_required_int(raw.get("maturity"), f"{context}.maturity", minimum=1),
        seconds_to_maturity=_required_int(
            raw.get("secondsToMaturity"), f"{context}.secondsToMaturity", minimum=0
        ),
        underlying=_required_string(raw.get("underlying"), f"{context}.underlying"),
        pairs=tuple(_normalize_pair(item, f"{context}.pairs[{index}]") for index, item in enumerate(pairs)),
        warnings=_string_tuple(raw.get("warnings"), f"{context}.warnings"),
        best_pair=(
            None
            if raw.get("bestPair") is None
            else _normalize_pair(raw.get("bestPair"), f"{context}.bestPair")
        ),
    )


def normalize_opportunities_response(value: Any, *, requested_notional: int) -> CrossExResponse:
    """Validate and normalize the live ``{ok, data, meta}`` envelope."""
    if isinstance(requested_notional, bool) or not isinstance(requested_notional, int):
        raise ValueError("requested_notional must be an integer")
    envelope = _required_mapping(value, "CrossEx response")
    if envelope.get("ok") is not True:
        raise ValueError("CrossEx response.ok must be true")
    data = _required_mapping(envelope.get("data"), "CrossEx response.data")
    meta = _required_mapping(data.get("meta"), "CrossEx response.data.meta")
    actual_notional = _required_int(meta.get("notionalUsd"), "CrossEx response.meta.notionalUsd", minimum=1)
    if actual_notional != requested_notional:
        raise ValueError(
            f"CrossEx response notionalUsd {actual_notional} does not match requested {requested_notional}"
        )
    as_of_timestamp = _required_int(meta.get("asOfSec"), "CrossEx response.meta.asOfSec", minimum=1)
    groups = data.get("groups")
    if not isinstance(groups, list):
        raise ValueError("CrossEx response.data.groups must be a list")
    return CrossExResponse(
        notional_usd=actual_notional,
        as_of_timestamp=as_of_timestamp,
        groups=tuple(_normalize_group(item, f"CrossEx response.data.groups[{index}]") for index, item in enumerate(groups)),
        warnings=_string_tuple(data.get("warnings"), "CrossEx response.data.warnings"),
    )


def _default_request_json(url: str, params: dict[str, str], headers: dict[str, str]) -> Any:
    request_url = f"{url}?{urlencode(params)}"
    request = Request(request_url, method="GET", headers={"Accept": "application/json", **headers})
    try:
        with urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise RuntimeError(f"CrossEx GET returned HTTP {response.status}")
            return json.loads(response.read().decode("utf-8"))
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"CrossEx GET failed: {type(exc).__name__}") from exc


def _resolve_token(token: str | None, token_file: Path | None) -> str | None:
    if token is not None:
        return token or None
    from_env = os.environ.get("CROSSEX_API_TOKEN")
    if from_env:
        return from_env
    configured_path = token_file
    if configured_path is None:
        env_path = os.environ.get("CROSSEX_API_TOKEN_FILE")
        configured_path = Path(env_path).expanduser() if env_path else DEFAULT_CROSSEX_TOKEN_FILE
    try:
        value = configured_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError("unable to read CrossEx local API token file") from exc
    return value or None


class CrossExClient:
    """A GET-only client for the local CrossEx opportunities route."""

    def __init__(
        self,
        base_url: str = DEFAULT_CROSSEX_BASE_URL,
        *,
        token: str | None = None,
        token_file: Path | None = None,
        request_json: JsonRequester | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = _resolve_token(token, token_file)
        self._request_json = request_json or _default_request_json

    def fetch(self, notional_usd: int) -> CrossExResponse:
        if isinstance(notional_usd, bool) or not isinstance(notional_usd, int) or notional_usd <= 0:
            raise ValueError("notional_usd must be a positive integer")
        params = {
            "notionalUsd": str(notional_usd),
            "borosEntry": "market",
            "entryMode": "both-market",
            "exitMode": "close",
        }
        headers = {"x-arb-token": self._token} if self._token else {}
        try:
            payload = self._request_json(
                f"{self.base_url}/api/opportunities",
                params,
                headers,
            )
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError("CrossEx opportunity GET failed") from exc
        return normalize_opportunities_response(payload, requested_notional=notional_usd)
