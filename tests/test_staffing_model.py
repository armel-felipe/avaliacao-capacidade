import pandas as pd
import pytest
import staffing_model

from staffing_model import (
    aplicar_shrinkage,
    calcular_cobertura_12x36,
    calcular_min_atendentes,
    dimensionar_12x36,
    montar_cobertura,
    normalize_demand,
    otimizar_5x2,
    preparar_dados_grafico,
)


def test_zero_arrivals_require_zero_agents():
    assert calcular_min_atendentes(
        lam=0,
        tmr=30,
        simult=7,
        limite_espera_min=5,
        efetividade=0.85,
    ) == (0, 0.0)


def test_queue_wait_uses_minutes_without_a_second_conversion():
    raw_need, wait_min = calcular_min_atendentes(
        lam=10 / 60,
        tmr=30,
        simult=7,
        limite_espera_min=5,
        efetividade=0.85,
    )
    adjusted = aplicar_shrinkage(
        pd.DataFrame({"atendentes_necessarios": [raw_need]}),
        enabled=True,
        factor=1 / 12,
    )

    assert raw_need == 2
    assert wait_min == 1.08
    assert adjusted["necessidade_ajustada"].tolist() == [3]


def test_normalize_demand_fills_missing_combinations_with_zero():
    source = pd.DataFrame([
        {"dia_da_semana": "segunda", "hora": 8, "volume_mensagens": 4, "Peso": 0.5},
    ])
    result = normalize_demand(source)

    assert len(result) == 168
    assert not result.duplicated(["dia_ordem", "hora"]).any()
    assert result.loc[
        (result["dia_ordem"] == 1) & (result["hora"] == 8), "volume_mensagens"
    ].item() == 4
    assert result.loc[
        (result["dia_ordem"] == 1) & (result["hora"] == 9), "volume_mensagens"
    ].item() == 0


def test_normalize_demand_rejects_fractional_hours():
    source = pd.DataFrame(
        [
            {
                "dia_da_semana": "segunda",
                "hora": 1.5,
                "volume_mensagens": 4,
            }
        ]
    )

    with pytest.raises(ValueError, match="hora precisa ser inteira"):
        normalize_demand(source)


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            pd.DataFrame(
                [{"dia_da_semana": "funday", "hora": 1, "volume_mensagens": 4}]
            ),
            "Dias da semana inválidos",
        ),
        (
            pd.DataFrame(
                [{"dia_da_semana": "segunda", "hora": 24, "volume_mensagens": 4}]
            ),
            "entre 0 e 23",
        ),
        (
            pd.DataFrame(
                [{"dia_da_semana": "segunda", "hora": "x", "volume_mensagens": 4}]
            ),
            "precisam ser numéricos",
        ),
        (
            pd.DataFrame(
                [{"dia_da_semana": "segunda", "hora": 1, "volume_mensagens": -1}]
            ),
            "não pode ser negativo",
        ),
        (
            pd.DataFrame(
                [
                    {"dia_da_semana": "segunda", "hora": 1, "volume_mensagens": 4},
                    {"dia_da_semana": "segunda-feira", "hora": 1, "volume_mensagens": 5},
                ]
            ),
            "mais de uma linha",
        ),
        (
            pd.DataFrame(
                [
                    {
                        "dia_da_semana": "segunda",
                        "hora": 1,
                        "volume_mensagens": float("inf"),
                    }
                ]
            ),
            "finitos",
        ),
    ],
    ids=[
        "unknown-day",
        "out-of-range-hour",
        "nonnumeric-hour",
        "negative-volume",
        "duplicate-day-hour",
        "infinite-volume",
    ],
)
def test_normalize_demand_rejects_invalid_input(source, message):
    with pytest.raises(ValueError, match=message):
        normalize_demand(source)


def test_shrinkage_adjusts_positive_need_and_preserves_zero():
    source = pd.DataFrame({"atendentes_necessarios": [12, 0]})
    result = aplicar_shrinkage(source, enabled=True, factor=1 / 12)

    assert result["necessidade_ajustada"].tolist() == [14, 0]
    assert source["atendentes_necessarios"].tolist() == [12, 0]


@pytest.fixture
def zero_need():
    days = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
    return pd.DataFrame([
        {
            "dia_ordem": day_order,
            "dia_da_semana": day_name,
            "hora": hour,
            "necessidade_ajustada": 0,
        }
        for day_order, day_name in enumerate(days, start=1)
        for hour in range(24)
    ])


def test_12x36_has_no_fixed_floor_when_demand_is_zero(zero_need):
    result = dimensionar_12x36(
        zero_need,
        janela_pausa_diurna=(11, 15),
        pausa_noturna=(21, 22),
        janela_pausa_noturna=(21, 23),
    )

    assert result["postos_base"] == {"dia": 0, "noite": 0}
    assert result["postos_relevo"] == {"dia": 0, "noite": 0}


def test_12x36_weekly_peak_uses_only_approved_day_and_night_windows(zero_need):
    need = zero_need.copy()
    peak_needs = [
        (1, 6, 3),
        (2, 7, 5),
        (7, 8, 20),
        (3, 22, 4),
        (4, 5, 6),
    ]
    for day, hour, people in peak_needs:
        need.loc[
            (need["dia_ordem"] == day) & (need["hora"] == hour),
            "necessidade_ajustada",
        ] = people

    staffing = dimensionar_12x36(need)

    assert staffing["postos_base"] == {"dia": 5, "noite": 6}
    assert "postos_adicionais" not in staffing


def test_12x36_zero_demand_has_no_base_relief_or_active_capacity(zero_need):
    staffing = dimensionar_12x36(zero_need)

    assert staffing["postos_base"] == {"dia": 0, "noite": 0}
    assert staffing["postos_relevo"] == {"dia": 0, "noite": 0}
    assert staffing["cobertura_12x36"]["capacidade_12x36"].sum() == 0


def test_12x36_relief_minimum_covers_daytime_breaks(zero_need):
    need = zero_need.copy()
    need.loc[
        (need["dia_ordem"] == 1) & (need["hora"] == 6),
        "necessidade_ajustada",
    ] = 2

    staffing = dimensionar_12x36(need)

    assert staffing["postos_base"]["dia"] == 2
    assert staffing["postos_relevo"]["dia"] == 1
    for day in range(1, 8):
        assert sum(staffing["pausas"]["dia"]["base"][day].values()) == 2
        assert sum(staffing["pausas"]["dia"]["relevo"][day].values()) == 1
        assert set(staffing["pausas"]["dia"]["base"][day]).issubset(
            {11, 12, 13, 14}
        )
        assert set(staffing["pausas"]["dia"]["relevo"][day]).issubset(
            {11, 12, 13, 14}
        )
        assert all(
            staffing["cobertura_12x36"].query(
                f"dia_ordem == {day} and hora == {hour}"
            )["capacidade_12x36"].item()
            >= 2
            for hour in {11, 12, 13, 14}
        )


def test_12x36_relief_preserves_base_during_night_fixed_pause(zero_need):
    need = zero_need.copy()
    need.loc[
        (need["dia_ordem"] == 1) & (need["hora"] == 18),
        "necessidade_ajustada",
    ] = 2

    staffing = dimensionar_12x36(need)

    assert staffing["postos_base"]["noite"] == 2
    assert staffing["postos_relevo"]["noite"] == 1
    for day in range(1, 8):
        assert sum(staffing["pausas"]["noite"]["base"][day].values()) == 2
        assert sum(staffing["pausas"]["noite"]["relevo"][day].values()) == 1
        assert all(
            staffing["cobertura_12x36"].query(
                f"dia_ordem == {day} and hora == {hour}"
            )["capacidade_12x36"].item() >= 2
            for hour in (21, 22, 23, 0)
        )


def test_night_relief_is_minimized_across_four_meal_slots(zero_need):
    need = zero_need.copy()
    need.loc[
        (need["dia_ordem"] == 1) & (need["hora"] == 18),
        "necessidade_ajustada",
    ] = 3

    staffing = dimensionar_12x36(
        need,
        pausa_noturna=(21, 22),
        janela_pausa_noturna=(21, 1),
    )

    assert staffing["postos_base"]["noite"] == 3
    assert staffing["postos_relevo"]["noite"] == 1
    for day in range(1, 8):
        assert sum(staffing["pausas"]["noite"]["base"][day].values()) == 3
        assert sum(staffing["pausas"]["noite"]["relevo"][day].values()) == 1
        assert all(
            staffing["cobertura_12x36"].query(
                f"dia_ordem == {day} and hora == {hour}"
            )["capacidade_12x36"].item() >= 3
            for hour in (21, 22, 23)
        )
        next_day = day % 7 + 1
        assert staffing["cobertura_12x36"].query(
            f"dia_ordem == {next_day} and hora == 0"
        )["capacidade_12x36"].item() >= 3


def test_relief_is_excluded_from_dimensioned_quantity_but_included_in_effective_hc():
    staffing = {
        "postos_base": {"dia": 0, "noite": 3},
        "postos_relevo": {"dia": 0, "noite": 1},
    }
    sol5 = {
        "base": {"x5_8": 0, "x5_9": 0, "total": 0},
        "relevo": {"x5_8": 0, "x5_9": 0, "total": 0},
    }

    result = staffing_model.construir_headcount_efetivo(staffing, sol5)

    rows = result["tabela"]
    assert result["hc_12x36"] == 8
    assert rows.loc[rows["Camada"] == "Base", "Quantidade dimensionada"].sum() == 3
    assert rows.loc[rows["Camada"] == "Relevo", "Quantidade dimensionada"].sum() == 0
    assert rows.loc[rows["Camada"] == "Relevo", "HC efetivo"].sum() == 2
    assert rows.iloc[-1]["HC efetivo"] == 8
    relief_subtotal = rows.loc[
        rows["Camada"] == "Total 12x36 — relevo", "HC efetivo"
    ].item()
    assert relief_subtotal == 2
    assert rows.loc[
        rows["Camada"] == "HC efetivo 12x36", "HC efetivo"
    ].item() == 8


def test_pause_summary_preserves_daily_positions():
    pause_rows = pd.DataFrame(
        [
            {
                "dia_ordem": 1,
                "turno": "noite",
                "inicio_pausa": 21,
                "pausa": "21:00–22:00",
                "quantidade": 1,
            },
            {
                "dia_ordem": 2,
                "turno": "noite",
                "inicio_pausa": 21,
                "pausa": "21:00–22:00",
                "quantidade": 2,
            },
        ]
    )

    result = staffing_model.preparar_dados_pausas_por_dia(pause_rows)

    assert result[
        ["dia_ordem", "turno", "inicio_pausa", "posicoes_em_pausa"]
    ].to_dict("records") == [
        {
            "dia_ordem": 1,
            "turno": "noite",
            "inicio_pausa": 21,
            "posicoes_em_pausa": 1,
        },
        {
            "dia_ordem": 2,
            "turno": "noite",
            "inicio_pausa": 21,
            "posicoes_em_pausa": 2,
        },
    ]


def test_12x36_breaks_are_staggered_and_reduce_active_capacity(zero_need):
    need = zero_need.copy()
    need.loc[
        (need["dia_ordem"] == 1) & (need["hora"].isin([6, 18])),
        "necessidade_ajustada",
    ] = 2
    staffing = dimensionar_12x36(
        need,
        janela_pausa_diurna=(11, 15),
        pausa_noturna=(21, 22),
        janela_pausa_noturna=(21, 1),
    )
    coverage = calcular_cobertura_12x36(need, staffing)
    monday = coverage.query("dia_ordem == 1").set_index("hora")

    assert sum(staffing["pausas"]["dia"]["base"][1].values()) == 2
    assert sum(staffing["pausas"]["dia"]["relevo"][1].values()) == 1
    assert sum(staffing["pausas"]["noite"]["base"][1].values()) == 2
    assert sum(staffing["pausas"]["noite"]["relevo"][1].values()) == 1
    assert all(monday.loc[hour, "capacidade_12x36"] >= 2 for hour in (11, 12, 13, 14, 21, 22, 23, 0))


def test_pause_windows_use_exclusive_endpoints(zero_need):
    staffing = dimensionar_12x36(zero_need)

    assert staffing["horas_pausa_permitidas"] == {
        "dia": [11, 12, 13, 14],
        "noite": [21, 22, 23, 0],
    }
    assert staffing["pausas"]["noite"]["base"][1] == {}
    assert staffing["pausas"]["noite"]["relevo"][1] == {}


def test_12x36_uses_night_peak_and_excludes_weekday_business_need(zero_need):
    need = zero_need.copy()
    need.loc[(need["dia_ordem"] == 2) & (need["hora"] == 22), "necessidade_ajustada"] = 4
    need.loc[(need["dia_ordem"] == 6) & (need["hora"] == 10), "necessidade_ajustada"] = 4

    staffing = dimensionar_12x36(
        need,
        janela_pausa_diurna=(11, 15),
        pausa_noturna=(21, 22),
        janela_pausa_noturna=(21, 1),
    )
    coverage = calcular_cobertura_12x36(need, staffing)
    night = coverage.query("dia_ordem == 2 and hora == 22").iloc[0]
    saturday = coverage.query("dia_ordem == 6 and hora == 10").iloc[0]

    assert staffing["postos_base"]["noite"] == 4
    assert staffing["postos_base"]["dia"] == 0
    assert staffing["postos_relevo"]["noite"] == 2
    assert sum(staffing["pausas"]["noite"]["relevo"][2].values()) == 2
    assert night["capacidade_12x36"] >= 4
    assert saturday["capacidade_12x36"] == 0


def test_12x36_day_posts_ignore_weekday_business_hour_need(zero_need):
    baseline_need = zero_need.copy()
    baseline_need.loc[
        (baseline_need["dia_ordem"] == 1) & (baseline_need["hora"] == 6),
        "necessidade_ajustada",
    ] = 3
    baseline = dimensionar_12x36(baseline_need)
    business_need = zero_need.copy()
    business_need.loc[
        (business_need["dia_ordem"] == 1) & (business_need["hora"] == 6),
        "necessidade_ajustada",
    ] = 3
    business_need.loc[
        (business_need["dia_ordem"] == 1)
        & (business_need["hora"] == 10),
        "necessidade_ajustada",
    ] = 12

    business_staffing = dimensionar_12x36(business_need)
    business_coverage = calcular_cobertura_12x36(
        business_need, business_staffing
    )

    assert business_staffing["postos_base"] == baseline["postos_base"]
    assert business_staffing["postos_relevo"] == baseline["postos_relevo"]
    assert business_coverage.query(
        "dia_ordem == 1 and hora == 10"
    )["capacidade_12x36"].item() > 0


def test_12x36_posts_respond_to_their_sizing_windows(zero_need):
    cases = [
        (1, 7, "dia", 4),
        (6, 10, "dia", 0),
        (2, 22, "noite", 4),
        (2, 4, "noite", 4),
    ]
    for day, hour, shift, expected_base in cases:
        need = zero_need.copy()
        need.loc[
            (need["dia_ordem"] == day) & (need["hora"] == hour),
            "necessidade_ajustada",
        ] = 4
        staffing = dimensionar_12x36(need)
        assert staffing["postos_base"][shift] == expected_base


def test_12x36_night_shift_preserves_need_and_stays_flat_off_break(zero_need):
    need = zero_need.copy()
    need.loc[
        (need["dia_ordem"] == 4) & (need["hora"] == 21),
        "necessidade_ajustada",
    ] = 3
    need.loc[
        (need["dia_ordem"] == 4) & (need["hora"] == 22),
        "necessidade_ajustada",
    ] = 2

    staffing = dimensionar_12x36(need)
    coverage = calcular_cobertura_12x36(need, staffing)
    thursday = coverage.query("dia_ordem == 4").set_index("hora")
    monday_midnight = coverage.query(
        "dia_ordem == 1 and hora.between(0, 5)"
    )["capacidade_12x36"]

    assert staffing["postos_base"]["noite"] == 3
    assert staffing["postos_relevo"]["noite"] == 1
    assert thursday.loc[21, "capacidade_12x36"] >= 3
    assert thursday.loc[22, "capacidade_12x36"] >= 2
    assert monday_midnight.iloc[0] == 3
    assert monday_midnight.iloc[1:].nunique() == 1
    assert monday_midnight.iloc[1] == 4


def test_5x2_complements_12x36_on_shared_weekday_queue(zero_need):
    need = zero_need.copy()
    need["necessidade_ajustada"] = 0
    need.loc[
        (need["dia_ordem"] == 1) & (need["hora"] == 10),
        "necessidade_ajustada",
    ] = 4
    staffing = dimensionar_12x36(need)
    coverage_12x36 = calcular_cobertura_12x36(need, staffing)

    result = otimizar_5x2(need, coverage_12x36)
    coverage = montar_cobertura(need, staffing, result)
    monday_10 = coverage.query("dia_ordem == 1 and hora == 10").iloc[0]

    assert monday_10["capacidade_12x36"] == 0
    assert monday_10["capacidade_5x2"] > 0
    assert monday_10["capacidade_planejada"] >= monday_10["necessidade_estimada"]
    assert monday_10["gap"] == 0


def test_5x2_meal_and_weekend_boundaries(zero_need):
    from staffing_model import capacidade_5x2_por_hora

    lunch_8 = {11: 1}
    lunch_9 = {12: 1}
    assert capacidade_5x2_por_hora(1, 8, 1, 8, lunch_8) == 1
    assert capacidade_5x2_por_hora(1, 11, 1, 8, lunch_8) == 0
    assert capacidade_5x2_por_hora(1, 12, 1, 8, lunch_8) == 1
    assert capacidade_5x2_por_hora(1, 17, 1, 8) == 0
    assert capacidade_5x2_por_hora(1, 18, 1, 8) == 0
    assert capacidade_5x2_por_hora(1, 11, 1, 9, lunch_9) == 1
    assert capacidade_5x2_por_hora(1, 12, 1, 9, lunch_9) == 0
    assert capacidade_5x2_por_hora(1, 17, 1, 9) == 1
    assert capacidade_5x2_por_hora(1, 18, 1, 9) == 0
    assert capacidade_5x2_por_hora(6, 10, 1, 8) == 0


def test_prepare_chart_data_has_two_series_for_each_selected_day(zero_need):
    staffing = dimensionar_12x36(zero_need)
    sol5 = {
        "base": {"x5_8": 0, "x5_9": 0, "total": 0},
        "relevo": {"x5_8": 0, "x5_9": 0, "total": 0},
        "pausas": {
            day: {
                entry: {"base": {}, "relevo": {}}
                for entry in (8, 9)
            }
            for day in range(1, 8)
        },
    }
    coverage = montar_cobertura(zero_need, staffing, sol5)

    chart_data = preparar_dados_grafico(coverage, ["segunda", "sábado"])

    assert set(chart_data["dia_da_semana"]) == {"segunda", "sábado"}
    assert set(chart_data["metrica"]) == {
        "Necessidade estimada",
        "Capacidade planejada",
    }
    assert len(chart_data) == 2 * 24 * 2
    assert not chart_data.duplicated(
        ["dia_da_semana", "hora", "metrica"]
    ).any()


def test_prepare_chart_data_filters_metrics_without_changing_days(zero_need):
    staffing = dimensionar_12x36(zero_need)
    coverage = montar_cobertura(
        zero_need,
        staffing,
        {
            "base": {"x5_8": 0, "x5_9": 0, "total": 0},
            "relevo": {"x5_8": 0, "x5_9": 0, "total": 0},
            "pausas": {
                day: {
                    entry: {"base": {}, "relevo": {}}
                    for entry in (8, 9)
                }
                for day in range(1, 8)
            },
        },
    )

    chart_data = preparar_dados_grafico(
        coverage,
        ["segunda"],
        ["Necessidade estimada"],
    )

    assert set(chart_data["dia_da_semana"]) == {"segunda"}
    assert set(chart_data["metrica"]) == {"Necessidade estimada"}
    assert len(chart_data) == 24


def test_5x2_one_person_on_lunch_preserves_two_active(zero_need):
    from staffing_model import capacidade_5x2_por_hora

    assert capacidade_5x2_por_hora(
        dia_ordem=1,
        hora=12,
        quantidade=3,
        entrada=8,
        pausas_por_hora={12: 1},
    ) == 2
    assert capacidade_5x2_por_hora(
        dia_ordem=1,
        hora=13,
        quantidade=3,
        entrada=8,
        pausas_por_hora={12: 1},
    ) == 3


def test_5x2_optimizer_assigns_one_lunch_per_person_and_covers_need(zero_need):
    from staffing_model import capacidade_5x2_por_hora

    need = zero_need.copy()
    need.loc[
        (need["dia_ordem"] == 1)
        & (need["hora"].isin([8, 11, 17])),
        "necessidade_ajustada",
    ] = 2
    coverage_12x36 = zero_need[["dia_ordem", "hora"]].copy()
    coverage_12x36["capacidade_12x36"] = 0

    solution = otimizar_5x2(need, coverage_12x36)

    assert solution["base"] == {"x5_8": 2, "x5_9": 2, "total": 4}
    for day in range(1, 6):
        for entry in (8, 9):
            meal_counts = solution["pausas"][day][entry]["base"]
            assert sum(meal_counts.values()) == solution["base"][f"x5_{entry}"]
            assert set(meal_counts).issubset({11, 12, 13, 14})

    coverage = montar_cobertura(
        need, {"cobertura_12x36": coverage_12x36}, solution
    )
    assert (
        coverage.query("dia_ordem == 1 and hora == 8")[
            "capacidade_planejada"
        ].item()
        >= 2
    )
    assert (
        coverage.query("dia_ordem == 1 and hora == 17")[
            "capacidade_planejada"
        ].item()
        >= 2
    )


def test_5x2_base_minimizes_total_across_entry_windows(zero_need):
    need = zero_need.copy()
    need.loc[
        (need["dia_ordem"] == 1) & (need["hora"].isin([8, 17])),
        "necessidade_ajustada",
    ] = 2
    coverage_12x36 = zero_need[["dia_ordem", "hora"]].copy()
    coverage_12x36["capacidade_12x36"] = 0

    solution = otimizar_5x2(need, coverage_12x36)

    assert solution["base"] == {"x5_8": 2, "x5_9": 2, "total": 4}
    from staffing_model import capacidade_5x2_por_hora

    assert [
        hour
        for hour in range(24)
        if capacidade_5x2_por_hora(1, hour, 1, 8) > 0
    ] == list(range(8, 17))
    assert [
        hour
        for hour in range(24)
        if capacidade_5x2_por_hora(1, hour, 1, 9) > 0
    ] == list(range(9, 18))


def test_5x2_relief_preserves_base_profile_during_lunch(zero_need):
    need = zero_need.copy()
    need.loc[
        (need["dia_ordem"] == 1) & (need["hora"].isin([8, 11, 17])),
        "necessidade_ajustada",
    ] = [2, 4, 2]
    coverage_12x36 = zero_need[["dia_ordem", "hora"]].copy()
    coverage_12x36["capacidade_12x36"] = 0

    solution = otimizar_5x2(need, coverage_12x36)

    assert solution["base"] == {"x5_8": 2, "x5_9": 2, "total": 4}
    assert solution["relevo"]["total"] == 2
    for day in range(1, 6):
        for entry in (8, 9):
            pauses = solution["pausas"][day][entry]
            assert sum(pauses["base"].values()) == solution["base"][
                f"x5_{entry}"
            ]
            assert sum(pauses["relevo"].values()) == solution["relevo"][
                f"x5_{entry}"
            ]
            assert set(pauses["base"]).issubset({11, 12, 13, 14})
            assert set(pauses["relevo"]).issubset({11, 12, 13, 14})

    staffing = {"cobertura_12x36": coverage_12x36}
    coverage = montar_cobertura(need, staffing, solution)
    monday = coverage.query("dia_ordem == 1").set_index("hora")
    assert monday.loc[9, "capacidade_5x2"] > 4
    assert all(monday.loc[hour, "capacidade_5x2"] >= 4 for hour in range(11, 15))


def test_5x2_zero_demand_has_no_base_or_relief(zero_need):
    coverage_12x36 = zero_need[["dia_ordem", "hora"]].copy()
    coverage_12x36["capacidade_12x36"] = 0

    solution = otimizar_5x2(zero_need, coverage_12x36)

    assert solution["base"] == {"x5_8": 0, "x5_9": 0, "total": 0}
    assert solution["relevo"] == {"x5_8": 0, "x5_9": 0, "total": 0}


def test_headcount_effective_counts_base_and_relief_by_schedule():
    staffing = {
        "postos_base": {"dia": 2, "noite": 3},
        "postos_relevo": {"dia": 1, "noite": 2},
    }
    sol5 = {
        "base": {"x5_8": 4, "x5_9": 2, "total": 6},
        "relevo": {"x5_8": 1, "x5_9": 1, "total": 2},
    }

    result = staffing_model.construir_headcount_efetivo(staffing, sol5)

    assert result["hc_12x36"] == (2 + 3 + 1 + 2) * 2 == 16
    assert result["hc_5x2"] == 4 + 2 + 1 + 1 == 8
    assert result["hc_total"] == 24
    assert set(result["detalhamento"].columns) == {
        "Camada",
        "Unidade",
        "Quantidade dimensionada",
        "HC efetivo",
    }
    assert len(result["detalhamento"]) == 8
    assert set(result["detalhamento"]["Camada"]) == {"Base", "Relevo"}
    assert result["detalhamento"]["Unidade"].str.startswith(
        "postos 12x36"
    ).sum() == 4
    assert result["detalhamento"]["Unidade"].str.startswith(
        "pessoas 5x2"
    ).sum() == 4
    assert "incrementos" not in " ".join(result["detalhamento"]["Camada"]).lower()
    assert result["detalhamento"]["HC efetivo"].sum() == 24
    assert {
        "HC efetivo 12x36",
        "HC efetivo 5x2",
        "HC efetivo total",
    }.issubset(set(result["tabela"]["Camada"]))
    assert result["tabela"].iloc[-1]["Camada"] == "HC efetivo total"
    assert result["tabela"].iloc[-1]["HC efetivo"] == 24


def test_peak_capacity_summary_uses_earliest_tied_day_and_hour():
    coverage = pd.DataFrame(
        [
            {
                "dia_ordem": 2,
                "dia_da_semana": "terça",
                "hora": 9,
                "capacidade_planejada": 12,
            },
            {
                "dia_ordem": 1,
                "dia_da_semana": "segunda",
                "hora": 10,
                "capacidade_planejada": 12,
            },
            {
                "dia_ordem": 1,
                "dia_da_semana": "segunda",
                "hora": 9,
                "capacidade_planejada": 12,
            },
        ]
    )

    peak = staffing_model.resumir_pico_capacidade_planejada(coverage)

    assert peak == {"pessoas": 12, "dia_da_semana": "segunda", "hora": 9}


@pytest.mark.parametrize(
    "missing_column",
    ["dia_ordem", "dia_da_semana", "hora", "capacidade_planejada"],
)
def test_peak_capacity_summary_names_missing_required_column(missing_column):
    coverage = pd.DataFrame(
        [
            {
                "dia_ordem": 1,
                "dia_da_semana": "segunda",
                "hora": 9,
                "capacidade_planejada": 12,
            }
        ]
    ).drop(columns=[missing_column])

    with pytest.raises(ValueError, match=missing_column):
        staffing_model.resumir_pico_capacidade_planejada(coverage)


def test_peak_capacity_summary_rejects_empty_coverage():
    coverage = pd.DataFrame(
        columns=[
            "dia_ordem",
            "dia_da_semana",
            "hora",
            "capacidade_planejada",
        ]
    )

    with pytest.raises(ValueError, match="vazio"):
        staffing_model.resumir_pico_capacidade_planejada(coverage)
