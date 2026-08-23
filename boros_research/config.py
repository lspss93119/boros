from pathlib import Path

HISTORICAL_BASE_URL = "https://historical-data.boros.finance"
BOROS_OPEN_API_BASE_URL = "https://api-boros.pendle.finance/apis/v1"

SAMPLE_INTERVAL_SEC = 5 * 60
MAX_SNAPSHOT_AGE_SEC = 15 * 60
NOTIONALS_USD = (1_000, 2_000, 5_000, 10_000, 25_000, 50_000)

CROSSEX_VENUES = frozenset(
    {"BINANCE", "BYBIT", "GATE", "OKX", "KRAKEN", "HYPERLIQUID"}
)

DTE_BUCKETS = (
    (0, 7, "0-7"),
    (8, 21, "8-21"),
    (22, 45, "22-45"),
    (46, 90, "46-90"),
    (91, None, "91+"),
)

RAW_BOROS_DIR = Path("raw_boros")
RAW_API_DIR = Path("raw_api")
RAW_INDICATORS_DIR = Path("raw_indicators")
PARQUET_DIR = Path("data/parquet")
DUCKDB_PATH = Path("data/boros.duckdb")
