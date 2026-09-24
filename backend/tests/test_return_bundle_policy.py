"""整組退規則（純函式；docs/40 §8、裁示 5）。"""

import pytest

from app.modules.returns.bundle_policy import BundleGroupMembers, bundles_to_return
from app.shared.exceptions import ReturnLineInvalid

TENT, GAS, CHAIR = 1, 2, 3
GROUP = BundleGroupMembers(group_id=7, members=((TENT, 1), (GAS, 2)))
QTY = {TENT: 1, GAS: 3, CHAIR: 1}


def test_returning_unrelated_lines_touches_no_bundle() -> None:
    assert bundles_to_return({CHAIR: 1}, {}, QTY, [GROUP]) == []


def test_unbundled_units_go_first() -> None:
    assert bundles_to_return({GAS: 1}, {}, QTY, [GROUP]) == []


def test_touching_bundled_units_requires_the_whole_group() -> None:
    for requested, previous in [({TENT: 1}, {}), ({GAS: 2}, {}), ({GAS: 1}, {GAS: 1})]:
        with pytest.raises(ReturnLineInvalid, match="整組"):
            bundles_to_return(requested, previous, QTY, [GROUP])


def test_whole_group_is_returned() -> None:
    assert bundles_to_return({TENT: 1, GAS: 2}, {}, QTY, [GROUP]) == [7]
    assert bundles_to_return({TENT: 1, GAS: 3}, {}, QTY, [GROUP]) == [7]


def test_two_groups_sharing_a_line_need_their_combined_units() -> None:
    other = BundleGroupMembers(group_id=8, members=((CHAIR, 1), (GAS, 1)))
    qty = {TENT: 1, GAS: 3, CHAIR: 1}
    with pytest.raises(ReturnLineInvalid):
        bundles_to_return({TENT: 1, CHAIR: 1, GAS: 2}, {}, qty, [GROUP, other])
    assert bundles_to_return({TENT: 1, CHAIR: 1, GAS: 3}, {}, qty, [GROUP, other]) == [7, 8]
