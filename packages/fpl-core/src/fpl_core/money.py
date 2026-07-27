from typing import NewType

Price = NewType("Price", int)


def from_api(now_cost: int) -> Price:
    """FPL API gives tenths already."""
    return Price(now_cost)


def from_csv(value: float) -> Price:
    """Core insights gives millions as float: 10.1 -> 101."""
    return Price(round(value * 10))


def to_millions(p: Price) -> float:
    return p / 10


def format(p: Price) -> str:
    return f"£{p / 10:.1f}m"
