"""Tests for fpl_core.money.

Prices are integer tenths of a million throughout: 101 == £10.1m. Float pounds
accumulate rounding error that surfaces much later, inside the optimiser's
budget constraint, where it is miserable to diagnose.
"""

from __future__ import annotations

import pytest
from fpl_core.money import Price, format, from_api, from_csv, sell_price, to_millions


class TestSellPrice:
    """Sell price = purchase + half of any profit, rounded DOWN to 0.1m.

    Losses are returned in full.
    """

    @pytest.mark.parametrize(
        ("purchase", "current", "expected", "why"),
        [
            (100, 100, 100, "no price change"),
            (100, 95, 95, "loss returned in full"),
            (100, 90, 90, "larger loss returned in full"),
            (100, 102, 101, "0.2 profit halves cleanly to 0.1"),
            (100, 101, 100, "0.1 profit rounds down to nothing"),
            (100, 105, 102, "0.5 profit -> 0.2"),
            (100, 103, 101, "0.3 profit -> 0.1"),
            (100, 104, 102, "0.4 profit -> 0.2"),
            (45, 52, 48, "cheap enabler, 0.7 profit -> 0.3"),
            (150, 163, 156, "premium, 1.3 profit -> 0.6"),
        ],
    )
    def test_table(self, purchase: int, current: int, expected: int, why: str) -> None:
        assert sell_price(Price(purchase), Price(current)) == expected, why

    def test_single_tick_rise_gains_nothing(self) -> None:
        """The case that breaks squad plans.

        A 0.1m rise returns no profit on sale. An optimiser assuming otherwise
        produces plans that are infeasible by exactly 0.1m.
        """
        assert sell_price(Price(100), Price(101)) == 100

    @pytest.mark.parametrize("rise", range(0, 21))
    def test_never_exceeds_market_price(self, rise: int) -> None:
        purchase = Price(100)
        current = Price(100 + rise)
        assert sell_price(purchase, current) <= current

    @pytest.mark.parametrize("rise", range(0, 21))
    def test_never_below_purchase_when_risen(self, rise: int) -> None:
        purchase = Price(100)
        current = Price(100 + rise)
        assert sell_price(purchase, current) >= purchase

    @pytest.mark.parametrize("drop", range(1, 21))
    def test_loss_is_not_halved(self, drop: int) -> None:
        purchase = Price(100)
        current = Price(100 - drop)
        assert sell_price(purchase, current) == current

    def test_monotonic_in_current_price(self) -> None:
        """A higher market price never yields a lower sell price."""
        purchase = Price(100)
        prices = [sell_price(purchase, Price(p)) for p in range(90, 130)]
        assert prices == sorted(prices)


class TestConversions:
    def test_from_api_is_already_tenths(self) -> None:
        assert from_api(101) == 101

    @pytest.mark.parametrize(
        ("millions", "expected"),
        [(3.8, 38), (4.0, 40), (4.5, 45), (10.1, 101), (10.5, 105), (15.0, 150)],
    )
    def test_from_csv(self, millions: float, expected: int) -> None:
        assert from_csv(millions) == expected

    @pytest.mark.parametrize("tenths", range(38, 151))
    def test_from_csv_round_trips_every_valid_price(self, tenths: int) -> None:
        """Float representation of x.1 values is inexact.

        10.1 * 10 is 100.99999999999999 in IEEE 754. Exercise every price a
        player can realistically hold to confirm the rounding holds.
        """
        millions = tenths / 10
        assert from_csv(millions) == tenths

    def test_to_millions(self) -> None:
        assert to_millions(Price(101)) == pytest.approx(10.1)

    @pytest.mark.parametrize(
        ("tenths", "expected"),
        [(38, "£3.8m"), (100, "£10.0m"), (101, "£10.1m"), (150, "£15.0m")],
    )
    def test_format(self, tenths: int, expected: str) -> None:
        assert format(Price(tenths)) == expected
