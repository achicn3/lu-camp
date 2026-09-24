"""整組退規則（純函式；docs/40 §8、裁示 5）。"""

import pytest

from app.modules.returns.bundle_policy import BundleGroupMembers, bundles_to_return
from app.shared.exceptions import ReturnLineInvalid

TENT, GAS, CHAIR = 1, 2, 3
GROUP = BundleGroupMembers(group_id=7, members=((TENT, 1), (GAS, 2)))
QTY = {TENT: 1, GAS: 3, CHAIR: 1}


def test_returning_unrelated_lines_touches_no_bundle() -> None:
    assert bundles_to_return({CHAIR: 1}, {}, QTY, [GROUP]) == []


def test_touching_a_bundled_line_at_all_requires_the_whole_group() -> None:
    """同一行混了組內／組外的件時，按行平均的退價對組外那件也不準（Codex 審查）：一律整組整行退。"""
    with pytest.raises(ReturnLineInvalid, match="整組"):
        bundles_to_return({GAS: 1}, {}, QTY, [GROUP])


def test_touching_bundled_units_requires_the_whole_group() -> None:
    for requested, previous in [({TENT: 1}, {}), ({GAS: 2}, {}), ({GAS: 1}, {GAS: 1})]:
        with pytest.raises(ReturnLineInvalid, match="整組"):
            bundles_to_return(requested, previous, QTY, [GROUP])


def test_whole_group_returns_every_remaining_unit_of_its_lines() -> None:
    """退價按行平均：組內的行要整行退回（含組外的那罐），否則留下的那罐拿到折扣。"""
    assert bundles_to_return({TENT: 1, GAS: 3}, {}, QTY, [GROUP]) == [7]
    with pytest.raises(ReturnLineInvalid, match="整組"):
        bundles_to_return({TENT: 1, GAS: 2}, {}, QTY, [GROUP])


def test_units_returned_before_the_bundle_existed_are_not_required_again() -> None:
    assert bundles_to_return({TENT: 1, GAS: 2}, {GAS: 1}, QTY, [GROUP]) == [7]


def test_groups_sharing_a_line_are_returned_together() -> None:
    other = BundleGroupMembers(group_id=8, members=((CHAIR, 1), (GAS, 1)))
    with pytest.raises(ReturnLineInvalid):
        bundles_to_return({TENT: 1, GAS: 3}, {}, QTY, [GROUP, other])
    assert bundles_to_return({TENT: 1, CHAIR: 1, GAS: 3}, {}, QTY, [GROUP, other]) == [7, 8]
