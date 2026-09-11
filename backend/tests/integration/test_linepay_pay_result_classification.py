"""LINE Pay 付款回應的判讀：哪些是「確定沒扣款」、哪些只能說「結果未確認」。

稽核 F03（2026-09-10）：原本只要回應碼不是 0000 就一律當成拒付，並提示店員「重新掃碼」。
但 1145 是官方的「付款進行中」——正常流量下就會出現。這時叫店員重掃，客人就可能被扣兩次。

判讀改為**白名單**：只有官方結果碼表中「請求在扣款前就被擋下」的碼才算確定拒付；
其餘一律視為結果未確認——包含沒見過的碼與空值。碰到不認得的回應時，
寧可鎖單請店長查原單，也不能叫店員再收一次錢。

稽核 F02：付款成功回應未核對平台實付金額。回應帶了 payInfo 而總額與請款不符時，
不能把這筆當成足額成功。

結果碼依據 LINE Pay Offline API v4「結果程式碼」表（2026-09-11 查閱）。
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.sales.linepay import (
    DEFINITIVE_PAY_REJECT_CODES,
    LINEPAY_READ_TIMEOUT_SECONDS,
    parse_pay_result,
)
from app.modules.sales.models import Sale
from app.modules.sales.service import LinePayAttemptState, SalesService
from app.shared.exceptions import (
    LinePayChargeFailed,
    LinePayResultUncertain,
    LinePayTransportError,
)
from tests.integration.test_sales_linepay import (
    _CHECK_NOT_FOUND,
    ScriptedTransport,
    _client,
    _line,
    _linepay_cart_kwargs,
    _seed,
    _seed_item,
    _tender,
)


async def _pay(
    session: AsyncSession, pay_resp: dict[str, object], *, key: str
) -> tuple[LinePayAttemptState, Exception | None, int]:
    """以指定的 pay 回應跑一次 100 元結帳，回傳 (attempt 狀態, 例外, 本店的銷售列數)。

    服務層在呼叫 LINE Pay 前就 flush 了銷售列，失敗時**撤掉它的是 router 的 rollback**
    （見 test_sales_linepay 的 fail-closed 慣例）。所以失敗情境在這一層只驗「拋什麼、
    attempt 狀態」；「沒留下銷售、有鎖單、回 409」由 API 層測試證明。
    """
    store_id, clerk_id = await _seed(session)
    await _seed_item(session, store_id, code="LP-100", price="100")
    lines = _line("LP-100")
    tenders = _tender("100")
    cart = await _linepay_cart_kwargs(
        session, store_id=store_id, clerk_id=clerk_id, lines=lines, tenders=tenders
    )
    attempt = LinePayAttemptState()
    error: Exception | None = None
    try:
        await SalesService(session).create_sale(
            store_id,
            clerk_id,
            lines=lines,
            tenders=tenders,
            idempotency_key=key,
            linepay_client=_client(
                ScriptedTransport(check_resp=_CHECK_NOT_FOUND, pay_resp=pay_resp)
            ),
            linepay_attempt=attempt,
            **cart,
        )
    except Exception as exc:
        error = exc
    sales = await session.scalar(
        select(func.count()).select_from(Sale).where(Sale.store_id == store_id)
    )
    return attempt, error, int(sales or 0)


# ── F03：非確定拒付的回應不得被當成「沒扣款」 ─────────────────────────────


@pytest.mark.parametrize(
    "code",
    [
        "1145",  # 付款進行中
        "1152",  # 有相同交易歷史——前次可能已扣款
        "1172",  # 同訂單號已有交易紀錄——前次很可能已扣款
        "1198",  # API 呼叫請求重複
        "1199",  # 內部請求錯誤：無法判斷
        "9000",  # 內部錯誤：無法判斷
        "",  # 回應缺碼
        "7777",  # 官方表上沒有的碼：不認得就不能假設沒扣款
    ],
)
@pytest.mark.asyncio
async def test_nonterminal_response_stays_uncertain(db_session: AsyncSession, code: str) -> None:
    attempt, error, _ = await _pay(
        db_session, {"returnCode": code}, key=f"nt-{code or 'empty'}"
    )

    assert attempt.may_have_succeeded, "無法證實沒扣款，就不能讓畫面以為沒扣款"
    assert isinstance(error, LinePayResultUncertain)
    # 必須走「結果不明」的既有復原路徑（router 以 LinePayTransportError 攔下並鎖單），
    # 而不是「拒付、請重掃」那條。
    assert isinstance(error, LinePayTransportError)
    assert not isinstance(error, LinePayChargeFailed)


@pytest.mark.asyncio
async def test_uncertain_message_tells_clerk_not_to_charge_again(db_session: AsyncSession) -> None:
    """舊訊息「請改用其他方式或重新掃碼」正是誘發重複扣款的那句話，不得再出現。"""
    _, error, _ = await _pay(db_session, {"returnCode": "1145"}, key="nt-msg")
    assert error is not None
    message = str(error)
    assert "重新掃碼" not in message
    assert "改用其他方式" not in message
    assert "勿" in message or "不要" in message


@pytest.mark.parametrize(
    "code",
    [
        "1101",  # 非 LINE Pay 用戶
        "1110",  # 信用卡無法使用
        "1133",  # 付款碼無效（沙盒已實測）
        "1142",  # 餘額不足
        "2101",  # 參數錯誤（沙盒已實測）
    ],
)
@pytest.mark.asyncio
async def test_definitive_reject_still_allows_retry(db_session: AsyncSession, code: str) -> None:
    """真的在扣款前就被擋下的，照舊判為拒付——店員可以請客人換方式或重掃。"""
    attempt, error, _ = await _pay(db_session, {"returnCode": code}, key=f"rj-{code}")

    assert attempt.status == "FAILED"
    assert not attempt.may_have_succeeded
    assert isinstance(error, LinePayChargeFailed)


def test_reject_whitelist_does_not_contain_ambiguous_codes() -> None:
    """白名單裡混進任何一個「可能已扣款」的碼，就等於又會叫店員重收一次。"""
    ambiguous = {"1145", "1152", "1172", "1198", "1199", "9000", ""}
    assert DEFINITIVE_PAY_REJECT_CODES.isdisjoint(ambiguous)
    assert "0000" not in DEFINITIVE_PAY_REJECT_CODES


# ── F02：成功回應要核對平台實付金額 ──────────────────────────────────────


def _pay_success(amount: int | None) -> dict[str, object]:
    info: dict[str, object] = {"transactionId": 2026091100000000001, "orderId": "x"}
    if amount is not None:
        info["payInfo"] = [{"method": "BALANCE", "amount": amount}]
    return {"returnCode": "0000", "returnMessage": "Success.", "info": info}


@pytest.mark.parametrize("reported", [1, 99, 101])
@pytest.mark.asyncio
async def test_amount_mismatch_is_not_a_completed_sale(
    db_session: AsyncSession, reported: int
) -> None:
    """請收 100、平台說收了別的數字：錢確實動了，但數字對不起來，交店長核對。"""
    attempt, error, _ = await _pay(db_session, _pay_success(reported), key=f"amt-{reported}")

    assert isinstance(error, LinePayResultUncertain)
    assert attempt.may_have_succeeded  # 平台已回 0000：錢動了，不能讓畫面以為沒扣


@pytest.mark.asyncio
async def test_matching_amount_completes_sale(db_session: AsyncSession) -> None:
    attempt, error, sales = await _pay(db_session, _pay_success(100), key="amt-ok")

    assert error is None
    assert attempt.status == "SUCCESS"
    assert sales == 1


@pytest.mark.asyncio
async def test_missing_pay_info_keeps_prior_behaviour(db_session: AsyncSession) -> None:
    """官方文件沒把 payInfo 標為必要：沒帶就沿用舊行為，否則每一筆正常付款都會被鎖住。"""
    attempt, error, sales = await _pay(db_session, _pay_success(None), key="amt-none")

    assert error is None
    assert attempt.status == "SUCCESS"
    assert sales == 1


def test_split_pay_info_is_summed() -> None:
    """一筆付款可能拆成多種方式（餘額＋點數）：要比的是總額，不是第一項。"""
    result = parse_pay_result(
        {
            "returnCode": "0000",
            "info": {
                "transactionId": 2026091100000000002,
                "payInfo": [{"method": "BALANCE", "amount": 70}, {"method": "POINT", "amount": 30}],
            },
        }
    )
    assert result.amount == 100


# ── 逾時：官方要求 pay 的讀取逾時至少 40 秒 ─────────────────────────────


def test_read_timeout_meets_official_minimum() -> None:
    """短於官方下限，會把本來會成功的慢回應變成「結果不明」而平白鎖單。"""
    assert LINEPAY_READ_TIMEOUT_SECONDS >= 40
