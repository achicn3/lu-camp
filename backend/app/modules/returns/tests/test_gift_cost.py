"""贈品退回成本分攤的純金額規則。"""

from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest

from app.modules.returns.repository import GiftReturnQuantity
from app.modules.returns.service import GiftReturnAdjustment, ReturnsService, _gift_return_cost
from app.modules.sales.service import GiftLineSnapshot
from app.shared.enums import TenderType


def test_gift_return_cost_uses_cumulative_difference_rounding() -> None:
    assert _gift_return_cost(Decimal(1000), line_qty=3, prior_qty=0, period_qty=1) == Decimal(333)
    assert _gift_return_cost(Decimal(1000), line_qty=3, prior_qty=1, period_qty=1) == Decimal(334)
    assert _gift_return_cost(Decimal(1000), line_qty=3, prior_qty=2, period_qty=1) == Decimal(333)


def test_gift_return_cost_is_zero_when_snapshot_is_unknown() -> None:
    assert _gift_return_cost(None, line_qty=2, prior_qty=0, period_qty=1) == Decimal(0)


@pytest.mark.asyncio
async def test_gift_report_filters_candidates_before_quantity_aggregation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Mock()
    repo.period_return_sale_line_ids = AsyncMock(return_value=[11, 12])
    repo.return_quantities_for_sale_line_ids = AsyncMock(
        return_value=[GiftReturnQuantity(sale_line_id=12, prior_qty=1, period_qty=1)]
    )

    sales = Mock()
    sales.gift_line_snapshots = AsyncMock(
        return_value={
            12: GiftLineSnapshot(
                reason_id=3,
                reason_name="活動贈品",
                description="杯子",
                qty=2,
                retail_unit_price=Decimal(300),
                cost=Decimal(100),
            )
        }
    )
    monkeypatch.setattr("app.modules.returns.service.SalesService", lambda _session: sales)

    service = ReturnsService.__new__(ReturnsService)
    service._session = Mock()
    service._repo = repo

    result = await service.gift_return_adjustments(
        store_id=7,
        date_from=Mock(),
        date_to=Mock(),
    )

    repo.return_quantities_for_sale_line_ids.assert_awaited_once()
    assert repo.return_quantities_for_sale_line_ids.await_args.args[3] == [12]
    assert result == [
        GiftReturnAdjustment(
            reason_id=3,
            reason_name="活動贈品",
            description="杯子",
            qty=1,
            retail_value=Decimal(300),
            cost=Decimal(50),
        )
    ]


@pytest.mark.asyncio
async def test_gift_report_batches_filtered_quantity_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_ids = list(range(1, 502))
    snapshots = {
        line_id: GiftLineSnapshot(
            reason_id=None,
            reason_name="未指定原因",
            description=f"gift-{line_id}",
            qty=1,
            retail_unit_price=Decimal(0),
            cost=Decimal(0),
        )
        for line_id in candidate_ids
    }
    repo = Mock()
    repo.period_return_sale_line_ids = AsyncMock(return_value=candidate_ids)
    repo.return_quantities_for_sale_line_ids = AsyncMock(return_value=[])

    sales = Mock()
    sales.gift_line_snapshots = AsyncMock(side_effect=lambda _store, ids: {
        line_id: snapshots[line_id] for line_id in ids
    })
    monkeypatch.setattr("app.modules.returns.service.SalesService", lambda _session: sales)

    service = ReturnsService.__new__(ReturnsService)
    service._session = Mock()
    service._repo = repo

    assert await service.gift_return_adjustments(7, Mock(), Mock()) == []
    quantity_calls = repo.return_quantities_for_sale_line_ids.await_args_list
    assert [len(call.args[3]) for call in quantity_calls] == [500, 1]


@pytest.mark.asyncio
async def test_report_adjustments_uses_separate_tender_and_filtered_gift_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Mock()
    repo.refund_tender_totals = AsyncMock(
        return_value=[(TenderType.CASH, Decimal(200))]
    )
    service = ReturnsService.__new__(ReturnsService)
    service._repo = repo
    monkeypatch.setattr(service, "gift_return_adjustments", AsyncMock(return_value=[]))

    result = await service.report_adjustments(7, Mock(), Mock())

    assert result == ([(TenderType.CASH, Decimal(200))], [])
