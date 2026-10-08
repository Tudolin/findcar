"""Car-loan simulation (Tabela Price — fixed installments, the standard for auto loans in Brazil).

Rates are user-configurable defaults, not market quotes: ask your bank for the real CET.
IOF is approximated as 0.38% + 0.0082%/day (capped at 365 days ⇒ ≤ 3.38%) of the financed
amount, financed together with the loan, which is how banks usually charge it.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULTS = {"rate_month": 1.99, "down_pct": 30, "months": 48, "iof": True, "fees": 0}
TERMS = (12, 24, 36, 48, 60)


def iof_rate(months: int) -> float:
    days = min(365, months * 30)
    return 0.0038 + 0.000082 * days


def pmt(principal: float, rate_month_pct: float, months: int) -> float:
    if months <= 0:
        return principal
    i = rate_month_pct / 100
    if i == 0:
        return principal / months
    return principal * i / (1 - (1 + i) ** -months)


@dataclass
class Simulation:
    price: int
    down: int
    months: int
    rate_month: float
    financed: int  # principal + IOF + fees
    iof: int
    installment: int
    total_paid: int  # down + installments
    interest: int  # total_paid - price
    rate_year: float


def simulate(price: int, down: int, months: int, rate_month: float, with_iof: bool = True,
             fees: int = 0) -> Simulation:
    down = max(0, min(down, price))
    base = price - down
    iof = round(base * iof_rate(months)) if with_iof and base > 0 else 0
    financed = base + iof + max(0, fees)
    inst = round(pmt(financed, rate_month, months)) if financed > 0 else 0
    total = down + inst * months  # what is actually paid, with the rounded installment
    return Simulation(
        price=price, down=down, months=months, rate_month=rate_month, financed=round(financed),
        iof=iof, installment=inst, total_paid=total, interest=total - price,
        rate_year=round(((1 + rate_month / 100) ** 12 - 1) * 100, 1),
    )


def table(price: int, cfg: dict) -> list[Simulation]:
    down = round(price * cfg["down_pct"] / 100)
    return [simulate(price, down, n, cfg["rate_month"], cfg["iof"], cfg.get("fees", 0)) for n in TERMS]
