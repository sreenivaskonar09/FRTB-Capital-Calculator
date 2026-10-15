"""Project-wide constants and paths."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
SQL_DIR = ROOT / "sql"
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "output"
FRED_DIR = ROOT / "refs" / "fred"
DB_PATH = DATA_DIR / "frtb_sbm.db"

REPORTING_CCY = "USD"
CURRENCIES = ["USD", "EUR", "GBP", "CHF"]
# GIRR delta tenor vertices, MAR21.8(1)
TENORS = [0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0]
# GIRR vega grid (option maturity and residual underlying maturity), MAR21.8(4)
VEGA_TENORS = [0.5, 1.0, 3.0, 5.0, 10.0]
SCENARIOS = ["high", "medium", "low"]
SEED = 20261015
