"""購物金溢價率每日建議值引擎（docs/16 §6.2）——決定性、規則式、可審計。

純函數：輸入 = 各回看視窗的歷史指標（由帳本/銷售/收購推導，呼叫端組好）＋現行政策，
輸出 = 建議溢價率 ＋ 全部約束中間值。無隨機、無黑盒；同輸入恆同輸出（`ENGINE_VERSION`
標記規則版本，演算法改版可識別）。本檔**不碰 DB、不碰 service**——所有跨模組取數在
service 層完成後以 `WindowMetrics` 餵入。

約束（§6.2）：
1. 毛利上限 p_max1 = c·m/(1−c·m)，c = alpha_safety × α̂；c·m ≥ 1 → 無上限（取 max）。
2. 負債上限 p_max2：依 total_outstanding ÷ monthly_fixed_cash_outflow 階梯收斂。
3. 選用率導向：take_rate 低於目標帶 → 現值+step；高於 → 現值−step；帶內 → 維持。
最終建議 = min(有效約束)，夾在 [min, max]，捨入到 0.5pp。冷啟動（資料天數不足）→
固定起手值並標示資料不足。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

# 規則版本：演算法/權重語意改變時遞增，使落庫的歷史建議可回溯到當時規則。
ENGINE_VERSION = "sc5b-1.1"

# 冷啟動固定起手值（docs/16 §6.2；§1.5 premium_rate 預設 +10%）。
COLD_START_RATE = Decimal("0.1000")

_HALF_PP = Decimal("0.005")  # 0.5 個百分點
_RATE_QUANT = Decimal("0.0001")  # 比率 4 位小數（與 settings Numeric(5,4) 一致）

# 視窗名稱（昨日／近 7／30／90 天／去年同期）；權重正規化與組合都據此集合。
WINDOW_NAMES: tuple[str, ...] = ("yesterday", "d7", "d30", "d90", "yoy")
# 參與綜合指標組合的量測欄位（逐欄位獨立加權，缺值視窗於該欄位被剔除後重正規化）。
_METRIC_FIELDS: tuple[str, ...] = (
    "take_rate",
    "avg_premium_rate",
    "beta_retention",
    "alpha_incremental",
    "gross_margin_m",
)


@dataclass(frozen=True)
class WindowMetrics:
    """單一回看視窗的效益指標（docs/16 §5B；皆為比率，缺資料以 None 表示）。"""

    take_rate: Decimal | None = None
    avg_premium_rate: Decimal | None = None
    beta_retention: Decimal | None = None
    alpha_incremental: Decimal | None = None
    gross_margin_m: Decimal | None = None

    def has_any(self) -> bool:
        """該視窗是否有任一指標可用（全 None → 視為無資料視窗）。"""
        return any(getattr(self, field) is not None for field in _METRIC_FIELDS)


@dataclass(frozen=True)
class EngineParams:
    """引擎可調參數（docs/16 §1.5；來自 settings.store_credit_engine_params）。"""

    window_weights: dict[str, Decimal]
    alpha_safety: Decimal
    liability_ladder: tuple[Decimal, Decimal, Decimal]
    take_rate_band: tuple[Decimal, Decimal]
    take_rate_step: Decimal
    beta_n_days: int
    alpha_proxy_window_days: int
    cold_start_min_days: int
    yoy_halfwidth_days: int

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> EngineParams:
        """從 JSONB（值為 JSON number/list）解析為具型別參數；缺鍵/格式不符以 §1.5 預設補。

        `store_credit_engine_params` 在 settings 為任意 dict（PATCH 不逐欄驗結構），故本解析
        必須對畸形值（缺鍵、長度不符的 list、非數字、非 dict）一律退回文件化預設——
        絕不可因壞設定而讓引擎/端點 500（Codex SC-5b P2）。以 str() 轉 Decimal 保決定性。
        """
        source = raw if isinstance(raw, dict) else {}
        ladder = _safe_num_list(source.get("liability_ladder"), [1.5, 2.0, 2.5], 3)
        band = _safe_num_list(source.get("take_rate_band"), [0.30, 0.70], 2)
        return cls(
            window_weights=_safe_weights(source.get("window_weights")),
            alpha_safety=_safe_dec(source.get("alpha_safety"), Decimal("0.8")),
            liability_ladder=(ladder[0], ladder[1], ladder[2]),
            take_rate_band=(band[0], band[1]),
            take_rate_step=_safe_dec(source.get("take_rate_step"), Decimal("0.025")),
            beta_n_days=_safe_int(source.get("beta_n_days"), 180),
            alpha_proxy_window_days=_safe_int(source.get("alpha_proxy_window_days"), 90),
            cold_start_min_days=_safe_int(source.get("cold_start_min_days"), 30),
            yoy_halfwidth_days=_safe_int(source.get("yoy_halfwidth_days"), 15),
        )


_DEFAULT_WEIGHTS: dict[str, Decimal] = {
    "yesterday": Decimal("0.05"),
    "d7": Decimal("0.25"),
    "d30": Decimal("0.40"),
    "d90": Decimal("0.20"),
    "yoy": Decimal("0.10"),
}


@dataclass(frozen=True)
class EngineResult:
    """引擎輸出：建議溢價率＋全部可審計中間值（落 store_credit_suggestion_log）。"""

    suggested_rate: Decimal
    insufficient_data: bool
    engine_version: str
    combined_metrics: dict[str, str | None]
    normalized_weights: dict[str, str]
    constraint_values: dict[str, Any]


def _dec(value: Any) -> Decimal:
    """安全轉 Decimal（經 str()，避免 float 二進位表示誤差；Decimal 入參亦正確）。"""
    return Decimal(str(value))


def _safe_dec(value: Any, default: Decimal) -> Decimal:
    """轉 Decimal；缺值（None）或無法解析 → 退回 default（畸形設定不可 500）。"""
    if value is None:
        return default
    try:
        return _dec(value)
    except (InvalidOperation, TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int) -> int:
    """轉 int；缺值或無法解析 → 退回 default。"""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_num_list(value: Any, default: list[float], length: int) -> list[Decimal]:
    """轉長度為 length 的 Decimal list；非 list／長度不符／含非數字 → 退回 default。"""
    if isinstance(value, list) and len(value) == length:
        try:
            return [_dec(item) for item in value]
        except (InvalidOperation, TypeError, ValueError):
            pass
    return [_dec(item) for item in default]


def _safe_weights(value: Any) -> dict[str, Decimal]:
    """各視窗權重；非 dict 或某鍵畸形 → 該鍵退回文件化預設。"""
    source = value if isinstance(value, dict) else {}
    return {name: _safe_dec(source.get(name), _DEFAULT_WEIGHTS[name]) for name in WINDOW_NAMES}


def _fmt(value: Decimal) -> str:
    """比率字串化：剝除除法殘留的尾零、固定點記號（不出現科學記號）。

    內部約束數學仍用全精度 Decimal，僅在落庫/顯示時正規化，讓
    `0.3000…0`（加權平均殘留）顯示為 `0.3`、避免污染 JSONB 快照與審計可讀性。
    """
    return f"{value.normalize():f}"


def _fmt_or_none(value: Decimal | None) -> str | None:
    return None if value is None else _fmt(value)


def round_half_pp(rate: Decimal) -> Decimal:
    """捨入到最近的 0.5 個百分點（0.005），ROUND_HALF_UP；回 4dp Decimal。"""
    steps = (rate / _HALF_PP).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return (steps * _HALF_PP).quantize(_RATE_QUANT)


def _combine_metric(
    field: str, present: dict[str, WindowMetrics], weights: dict[str, Decimal]
) -> Decimal | None:
    """逐欄位加權混合：只取該欄位有值的視窗，權重在其間重正規化後加權平均。"""
    pairs = [
        (weights[name], getattr(wm, field))
        for name, wm in present.items()
        if getattr(wm, field) is not None
    ]
    if not pairs:
        return None
    total_weight = sum((w for w, _ in pairs), Decimal(0))
    if total_weight == 0:
        return None
    return sum((w * v for w, v in pairs), Decimal(0)) / total_weight


def _normalized_weights(
    present: dict[str, WindowMetrics], params: EngineParams
) -> dict[str, Decimal]:
    """有資料視窗的權重重正規化（總和為 1；全部無資料則回空）。"""
    raw = {name: params.window_weights[name] for name in present}
    total = sum(raw.values(), Decimal(0))
    if total == 0:
        return {}
    return {name: weight / total for name, weight in raw.items()}


def _p_max1(
    alpha_hat: Decimal | None, margin: Decimal | None, params: EngineParams, premium_max: Decimal
) -> tuple[Decimal, bool]:
    """毛利約束上限：p_max1 = c·m/(1−c·m)，c = alpha_safety×α̂。

    回 (上限, 是否套用)。α̂ 或 m 缺值 → 不套用（回 max，視為無此約束）。
    c·m ≥ 1（含 m≤0）→ 無上限（回 max）。
    """
    if alpha_hat is None or margin is None:
        return premium_max, False
    c = params.alpha_safety * alpha_hat
    cm = c * margin
    if cm >= 1:
        return premium_max, True
    return cm / (Decimal(1) - cm), True


def _p_max2(
    liability_ratio: Decimal | None,
    current_rate: Decimal,
    params: EngineParams,
    premium_max: Decimal,
) -> tuple[Decimal | None, str]:
    """負債約束上限（階梯）。分母未維護（ratio=None）→ 不套用（回 (None, 註記)）。"""
    if liability_ratio is None:
        return None, "monthly_fixed_cash_outflow 未維護（=0），略過負債約束"
    low, mid, high = params.liability_ladder
    if liability_ratio <= low:
        return premium_max, "ratio ≤ 1.5：不設限"
    if liability_ratio <= mid:
        return current_rate * Decimal("0.5"), "1.5 < ratio ≤ 2.0：現值×0.5"
    if liability_ratio <= high:
        return current_rate * Decimal("0.25"), "2.0 < ratio ≤ 2.5：現值×0.25"
    return Decimal(0), "ratio > 2.5：暫停溢價（0%）"


def _take_directional(
    take_rate: Decimal | None, current_rate: Decimal, params: EngineParams
) -> Decimal:
    """選用率導向值：低於目標帶 → 現值+step；高於 → 現值−step；帶內或缺值 → 現值。"""
    if take_rate is None:
        return current_rate
    low, high = params.take_rate_band
    if take_rate < low:
        return current_rate + params.take_rate_step
    if take_rate > high:
        return current_rate - params.take_rate_step
    return current_rate


def suggest_premium_rate(
    *,
    windows: dict[str, WindowMetrics | None],
    current_rate: Decimal,
    premium_min: Decimal,
    premium_max: Decimal,
    liability_ratio: Decimal | None,
    params: EngineParams,
    data_days: int,
) -> EngineResult:
    """產生當日建議溢價率與全部約束中間值（docs/16 §6.2）。

    冷啟動（`data_days < cold_start_min_days`）→ 固定 `COLD_START_RATE`、`insufficient_data=True`。
    否則：綜合指標（逐欄位加權混合，缺資料視窗重正規化）→ 三約束 → min → clamp → 捨入 0.5pp。
    """
    if data_days < params.cold_start_min_days:
        clamped = min(max(COLD_START_RATE, premium_min), premium_max)
        return EngineResult(
            suggested_rate=round_half_pp(clamped),
            insufficient_data=True,
            engine_version=ENGINE_VERSION,
            combined_metrics={field: None for field in _METRIC_FIELDS},
            normalized_weights={},
            constraint_values={
                "reason": "資料不足，採用預設值",
                "data_days": data_days,
                "cold_start_min_days": params.cold_start_min_days,
            },
        )

    present = {
        name: wm
        for name, wm in windows.items()
        if name in WINDOW_NAMES and wm is not None and wm.has_any()
    }
    norm_weights = _normalized_weights(present, params)
    combined = {field: _combine_metric(field, present, norm_weights) for field in _METRIC_FIELDS}

    alpha_hat = combined["alpha_incremental"]
    margin = combined["gross_margin_m"]
    take_rate = combined["take_rate"]

    p_max1, p_max1_applied = _p_max1(alpha_hat, margin, params, premium_max)
    p_max2, p_max2_note = _p_max2(liability_ratio, current_rate, params, premium_max)
    take_directional = _take_directional(take_rate, current_rate, params)

    candidates = [p_max1, take_directional]
    if p_max2 is not None:
        candidates.append(p_max2)
    final = min(candidates)
    clamped = min(max(final, premium_min), premium_max)
    suggested = round_half_pp(clamped)

    return EngineResult(
        suggested_rate=suggested,
        insufficient_data=False,
        engine_version=ENGINE_VERSION,
        combined_metrics={field: _fmt_or_none(combined[field]) for field in _METRIC_FIELDS},
        normalized_weights={name: _fmt(weight) for name, weight in norm_weights.items()},
        constraint_values={
            "p_max1": _fmt(p_max1),
            "p_max1_applied": p_max1_applied,
            "p_max2": None if p_max2 is None else _fmt(p_max2),
            "p_max2_note": p_max2_note,
            "take_rate_directional": _fmt(take_directional),
            "combined_alpha_hat": _fmt_or_none(alpha_hat),
            "combined_gross_margin_m": _fmt_or_none(margin),
            "combined_take_rate": _fmt_or_none(take_rate),
            "liability_ratio": None if liability_ratio is None else _fmt(liability_ratio),
            "raw_final": _fmt(final),
            "clamped": _fmt(clamped),
        },
    )
