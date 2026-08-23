from boros_research.config import (
    CROSSEX_VENUES,
    DTE_BUCKETS,
    MAX_SNAPSHOT_AGE_SEC,
    NOTIONALS_USD,
    SAMPLE_INTERVAL_SEC,
)


def test_phase1_research_policy_is_explicit():
    assert SAMPLE_INTERVAL_SEC == 300
    assert MAX_SNAPSHOT_AGE_SEC == 900
    assert NOTIONALS_USD == (1_000, 2_000, 5_000, 10_000, 25_000, 50_000)
    assert {"BINANCE", "BYBIT", "GATE", "OKX", "KRAKEN", "HYPERLIQUID"} <= CROSSEX_VENUES
    assert DTE_BUCKETS == (
        (0, 7, "0-7"),
        (8, 21, "8-21"),
        (22, 45, "22-45"),
        (46, 90, "46-90"),
        (91, None, "91+"),
    )
