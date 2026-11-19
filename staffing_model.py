import math

import pandas as pd
import pulp


def build_day_map() -> dict[str, int]:
    return {
        "segunda-feira": 1, "segunda": 1,
        "terça-feira": 2, "terca-feira": 2, "terça": 2, "terca": 2,
        "quarta-feira": 3, "quarta": 3,
        "quinta-feira": 4, "quinta": 4,
        "sexta-feira": 5, "sexta": 5,
        "sábado": 6, "sabado": 6,
        "domingo": 7,
    }


def normalize_day(day: str) -> str:
    return str(day).strip().lower()


def normalize_demand(df: pd.DataFrame) -> pd.DataFrame:
    required = {"dia_da_semana", "hora", "volume_mensagens"}
    if not required.issubset(df.columns):
        raise ValueError(
            "A planilha precisa conter dia_da_semana, hora e volume_mensagens."
        )

    data = df[["dia_da_semana", "hora", "volume_mensagens"]].copy()
    data["dia_da_semana"] = data["dia_da_semana"].map(normalize_day)
    data["dia_ordem"] = data["dia_da_semana"].map(build_day_map())
    if data["dia_ordem"].isna().any():
        invalid = data.loc[data["dia_ordem"].isna(), "dia_da_semana"].unique()
        raise ValueError(f"Dias da semana inválidos: {invalid.tolist()}")

    data["hora"] = pd.to_numeric(data["hora"], errors="coerce")
    data["volume_mensagens"] = pd.to_numeric(
        data["volume_mensagens"], errors="coerce"
    )
    if data[["hora", "volume_mensagens"]].isna().any().any():
        raise ValueError("Hora e volume_mensagens precisam ser numéricos.")
    if not data[["hora", "volume_mensagens"]].map(math.isfinite).all().all():
        raise ValueError("Hora e volume_mensagens precisam ser finitos.")
    if not data["hora"].mod(1).eq(0).all():
        raise ValueError("A coluna hora precisa ser inteira.")
    if not data["hora"].between(0, 23).all():
        raise ValueError("A coluna hora precisa estar entre 0 e 23.")
    if (data["volume_mensagens"] < 0).any():
        raise ValueError("volume_mensagens não pode ser negativo.")
    if data.duplicated(["dia_ordem", "hora"]).any():
        raise ValueError("Há mais de uma linha para o mesmo dia e hora.")
    data["hora"] = data["hora"].astype(int)

    full_grid = pd.MultiIndex.from_product(
        [range(1, 8), range(24)], names=["dia_ordem", "hora"]
    )
    result = (
        data.set_index(["dia_ordem", "hora"])[["volume_mensagens"]]
        .reindex(full_grid, fill_value=0)
        .reset_index()
    )
    day_names = {
        1: "segunda",
        2: "terça",
        3: "quarta",
        4: "quinta",
        5: "sexta",
        6: "sábado",
        7: "domingo",
    }
    result["dia_da_semana"] = result["dia_ordem"].map(day_names)
    result["volume_mensagens"] = result["volume_mensagens"].astype(float)
    return result[["dia_ordem", "dia_da_semana", "hora", "volume_mensagens"]]


def aplicar_shrinkage(
    df_need: pd.DataFrame, enabled: bool, factor: float
) -> pd.DataFrame:
    if not 0 <= factor < 1:
        raise ValueError("O fator de férias precisa estar entre 0 e 1.")
    result = df_need.copy()
    needs = result["atendentes_necessarios"].astype(int)
    if enabled:
        result["necessidade_ajustada"] = needs.map(
            lambda need: math.ceil(need / (1 - factor)) if need > 0 else 0
        )
    else:
        result["necessidade_ajustada"] = needs
    return result


def dimensionar_12x36(
    df_need: pd.DataFrame,
    janela_pausa_diurna: tuple[int, int] = (11, 15),
    pausa_noturna: tuple[int, int] = (21, 22),
    janela_pausa_noturna: tuple[int, int] = (21, 1),
) -> dict:
    day_slots = list(range(janela_pausa_diurna[0], janela_pausa_diurna[1]))
    if len(day_slots) < 2 or janela_pausa_diurna[1] > 18:
        raise ValueError("A janela da pausa diurna precisa permitir duas pausas distintas.")
    night_hours = [18, 19, 20, 21, 22, 23, 0, 1, 2, 3, 4, 5]
    night_pause_start, night_pause_end = janela_pausa_noturna
    if night_pause_end > night_pause_start:
        night_pause_slots = list(range(night_pause_start, night_pause_end))
    else:
        night_pause_slots = list(range(night_pause_start, 24)) + list(
            range(0, night_pause_end)
        )
    if pausa_noturna[0] not in night_hours or pausa_noturna[1] != pausa_noturna[0] + 1:
        raise ValueError("A pausa noturna deve ser um intervalo de uma hora no turno.")
    if (
        not night_pause_slots
        or not set(night_pause_slots).issubset(night_hours)
        or pausa_noturna[0] not in night_pause_slots
    ):
        raise ValueError(
            "A janela da pausa noturna precisa estar no turno e conter a pausa titular."
        )
    night_relief_slots = [
        hour for hour in night_pause_slots if hour != pausa_noturna[0]
    ]
    if not night_relief_slots:
        raise ValueError(
            "A janela noturna precisa permitir uma pausa distinta para o relevo."
        )
    required_columns = {"dia_ordem", "hora", "necessidade_ajustada"}
    if not required_columns.issubset(df_need.columns):
        raise ValueError("df_need precisa conter dia_ordem, hora e necessidade_ajustada.")

    day_needs = df_need.loc[
        df_need["hora"].isin([6, 7]), "necessidade_ajustada"
    ]
    night_needs = df_need.loc[
        df_need["hora"].isin(night_hours), "necessidade_ajustada"
    ]
    day_base = int(day_needs.max()) if not day_needs.empty else 0
    night_base = int(night_needs.max()) if not night_needs.empty else 0

    problem = pulp.LpProblem("Relevo_12x36", pulp.LpMinimize)
    day_relief = problem.add_variable(
        "day_relief_posts", lowBound=0, cat=pulp.LpInteger
    )
    night_relief = problem.add_variable(
        "night_relief_posts", lowBound=0, cat=pulp.LpInteger
    )
    day_base_break = {
        (day, hour): problem.add_variable(
            f"day_base_break_{day}_{hour}",
            lowBound=0,
            upBound=day_base,
            cat=pulp.LpInteger,
        )
        for day in range(1, 8)
        for hour in day_slots
    }
    day_relief_break = {
        (day, hour): problem.add_variable(
            f"day_relief_break_{day}_{hour}",
            lowBound=0,
            cat=pulp.LpInteger,
        )
        for day in range(1, 8)
        for hour in day_slots
    }
    night_base_break = {
        (day, hour): problem.add_variable(
            f"night_base_break_{day}_{hour}",
            lowBound=0,
            upBound=night_base,
            cat=pulp.LpInteger,
        )
        for day in range(1, 8)
        for hour in night_pause_slots
    }
    night_relief_break = {
        (day, hour): problem.add_variable(
            f"night_relief_break_{day}_{hour}",
            lowBound=0,
            cat=pulp.LpInteger,
        )
        for day in range(1, 8)
        for hour in night_pause_slots
    }

    for day in range(1, 8):
        problem += (
            pulp.lpSum(day_base_break[(day, hour)] for hour in day_slots)
            == day_base,
            f"every_day_base_post_has_meal_{day}",
        )
        problem += (
            pulp.lpSum(day_relief_break[(day, hour)] for hour in day_slots)
            == day_relief,
            f"every_day_relief_post_has_meal_{day}",
        )
        for hour in day_slots:
            problem += (
                day_relief_break[(day, hour)] <= day_relief,
                f"bound_day_relief_meals_{day}_{hour}",
            )
            problem += (
                day_base_break[(day, hour)]
                + day_relief_break[(day, hour)]
                <= day_relief,
                f"preserve_day_base_during_meal_{day}_{hour}",
            )

        problem += (
            pulp.lpSum(
                night_base_break[(day, hour)]
                for hour in night_pause_slots
            )
            == night_base,
            f"every_night_base_post_has_meal_{day}",
        )
        problem += (
            pulp.lpSum(
                night_relief_break[(day, hour)]
                for hour in night_pause_slots
            )
            == night_relief,
            f"every_night_relief_post_has_meal_{day}",
        )
        for hour in night_pause_slots:
            problem += (
                night_base_break[(day, hour)]
                + night_relief_break[(day, hour)]
                <= night_relief,
                f"preserve_night_base_during_meal_{day}_{hour}",
            )

    problem += day_relief + night_relief
    status = problem.solve(pulp.HiGHS(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(
            f"Não foi possível dimensionar o relevo 12x36: {pulp.LpStatus[status]}"
        )
    optimal_day_relief = int(round(pulp.value(day_relief)))
    optimal_night_relief = int(round(pulp.value(night_relief)))
    problem += day_relief == optimal_day_relief, "fix_optimal_day_relief"
    problem += night_relief == optimal_night_relief, "fix_optimal_night_relief"

    problem.setObjective(
        pulp.lpSum(
            hour
            * (
                day_base_break[(day, hour)]
                + day_relief_break[(day, hour)]
            )
            for day in range(1, 8)
            for hour in day_slots
        )
        + pulp.lpSum(
            hour * (night_base_break[(day, hour)] + night_relief_break[(day, hour)])
            for day in range(1, 8)
            for hour in night_pause_slots
        )
    )
    status = problem.solve(pulp.HiGHS(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(
            f"Não foi possível alocar as pausas 12x36: {pulp.LpStatus[status]}"
        )

    def pause_counts(variables, days, hours):
        return {
            day: {
                hour: int(round(pulp.value(variables[(day, hour)])))
                for hour in hours
                if pulp.value(variables[(day, hour)]) > 0.5
            }
            for day in days
        }

    pauses = {
        "dia": {
            "base": pause_counts(day_base_break, range(1, 8), day_slots),
            "relevo": pause_counts(day_relief_break, range(1, 8), day_slots),
        },
        "noite": {
            "base": pause_counts(night_base_break, range(1, 8), night_pause_slots),
            "relevo": pause_counts(night_relief_break, range(1, 8), night_pause_slots),
        },
    }
    staffing = {
        "postos_base": {"dia": day_base, "noite": night_base},
        "postos_relevo": {
            "dia": optimal_day_relief,
            "noite": optimal_night_relief,
        },
        "pausas": pauses,
        "horas_pausa_permitidas": {
            "dia": day_slots,
            "noite": night_pause_slots,
        },
    }
    staffing["cobertura_12x36"] = _coverage_12x36_for_staff(
        df_need, staffing
    )
    return staffing


def _coverage_12x36_for_staff(
    df_need: pd.DataFrame, staffing: dict
) -> pd.DataFrame:
    rows = []
    day_break_by_day = {}
    night_break_by_shift = {}
    for day in range(1, 8):
        day_break_by_day[day] = {}
        for layer in ("base", "relevo"):
            for start, count in staffing["pausas"]["dia"][layer][day].items():
                day_break_by_day[day][start] = (
                    day_break_by_day[day].get(start, 0) + count
                )

        night_break_by_shift[day] = {}
        for layer in ("base", "relevo"):
            for start, count in staffing["pausas"]["noite"][layer][day].items():
                night_break_by_shift[day][start] = (
                    night_break_by_shift[day].get(start, 0) + count
                )

    day_positions = (
        staffing["postos_base"]["dia"] + staffing["postos_relevo"]["dia"]
    )
    night_positions = (
        staffing["postos_base"]["noite"] + staffing["postos_relevo"]["noite"]
    )
    for row in df_need.itertuples(index=False):
        day_order = int(row.dia_ordem)
        hour = int(row.hora)
        day_index = day_order - 1
        if 6 <= hour <= 17:
            active = day_positions - day_break_by_day[day_order].get(hour, 0)
        elif hour >= 18 or hour <= 5:
            shift_day = day_order if hour >= 18 else ((day_index - 1) % 7) + 1
            active = night_positions - night_break_by_shift[shift_day].get(hour, 0)
        else:
            active = 0
        rows.append(
            {
                "dia_ordem": day_order,
                "hora": hour,
                "capacidade_12x36": max(0, active),
            }
        )
    return pd.DataFrame(rows)

def calcular_cobertura_12x36(
    df_need: pd.DataFrame, staffing: dict
) -> pd.DataFrame:
    return staffing["cobertura_12x36"].copy()


def capacidade_5x2_por_hora(
    dia_ordem: int,
    hora: int,
    quantidade: int,
    entrada: int,
    pausas_por_hora: dict[int, int] | None = None,
) -> int:
    if entrada == 8:
        active_hours = set(range(8, 17))
    elif entrada == 9:
        active_hours = set(range(9, 18))
    else:
        raise ValueError("A entrada 5x2 deve ser 8 ou 9.")
    if dia_ordem > 5 or quantidade <= 0:
        return 0
    breaks = pausas_por_hora or {}
    if any(count < 0 or count > quantidade for count in breaks.values()):
        raise ValueError("A quantidade em pausa deve estar entre zero e a equipe.")
    if hora not in active_hours:
        return 0
    return quantidade - int(breaks.get(hora, 0))


def otimizar_5x2(
    df_need: pd.DataFrame, cobertura_12x36: pd.DataFrame
) -> dict:
    required_need = {"dia_ordem", "hora", "necessidade_ajustada"}
    required_coverage = {"dia_ordem", "hora", "capacidade_12x36"}
    if not required_need.issubset(df_need.columns):
        raise ValueError("df_need precisa conter dia_ordem, hora e necessidade_ajustada.")
    if not required_coverage.issubset(cobertura_12x36.columns):
        raise ValueError(
            "cobertura_12x36 precisa conter dia_ordem, hora e capacidade_12x36."
        )

    need = {
        (int(row.dia_ordem), int(row.hora)): int(row.necessidade_ajustada)
        for row in df_need.itertuples(index=False)
    }
    cap12 = {
        (int(row.dia_ordem), int(row.hora)): int(row.capacidade_12x36)
        for row in cobertura_12x36.itertuples(index=False)
    }
    entries = (8, 9)
    lunch_hours = tuple(range(11, 15))
    active_hours = {8: set(range(8, 17)), 9: set(range(9, 18))}

    base_problem = pulp.LpProblem("Base_5x2_residual", pulp.LpMinimize)
    base_groups = {
        entry: base_problem.add_variable(
            f"base_x5_{entry}", lowBound=0, cat=pulp.LpInteger
        )
        for entry in entries
    }
    for day in range(1, 6):
        for hour in range(8, 18):
            residual = max(
                need.get((day, hour), 0) - cap12.get((day, hour), 0),
                0,
            )
            assigned = sum(
                base_groups[entry]
                for entry in entries
                if hour in active_hours[entry]
            )
            base_problem += (
                assigned >= residual,
                f"cover_residual_{day}_{hour}",
            )
    base_problem += pulp.lpSum(base_groups.values())
    status = base_problem.solve(pulp.HiGHS(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(f"Modelo de base 5x2 inviável. Status: {pulp.LpStatus[status]}")
    base_total = int(round(sum(pulp.value(v) for v in base_groups.values())))
    base_problem += (
        pulp.lpSum(base_groups.values()) == base_total,
        "fix_minimum_5x2_base_total",
    )
    base_problem.setObjective(base_groups[8])
    status = base_problem.solve(pulp.HiGHS(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError("Não foi possível escolher os turnos da base 5x2.")
    base_counts = {
        entry: int(round(pulp.value(base_groups[entry]))) for entry in entries
    }

    relief_problem = pulp.LpProblem("Relevo_5x2_almoco", pulp.LpMinimize)
    relief_groups = {
        entry: relief_problem.add_variable(
            f"relief_x5_{entry}", lowBound=0, cat=pulp.LpInteger
        )
        for entry in entries
    }
    base_break = {
        (day, entry, hour): relief_problem.add_variable(
            f"base_lunch_5x2_{day}_{entry}_{hour}",
            lowBound=0,
            upBound=base_counts[entry],
            cat=pulp.LpInteger,
        )
        for day in range(1, 6)
        for entry in entries
        for hour in lunch_hours
    }
    relief_break = {
        (day, entry, hour): relief_problem.add_variable(
            f"relief_lunch_5x2_{day}_{entry}_{hour}",
            lowBound=0,
            cat=pulp.LpInteger,
        )
        for day in range(1, 6)
        for entry in entries
        for hour in lunch_hours
    }
    for day in range(1, 6):
        for entry in entries:
            relief_problem += (
                pulp.lpSum(base_break[(day, entry, hour)] for hour in lunch_hours)
                == base_counts[entry],
                f"every_base_5x2_has_lunch_{day}_{entry}",
            )
            relief_problem += (
                pulp.lpSum(relief_break[(day, entry, hour)] for hour in lunch_hours)
                == relief_groups[entry],
                f"every_relief_5x2_has_lunch_{day}_{entry}",
            )
            for hour in lunch_hours:
                relief_problem += (
                    relief_break[(day, entry, hour)] <= relief_groups[entry],
                    f"bound_relief_5x2_lunch_{day}_{entry}_{hour}",
                )
        for hour in range(8, 18):
            base_profile = sum(
                base_counts[entry]
                for entry in entries
                if hour in active_hours[entry]
            )
            relief_profile = sum(
                relief_groups[entry]
                for entry in entries
                if hour in active_hours[entry]
            )
            breaks_at_hour = sum(
                base_break[(day, entry, hour)]
                + relief_break[(day, entry, hour)]
                for entry in entries
                if hour in lunch_hours and hour in active_hours[entry]
            )
            relief_problem += (
                base_profile + relief_profile - breaks_at_hour >= base_profile,
                f"preserve_base_profile_{day}_{hour}",
            )

    relief_problem += pulp.lpSum(relief_groups.values())
    status = relief_problem.solve(pulp.HiGHS(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(f"Modelo de relevo 5x2 inviável. Status: {pulp.LpStatus[status]}")
    relief_total = int(round(sum(pulp.value(v) for v in relief_groups.values())))
    relief_problem += (
        pulp.lpSum(relief_groups.values()) == relief_total,
        "fix_minimum_5x2_relief_total",
    )
    relief_problem.setObjective(relief_groups[8])
    status = relief_problem.solve(pulp.HiGHS(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError("Não foi possível escolher os turnos do relevo 5x2.")
    relief_counts = {
        entry: int(round(pulp.value(relief_groups[entry])))
        for entry in entries
    }
    for entry in entries:
        relief_problem += (
            relief_groups[entry] == relief_counts[entry],
            f"fix_relief_5x2_entry_{entry}",
        )
    relief_problem.setObjective(
        pulp.lpSum(
            hour
            * (base_break[(day, entry, hour)] + relief_break[(day, entry, hour)])
            for day in range(1, 6)
            for entry in entries
            for hour in lunch_hours
        )
    )
    status = relief_problem.solve(pulp.HiGHS(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError("Não foi possível distribuir os almoços 5x2.")

    pauses = {
        day: {
            entry: {
                layer: (
                    {
                        hour: int(
                            round(pulp.value(variables[(day, entry, hour)]))
                        )
                        for hour in lunch_hours
                        if pulp.value(variables[(day, entry, hour)]) > 0.5
                    }
                    if day <= 5
                    else {}
                )
                for layer, variables in (
                    ("base", base_break),
                    ("relevo", relief_break),
                )
            }
            for entry in entries
        }
        for day in range(1, 8)
    }
    return {
        "base": {
            "x5_8": base_counts[8],
            "x5_9": base_counts[9],
            "total": base_total,
        },
        "relevo": {
            "x5_8": relief_counts[8],
            "x5_9": relief_counts[9],
            "total": relief_total,
        },
        "pausas": pauses,
    }


def montar_cobertura(
    df_need: pd.DataFrame, staffing: dict, sol5: dict
) -> pd.DataFrame:
    need_cols = [
        col for col in
        ["dia_ordem", "dia_da_semana", "hora", "volume_mensagens",
         "atendentes_necessarios", "necessidade_ajustada"]
        if col in df_need.columns
    ]
    result = df_need[need_cols].copy()
    if "necessidade_ajustada" not in result:
        result["necessidade_ajustada"] = result["atendentes_necessarios"]
    result = result.rename(
        columns={"necessidade_ajustada": "necessidade_estimada"}
    )
    capacity_12x36 = calcular_cobertura_12x36(df_need, staffing)
    result = result.merge(
        capacity_12x36, on=["dia_ordem", "hora"], how="left", validate="one_to_one"
    )
    result["capacidade_12x36"] = result["capacidade_12x36"].fillna(0).astype(int)

    def active_5x2(row, layer, entry):
        day = int(row["dia_ordem"])
        group_breaks = sol5["pausas"][day][entry][layer]
        return capacidade_5x2_por_hora(
            day,
            int(row["hora"]),
            int(sol5[layer][f"x5_{entry}"]),
            entry,
            group_breaks,
        )

    result["capacidade_5x2"] = result.apply(
        lambda row: sum(
            active_5x2(row, layer, entry)
            for layer in ("base", "relevo")
            for entry in (8, 9)
        ),
        axis=1,
    )
    result["capacidade_planejada"] = (
        result["capacidade_12x36"] + result["capacidade_5x2"]
    )
    result["gap"] = (
        result["necessidade_estimada"] - result["capacidade_planejada"]
    ).clip(lower=0)
    result["sobrecobertura"] = (
        result["capacidade_planejada"] - result["necessidade_estimada"]
    ).clip(lower=0)
    day_names = {
        1: "segunda", 2: "terça", 3: "quarta", 4: "quinta",
        5: "sexta", 6: "sábado", 7: "domingo",
    }
    if "dia_da_semana" not in result:
        result["dia_da_semana"] = result["dia_ordem"].map(day_names)
    return result.sort_values(["dia_ordem", "hora"]).reset_index(drop=True)

def construir_headcount_efetivo(
    staffing: dict, sol5: dict
) -> dict[str, object]:
    details = []
    for shift, label in (("dia", "diurno"), ("noite", "noturno")):
        for layer, title in (("base", "Base"), ("relevo", "Relevo")):
            quantity = int(staffing[f"postos_{layer}"][shift])
            details.append(
                {
                    "Camada": title,
                    "Unidade": f"postos 12x36 — {label}",
                    "Quantidade dimensionada": quantity if layer == "base" else 0,
                    "HC efetivo": quantity * 2,
                }
            )
    for entry, label in ((8, "08:00"), (9, "09:00")):
        for layer, title in (("base", "Base"), ("relevo", "Relevo")):
            quantity = int(sol5[layer][f"x5_{entry}"])
            details.append(
                {
                    "Camada": title,
                    "Unidade": f"pessoas 5x2 — entrada {label}",
                    "Quantidade dimensionada": quantity if layer == "base" else 0,
                    "HC efetivo": quantity,
                }
            )
    detail = pd.DataFrame(
        details,
        columns=[
            "Camada",
            "Unidade",
            "Quantidade dimensionada",
            "HC efetivo",
        ],
    )
    hc_12x36 = int(
        detail.loc[detail["Unidade"].str.startswith("postos 12x36"), "HC efetivo"].sum()
    )
    hc_5x2 = int(
        detail.loc[detail["Unidade"].str.startswith("pessoas 5x2"), "HC efetivo"].sum()
    )
    base12 = int(
        detail.loc[
            (detail["Unidade"].str.startswith("postos 12x36"))
            & (detail["Camada"] == "Base"),
            "Quantidade dimensionada",
        ].sum()
    )
    relief12 = int(
        detail.loc[
            (detail["Unidade"].str.startswith("postos 12x36"))
            & (detail["Camada"] == "Relevo"),
            "HC efetivo",
        ].sum() // 2
    )
    base5 = int(
        detail.loc[
            (detail["Unidade"].str.startswith("pessoas 5x2"))
            & (detail["Camada"] == "Base"),
            "Quantidade dimensionada",
        ].sum()
    )
    relief5 = int(
        detail.loc[
            (detail["Unidade"].str.startswith("pessoas 5x2"))
            & (detail["Camada"] == "Relevo"),
            "HC efetivo",
        ].sum()
    )
    columns = list(detail.columns)
    summary_rows = [
        ("Total 12x36 — base", "postos 12x36", base12, base12 * 2),
        ("Total 12x36 — relevo", "postos 12x36", 0, relief12 * 2),
        ("HC efetivo 12x36", "pessoas", base12, hc_12x36),
        ("Total 5x2 — base", "pessoas 5x2", base5, base5),
        ("Total 5x2 — relevo", "pessoas 5x2", 0, relief5),
        ("HC efetivo 5x2", "pessoas", base5, hc_5x2),
        ("HC efetivo total", "pessoas", base12 + base5, hc_12x36 + hc_5x2),
    ]
    summary = pd.DataFrame(
        summary_rows, columns=["Camada", "Unidade", "Quantidade dimensionada", "HC efetivo"]
    )
    return {
        "detalhamento": detail,
        "tabela": pd.concat([detail, summary], ignore_index=True)[columns],
        "hc_12x36": hc_12x36,
        "hc_5x2": hc_5x2,
        "hc_total": hc_12x36 + hc_5x2,
    }


def resumir_pico_capacidade_planejada(
    df_coverage: pd.DataFrame,
) -> dict[str, object]:
    required_columns = {
        "dia_ordem",
        "dia_da_semana",
        "hora",
        "capacidade_planejada",
    }
    missing_columns = required_columns.difference(df_coverage.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(
            f"df_coverage precisa conter as colunas: {missing}."
        )
    if df_coverage.empty:
        raise ValueError("df_coverage não pode estar vazio.")

    max_capacity = df_coverage["capacidade_planejada"].max()
    peak_row = (
        df_coverage[df_coverage["capacidade_planejada"] == max_capacity]
        .sort_values(["dia_ordem", "hora"])
        .iloc[0]
    )
    return {
        "pessoas": int(peak_row["capacidade_planejada"]),
        "dia_da_semana": str(peak_row["dia_da_semana"]),
        "hora": int(peak_row["hora"]),
    }


def preparar_dados_pausas_por_dia(
    pausas: pd.DataFrame,
) -> pd.DataFrame:
    required_columns = {
        "dia_ordem",
        "turno",
        "inicio_pausa",
        "pausa",
        "quantidade",
    }
    missing_columns = required_columns.difference(pausas.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"pausas precisa conter as colunas: {missing}.")
    columns = [
        "dia_ordem",
        "turno",
        "inicio_pausa",
        "pausa",
        "posicoes_em_pausa",
    ]
    if pausas.empty:
        return pd.DataFrame(columns=columns)
    return (
        pausas.groupby(
            ["dia_ordem", "turno", "inicio_pausa", "pausa"], as_index=False
        )["quantidade"]
        .sum()
        .rename(columns={"quantidade": "posicoes_em_pausa"})
        .sort_values(["dia_ordem", "inicio_pausa", "turno"])
        .reset_index(drop=True)[columns]
    )


def preparar_dados_grafico(
    df_coverage: pd.DataFrame,
    dias_selecionados: list[str],
    metricas_selecionadas: list[str] | None = None,
) -> pd.DataFrame:
    columns = ["dia_da_semana", "hora", "metrica", "pessoas"]
    if not dias_selecionados or metricas_selecionadas == []:
        return pd.DataFrame(columns=columns)

    selected = df_coverage[
        df_coverage["dia_da_semana"].isin(dias_selecionados)
    ]
    long = selected.melt(
        id_vars=["dia_da_semana", "hora"],
        value_vars=["necessidade_estimada", "capacidade_planejada"],
        var_name="metrica",
        value_name="pessoas",
    )
    long["metrica"] = long["metrica"].map(
        {
            "necessidade_estimada": "Necessidade estimada",
            "capacidade_planejada": "Capacidade planejada",
        }
    )
    if metricas_selecionadas is not None:
        long = long[long["metrica"].isin(metricas_selecionadas)]
    return long[columns].sort_values(
        ["dia_da_semana", "metrica", "hora"]
    ).reset_index(drop=True)


def erlang_c(c: int, lam: float, mu: float) -> float:
    rho = lam / (c * mu)
    if rho >= 1:
        return 1.0
    total = sum((lam / mu) ** n / math.factorial(n) for n in range(c))
    top = ((lam / mu) ** c / math.factorial(c)) * (1 / (1 - rho))
    return top / (total + top)


def tempo_espera_min(c: int, lam: float, mu: float) -> float:
    probability_wait = erlang_c(c, lam, mu)
    if probability_wait == 1:
        return float("inf")
    return probability_wait * (1 / mu) / (c - (lam / mu))


def calcular_min_atendentes(
    lam: float,
    tmr: float,
    simult: int,
    limite_espera_min: float,
    efetividade: float,
) -> tuple[int, float]:
    if lam <= 0:
        return 0, 0.0

    mu = (simult / tmr) * efetividade
    c = 1
    while True:
        wait = tempo_espera_min(c, lam, mu)
        if wait <= limite_espera_min:
            return c, round(wait, 2)
        c += 1
        if c > 200:
            return c, float("inf")
