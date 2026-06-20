"""購物金溢價建議引擎純函數測試（docs/16 §6.2）。

涵蓋：冷啟動、視窗加權與權重重正規化、p_max1（含 c·m≥1）、p_max2 階梯（含分母未維護）、
選用率導向、min/clamp、捨入到 0.5pp、決定性。引擎不碰 DB，輸入皆為合成指標。
"""

from decimal import Decimal

from app.modules.settings.defaults import DEFAULT_STORE_CREDIT_ENGINE_PARAMS
from app.modules.storecredit.engine import (
    COLD_START_RATE,
    ENGINE_VERSION,
    EngineParams,
    EngineResult,
    WindowMetrics,
    round_half_pp,
    suggest_premium_rate,
)

PARAMS = EngineParams.from_mapping(DEFAULT_STORE_CREDIT_ENGINE_PARAMS)


def _suggest(**overrides: object) -> EngineResult:
    base: dict[str, object] = dict(
        windows={"d30": WindowMetrics()},
        current_rate=Decimal("0.10"),
        premium_min=Decimal("0.00"),
        premium_max=Decimal("0.20"),
        liability_ratio=None,
        params=PARAMS,
        data_days=400,
    )
    base.update(overrides)
    return suggest_premium_rate(**base)  # type: ignore[arg-type]


# ── 參數解析 ──


def test_params_from_default_mapping() -> None:
    assert PARAMS.alpha_safety == Decimal("0.8")
    assert PARAMS.window_weights["d30"] == Decimal("0.40")
    assert sum(PARAMS.window_weights.values()) == Decimal("1.00")
    assert PARAMS.liability_ladder == (Decimal("1.5"), Decimal("2.0"), Decimal("2.5"))
    assert PARAMS.take_rate_band == (Decimal("0.30"), Decimal("0.70"))
    assert PARAMS.cold_start_min_days == 30


def test_params_fills_missing_keys_with_defaults() -> None:
    sparse = EngineParams.from_mapping({"alpha_safety": 0.5})
    assert sparse.alpha_safety == Decimal("0.5")
    assert sparse.beta_n_days == 180  # 缺鍵以 §1.5 預設補
    assert sparse.window_weights["yesterday"] == Decimal("0.05")


def test_params_malformed_values_fall_back_to_defaults() -> None:
    """畸形 store_credit_engine_params（settings 任意 dict）不可 500，退回預設（SC-5b P2）。"""
    bad = EngineParams.from_mapping(
        {
            "liability_ladder": [1.5],  # 長度不符
            "take_rate_band": "nope",  # 非 list
            "alpha_safety": "abc",  # 非數字
            "beta_n_days": "x",  # 非整數
            "window_weights": [1, 2, 3],  # 非 dict
        }
    )
    assert bad.liability_ladder == (Decimal("1.5"), Decimal("2.0"), Decimal("2.5"))
    assert bad.take_rate_band == (Decimal("0.30"), Decimal("0.70"))
    assert bad.alpha_safety == Decimal("0.8")
    assert bad.beta_n_days == 180
    assert bad.window_weights["d30"] == Decimal("0.40")


def test_params_non_numeric_list_element_falls_back() -> None:
    bad = EngineParams.from_mapping({"liability_ladder": [1.5, "oops", 2.5]})
    assert bad.liability_ladder == (Decimal("1.5"), Decimal("2.0"), Decimal("2.5"))


def test_params_non_dict_raw_yields_all_defaults() -> None:
    # 整個欄位被存成非 dict（理論上不該發生）也不能 500。
    params = EngineParams.from_mapping(None)  # type: ignore[arg-type]
    assert params.cold_start_min_days == 30
    assert params.take_rate_band == (Decimal("0.30"), Decimal("0.70"))


def test_malformed_params_still_produce_a_suggestion() -> None:
    """端點層級保證：壞設定下引擎仍回有效建議（退回預設後正常計算），不丟例外。"""
    params = EngineParams.from_mapping({"liability_ladder": [1.5], "take_rate_band": [0.3]})
    result = suggest_premium_rate(
        windows={"d30": WindowMetrics(take_rate=Decimal("0.5"))},
        current_rate=Decimal("0.10"),
        premium_min=Decimal("0.00"),
        premium_max=Decimal("0.20"),
        liability_ratio=Decimal("1.75"),
        params=params,
        data_days=400,
    )
    assert result.suggested_rate == Decimal("0.0500")  # 1.5<ratio≤2.0 → 現值×0.5


# ── 冷啟動 ──


def test_cold_start_returns_fixed_rate_flagged() -> None:
    result = _suggest(data_days=29)
    assert result.insufficient_data is True
    assert result.suggested_rate == COLD_START_RATE
    assert result.constraint_values["reason"] == "資料不足，採用預設值"
    assert result.engine_version == ENGINE_VERSION


def test_cold_start_boundary_at_min_days_computes() -> None:
    # 剛好達門檻（30）→ 進入正常計算路徑，非冷啟動。
    result = _suggest(data_days=30, windows={"d30": WindowMetrics(take_rate=Decimal("0.5"))})
    assert result.insufficient_data is False


def test_cold_start_rate_clamped_to_max() -> None:
    # premium_max 低於起手 10% 時，冷啟動值仍須夾在界線內。
    result = _suggest(data_days=5, premium_max=Decimal("0.05"))
    assert result.suggested_rate == Decimal("0.0500")


# ── 捨入到 0.5pp ──


def test_round_half_pp() -> None:
    assert round_half_pp(Decimal("0.1234")) == Decimal("0.1250")  # 24.68 → 25
    assert round_half_pp(Decimal("0.1224")) == Decimal("0.1200")  # 24.48 → 24
    assert round_half_pp(Decimal("0.1025")) == Decimal("0.1050")  # 20.5 → 21（half-up）
    assert round_half_pp(Decimal("0.10")) == Decimal("0.1000")


# ── 視窗加權與重正規化 ──


def test_single_window_combines_to_that_window() -> None:
    result = _suggest(
        windows={"d30": WindowMetrics(take_rate=Decimal("0.5"), gross_margin_m=Decimal("0.4"))},
    )
    assert result.normalized_weights == {"d30": "1"}
    assert result.combined_metrics["take_rate"] == "0.5"
    assert result.combined_metrics["gross_margin_m"] == "0.4"


def test_weight_renormalization_over_present_windows() -> None:
    # 只有 d30(0.40) 與 yoy(0.10) 有資料 → 重正規化為 0.8 / 0.2（乾淨小數）。
    result = _suggest(
        windows={
            "yesterday": None,
            "d7": WindowMetrics(),  # 全 None → 視為無資料
            "d30": WindowMetrics(take_rate=Decimal("0.50")),
            "d90": None,
            "yoy": WindowMetrics(take_rate=Decimal("0.20")),
        },
    )
    assert set(result.normalized_weights) == {"d30", "yoy"}
    assert result.normalized_weights["d30"] == "0.8"
    assert result.normalized_weights["yoy"] == "0.2"
    # 0.8×0.50 + 0.2×0.20 = 0.44
    assert result.combined_metrics["take_rate"] == "0.44"


def test_per_metric_renormalization_when_field_missing_in_some_windows() -> None:
    # take_rate 兩窗都有；gross_margin 只有 d30 有 → margin 綜合值=該窗值。
    result = _suggest(
        windows={
            "d7": WindowMetrics(take_rate=Decimal("0.40")),
            "d30": WindowMetrics(take_rate=Decimal("0.40"), gross_margin_m=Decimal("0.3")),
        },
    )
    assert result.combined_metrics["take_rate"] == "0.4"  # 加權平均後 Decimal 正規化
    assert result.combined_metrics["gross_margin_m"] == "0.3"


# ── p_max1（毛利約束）──


def test_p_max1_formula_binds() -> None:
    # α̂=0.5, m=0.4 → c=0.4, cm=0.16, p_max1=0.16/0.84=0.190476 → 0.1900（其餘約束放寬）。
    result = _suggest(
        current_rate=Decimal("0.20"),
        windows={
            "d30": WindowMetrics(
                alpha_incremental=Decimal("0.5"),
                gross_margin_m=Decimal("0.4"),
                take_rate=Decimal("0.50"),  # 帶內 → 導向=現值 0.20，不綁
            )
        },
    )
    assert result.constraint_values["p_max1_applied"] is True
    assert result.suggested_rate == Decimal("0.1900")


def test_p_max1_skipped_when_inputs_missing() -> None:
    # 無 α̂/m → p_max1 不套用（回 max），由 take 導向決定。
    result = _suggest(
        windows={"d30": WindowMetrics(take_rate=Decimal("0.10"))},  # <0.30 → 現值+step
    )
    assert result.constraint_values["p_max1_applied"] is False
    assert result.suggested_rate == Decimal("0.1250")  # 0.10 + 0.025 = 0.125


def test_p_max1_no_limit_when_cm_ge_one() -> None:
    # 合成 m=2.0, α̂=0.7 → c=0.56, cm=1.12 ≥ 1 → 無上限（取 max）。
    result = _suggest(
        current_rate=Decimal("0.20"),
        windows={
            "d30": WindowMetrics(
                alpha_incremental=Decimal("0.7"),
                gross_margin_m=Decimal("2.0"),
                take_rate=Decimal("0.50"),
            )
        },
    )
    assert result.constraint_values["p_max1"] == "0.2"  # = premium_max（無上限）
    assert result.suggested_rate == Decimal("0.2000")


# ── p_max2（負債約束階梯）──


def test_p_max2_no_limit_below_first_rung() -> None:
    result = _suggest(
        liability_ratio=Decimal("1.0"),
        current_rate=Decimal("0.20"),
        windows={"d30": WindowMetrics(take_rate=Decimal("0.50"))},
    )
    assert result.suggested_rate == Decimal("0.2000")


def test_p_max2_half_at_second_rung() -> None:
    result = _suggest(
        liability_ratio=Decimal("1.75"),
        current_rate=Decimal("0.20"),
        windows={"d30": WindowMetrics(take_rate=Decimal("0.50"))},
    )
    assert result.suggested_rate == Decimal("0.1000")  # 0.20 × 0.5


def test_p_max2_quarter_at_third_rung() -> None:
    result = _suggest(
        liability_ratio=Decimal("2.25"),
        current_rate=Decimal("0.20"),
        windows={"d30": WindowMetrics(take_rate=Decimal("0.50"))},
    )
    assert result.suggested_rate == Decimal("0.0500")  # 0.20 × 0.25


def test_p_max2_suspends_above_high_rung() -> None:
    result = _suggest(
        liability_ratio=Decimal("3.0"),
        current_rate=Decimal("0.20"),
        windows={"d30": WindowMetrics(take_rate=Decimal("0.50"))},
    )
    assert result.suggested_rate == Decimal("0.0000")  # 暫停溢價


def test_p_max2_skipped_when_ratio_none() -> None:
    result = _suggest(
        liability_ratio=None,
        current_rate=Decimal("0.20"),
        windows={"d30": WindowMetrics(take_rate=Decimal("0.50"))},
    )
    assert result.constraint_values["p_max2"] is None
    assert "未維護" in result.constraint_values["p_max2_note"]


# ── 選用率導向 ──


def test_take_rate_below_band_raises_rate() -> None:
    result = _suggest(
        current_rate=Decimal("0.10"),
        windows={"d30": WindowMetrics(take_rate=Decimal("0.25"))},
    )
    assert result.suggested_rate == Decimal("0.1250")


def test_take_rate_above_band_lowers_rate() -> None:
    result = _suggest(
        current_rate=Decimal("0.10"),
        windows={"d30": WindowMetrics(take_rate=Decimal("0.80"))},
    )
    assert result.suggested_rate == Decimal("0.0750")


def test_take_rate_within_band_holds() -> None:
    result = _suggest(
        current_rate=Decimal("0.10"),
        windows={"d30": WindowMetrics(take_rate=Decimal("0.50"))},
    )
    assert result.suggested_rate == Decimal("0.1000")


# ── clamp 與決定性 ──


def test_clamp_to_premium_min() -> None:
    # ratio>2.5 → 0，但 min 界線 0.05 → 夾到 0.05。
    result = _suggest(
        liability_ratio=Decimal("3.0"),
        premium_min=Decimal("0.05"),
        current_rate=Decimal("0.20"),
        windows={"d30": WindowMetrics(take_rate=Decimal("0.50"))},
    )
    assert result.suggested_rate == Decimal("0.0500")


def test_min_of_all_constraints_wins() -> None:
    # p_max2(0.05) < p_max1 < take 導向 → 取 0.05。
    result = _suggest(
        liability_ratio=Decimal("2.25"),
        current_rate=Decimal("0.20"),
        windows={
            "d30": WindowMetrics(
                alpha_incremental=Decimal("0.5"),
                gross_margin_m=Decimal("0.4"),
                take_rate=Decimal("0.10"),  # 會想升，但被負債約束壓下
            )
        },
    )
    assert result.suggested_rate == Decimal("0.0500")


def test_deterministic_same_input_same_output() -> None:
    kwargs = dict(
        liability_ratio=Decimal("1.75"),
        current_rate=Decimal("0.20"),
        windows={"d30": WindowMetrics(take_rate=Decimal("0.50"), gross_margin_m=Decimal("0.4"))},
    )
    first = _suggest(**kwargs)
    second = _suggest(**kwargs)
    assert first == second


def test_combine_returns_none_when_only_zero_weight_window_has_field() -> None:
    # d7 權重設為 0 且為唯一帶 gross_margin 的視窗 → 該欄位綜合值權重和為 0 → None。
    params = EngineParams.from_mapping(
        {
            **DEFAULT_STORE_CREDIT_ENGINE_PARAMS,
            "window_weights": {
                "yesterday": 0.05,
                "d7": 0.0,
                "d30": 0.40,
                "d90": 0.20,
                "yoy": 0.10,
            },
        }
    )
    result = _suggest(
        params=params,
        windows={
            "d7": WindowMetrics(gross_margin_m=Decimal("0.4")),  # 唯一帶 margin，但權重 0
            "d30": WindowMetrics(take_rate=Decimal("0.5")),
        },
    )
    assert result.combined_metrics["gross_margin_m"] is None
    assert result.constraint_values["p_max1_applied"] is False  # m 缺 → 毛利約束不套用


def test_all_windows_empty_yields_held_rate() -> None:
    # 無任何視窗資料 → 綜合指標全 None → 約束皆不綁 → 取現值。
    result = _suggest(
        current_rate=Decimal("0.10"),
        windows={"d30": WindowMetrics(), "d7": None},
    )
    assert result.normalized_weights == {}
    assert result.suggested_rate == Decimal("0.1000")
