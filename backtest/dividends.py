"""Cash dividends from issuer implementation notices, not rounded quote gaps."""

from datetime import date
from decimal import Decimal


# https://www.jlgsgl.com/storage/2024/ad/572a1b5214d977fbb05a53f44d18a2.pdf
# https://www.jlgsgl.com/storage/2025/e2/886f9b1d17717740e1409e7576514c.pdf
# https://www.jlgsgl.com/storage/2026/d6/62506cafac99e6fdb5122aa109b964.pdf
DIVIDENDS: dict[date, Decimal] = {
    date(2024, 7, 4): Decimal("0.09"),
    date(2025, 7, 3): Decimal("0.086"),
    date(2026, 7, 6): Decimal("0.13"),
}
