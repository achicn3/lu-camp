"""販售籃分配規則：後分配先回，報表成本與庫存回補同一個順序。"""

from decimal import Decimal
from itertools import pairwise

import pytest

from app.modules.sales.bulk_allocation import returned_cost, returned_split


def test_split_fills_latest_allocation_first() -> None:
    assert returned_split([10, 2], 0) == [0, 0]
    assert returned_split([10, 2], 1) == [0, 1]
    assert returned_split([10, 2], 3) == [1, 2]
    assert returned_split([10, 2], 12) == [10, 2]


@pytest.mark.parametrize("total", [-1, 13])
def test_split_rejects_out_of_range(total: int) -> None:
    with pytest.raises(ValueError):
        returned_split([10, 2], total)


def test_cost_follows_the_same_order() -> None:
    allocations = [(10, Decimal(50)), (2, Decimal(16))]
    assert returned_cost(allocations, 0) == 0
    assert returned_cost(allocations, 3) == 16 + 5
    assert returned_cost(allocations, 12) == 66


def test_cost_is_path_independent() -> None:
    """三次退 1 件與一次退 3 件，累計成本相同（差額法的前提）。"""
    allocations = [(3, Decimal(10)), (3, Decimal(20))]
    steps = [returned_cost(allocations, n) for n in range(7)]
    assert steps[-1] == 30
    assert all(b >= a for a, b in pairwise(steps))
