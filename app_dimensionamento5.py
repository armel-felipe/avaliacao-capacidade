from datetime import datetime
from io import BytesIO

import altair as alt
import pandas as pd
import streamlit as st

from staffing_model import (
    aplicar_shrinkage,
    calcular_min_atendentes,
    dimensionar_12x36,
    montar_cobertura,
    normalize_demand,
    otimizar_5x2,
    preparar_dados_grafico,
    preparar_dados_pausas_por_dia,
    construir_headcount_efetivo,
    resumir_pico_capacidade_planejada,
)


st.set_page_config(page_title="Dimensionamento de capacidade", layout="wide")
st.title("Dimensionamento de capacidade")
st.caption(
    "Estimativa para a fila compartilhada do conjunto de canais informado na planilha. "
    "Capacidade planejada não representa disponibilidade observada."
)

with st.sidebar:
    st.header("Configuração")
    uploaded_file = st.file_uploader("Planilha de demanda (.xlsx)", type=["xlsx"])
    canal = st.selectbox(
        "Conjunto de canais da planilha",
        ["WhatsApp", "WebWidget", "WhatsApp + WebWidget", "Chat", "E-mail", "Outro"],
    )
    st.caption(
        "`volume_mensagens` deve ser o total agregado do conjunto selecionado. "
        "TMR, simultaneidade e efetividade devem refletir esse mesmo conjunto."
    )

    st.subheader("Parâmetros de atendimento")
    tempo_max_espera = st.number_input(
        "Espera-alvo (min)", min_value=1, value=5
    )
    tmr = st.number_input("TMR (min)", min_value=1, value=30)
    simult = st.number_input("Simultaneidade", min_value=1, value=7)
    efetividade_percentual = st.number_input(
        "Efetividade (%)", min_value=10, max_value=100, value=85
    )

    st.subheader("Férias (shrinkage)")
    aplicar_ferias = st.checkbox("Aplicar fator de férias", value=True)
    ferias_fator = st.number_input(
        "Fator de férias", min_value=0.0, max_value=0.5, value=1 / 12, step=0.01
    )

if uploaded_file is None:
    st.header("Comece pela planilha")
    st.markdown("Carregue a planilha na configuração lateral para ver o dimensionamento.")
    st.caption(
        "A planilha deve conter `dia_da_semana`, `hora` e `volume_mensagens`."
    )
    st.stop()

if uploaded_file is not None:
    try:
        source = pd.read_excel(uploaded_file)
        demand = normalize_demand(source)
    except (ValueError, TypeError) as error:
        st.error(f"Não foi possível ler a demanda: {error}")
        st.stop()

    effective = efetividade_percentual / 100
    need_rows = []
    for row in demand.itertuples(index=False):
        agents, wait = calcular_min_atendentes(
            lam=float(row.volume_mensagens) / 60,
            tmr=float(tmr),
            simult=int(simult),
            limite_espera_min=float(tempo_max_espera),
            efetividade=effective,
        )
        need_rows.append(
            {
                **row._asdict(),
                "atendentes_necessarios": agents,
                "tempo_estimado_espera_min": wait,
            }
        )
    df_need = aplicar_shrinkage(
        pd.DataFrame(need_rows), enabled=aplicar_ferias, factor=float(ferias_fator)
    )

    try:
        staffing = dimensionar_12x36(
            df_need,
            janela_pausa_diurna=(11, 15),
            pausa_noturna=(21, 22),
            janela_pausa_noturna=(21, 1),
        )
        coverage_12x36 = staffing["cobertura_12x36"]
        sol5 = otimizar_5x2(df_need, coverage_12x36)
        df_coverage = montar_cobertura(df_need, staffing, sol5)
    except (RuntimeError, ValueError) as error:
        st.error(f"Não foi possível dimensionar a escala: {error}")
        st.stop()

    headcount_result = construir_headcount_efetivo(staffing, sol5)
    headcount_details = headcount_result["detalhamento"]
    headcount_table = headcount_result["tabela"]
    peak = resumir_pico_capacidade_planejada(df_coverage)

    st.success("Dimensionamento concluído.")
    with st.container(horizontal=True):
        st.badge("RESUMO", color="yellow")
        st.subheader("Capacidade planejada")
    headcount_col, peak_col = st.columns(2)
    headcount_col.metric("HC efetivo total", headcount_result["hc_total"], border=True)
    peak_col.metric("Pico simultâneo planejado", peak["pessoas"], border=True)
    st.caption(
        f"Pico em {peak['dia_da_semana']} às {peak['hora']:02d}:00. "
        "O pico é a capacidade ativa em um intervalo e não é somado ao HC."
    )
    st.caption(
        "Cada posto 12x36 corresponde a duas pessoas no HC efetivo para cobrir "
        "a alternância; cada pessoa 5x2 é contada uma vez. Base e relevo são "
        "dimensionados separadamente por turno."
    )

    with st.container(horizontal=True):
        st.badge("COBERTURA", color="yellow")
        st.subheader("Necessidade e capacidade por hora")
    st.caption(
        "Capacidade ativa é exibida por intervalo; HC efetivo está separado acima. "
        "O 12x36 permanece ativo também na janela comercial e o 5x2 complementa a mesma fila."
    )
    days = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
    selected_days = st.multiselect(
        "Dias da semana no gráfico",
        options=days,
        default=days,
        key="dias_grafico",
    )
    metric_col1, metric_col2 = st.columns(2)
    show_need = metric_col1.checkbox(
        "Necessidade estimada",
        value=True,
        key="mostrar_necessidade_estimada",
    )
    show_capacity = metric_col2.checkbox(
        "Capacidade planejada",
        value=True,
        key="mostrar_capacidade_planejada",
    )
    selected_metrics = [
        metric
        for metric, enabled in (
            ("Necessidade estimada", show_need),
            ("Capacidade planejada", show_capacity),
        )
        if enabled
    ]
    if selected_days and selected_metrics:
        chart_data = preparar_dados_grafico(
            df_coverage, selected_days, selected_metrics
        )
        dash_styles = {
            "Necessidade estimada": [1, 0],
            "Capacidade planejada": [6, 3],
        }
        chart = (
            alt.Chart(chart_data)
            .mark_line(point=True)
            .encode(
                x=alt.X(
                    "hora:O",
                    sort=list(range(24)),
                    title="Hora de início do intervalo",
                ),
                y=alt.Y(
                    "pessoas:Q",
                    title="Pessoas ativas",
                    scale=alt.Scale(zero=True),
                ),
                color=alt.Color(
                    "dia_da_semana:N",
                    title="Dia",
                    legend=alt.Legend(orient="bottom", columns=4),
                ),
                strokeDash=alt.StrokeDash(
                    "metrica:N",
                    title="Métrica",
                    legend=alt.Legend(orient="bottom", columns=2),
                    scale=alt.Scale(
                        domain=selected_metrics,
                        range=[dash_styles[metric] for metric in selected_metrics],
                    ),
                ),
                detail=["dia_da_semana", "metrica"],
                tooltip=[
                    alt.Tooltip("dia_da_semana:N", title="Dia"),
                    alt.Tooltip("hora:O", title="Hora"),
                    alt.Tooltip("metrica:N", title="Métrica"),
                    alt.Tooltip("pessoas:Q", title="Pessoas ativas"),
                ],
            )
            .properties(height=380)
        )
        st.altair_chart(
            chart,
            width="stretch",
            alt="Necessidade e capacidade ativa por hora, para os dias selecionados",
        )
    elif not selected_metrics:
        st.info("Selecione ao menos uma métrica para exibir o gráfico.")
    else:
        st.info("Selecione ao menos um dia para exibir o gráfico.")

    st.caption(
        f"Pico de {peak['pessoas']} pessoas ativas: "
        f"{peak['dia_da_semana']} às {peak['hora']:02d}:00."
    )

    break_rows = []
    for shift in ("dia", "noite"):
        for layer in ("base", "relevo"):
            for day, slots in staffing["pausas"][shift][layer].items():
                for start, count in slots.items():
                    break_rows.append(
                        {
                            "turno": shift,
                            "camada": layer,
                            "dia_ordem": day,
                            "inicio_pausa": start,
                            "pausa": f"{start:02d}:00–{(start + 1) % 24:02d}:00",
                            "quantidade": count,
                        }
                    )
    for day, groups in sol5["pausas"].items():
        for entry, layers in groups.items():
            for layer, slots in layers.items():
                for start, count in slots.items():
                    break_rows.append(
                        {
                            "turno": "5x2",
                            "camada": f"{layer} — entrada {entry:02d}h",
                            "dia_ordem": day,
                            "inicio_pausa": start,
                            "pausa": f"{start:02d}:00–{start + 1:02d}:00",
                            "quantidade": count,
                        }
                    )
    breaks = pd.DataFrame(
        break_rows,
        columns=[
            "turno",
            "camada",
            "dia_ordem",
            "inicio_pausa",
            "pausa",
            "quantidade",
        ],
    )
    breaks_by_window = preparar_dados_pausas_por_dia(breaks)
    day_names = {
        1: "segunda",
        2: "terça",
        3: "quarta",
        4: "quinta",
        5: "sexta",
        6: "sábado",
        7: "domingo",
    }
    breaks_by_window["dia_da_semana"] = breaks_by_window["dia_ordem"].map(day_names)

    headcount_tab, coverage_tab, breaks_tab = st.tabs(
        ["Headcount", "Cobertura horária", "Pausas"]
    )
    with headcount_tab:
        st.caption(
            "Quantidade dimensionada mostra a base; o relevo entra no HC efetivo. "
            "Postos 12x36 são convertidos em duas pessoas; posições 5x2 contam "
            "uma pessoa cada."
        )
        st.dataframe(
            headcount_table,
            hide_index=True,
            width="stretch",
            alt="HC efetivo por camada, turno e unidade dimensionada",
        )
        st.bar_chart(
            headcount_details,
            x="Unidade",
            y="HC efetivo",
            color="Camada",
            y_label="HC efetivo",
            alt="HC efetivo nas camadas individuais, sem subtotais",
        )

    display_cols = [
        "dia_ordem",
        "dia_da_semana",
        "hora",
        "volume_mensagens",
        "necessidade_estimada",
        "capacidade_12x36",
        "capacidade_5x2",
        "capacidade_planejada",
        "gap",
        "sobrecobertura",
    ]
    total_gap = int(df_coverage["gap"].sum())
    with coverage_tab:
        st.caption(
            "A grade mantém os 168 intervalos semanais. Gap total: "
            f"{total_gap} pessoa-intervalos."
        )
        st.dataframe(
            df_coverage[display_cols],
            hide_index=True,
            width="stretch",
            alt="Necessidade estimada e capacidade planejada por dia e hora",
        )

    with breaks_tab:
        st.caption(
            "Janelas 12x36: diurno 11h–15h e noturno 21h–01h. Almoços 5x2 "
            "escalonados entre 11h e 15h. O intervalo 00h pertence ao turno "
            "noturno iniciado no dia selecionado. O gráfico mostra posições em "
            "pausa, não pessoas operando; o fator 2 do HC 12x36 não multiplica "
            "as posições da escala ativa."
        )
        selected_break_day = st.selectbox(
            "Dia do turno para pausas",
            options=list(day_names.values()),
            index=0,
            key="dia_pausas",
        )
        selected_break_day_order = next(
            day
            for day, name in day_names.items()
            if name == selected_break_day
        )
        selected_breaks = breaks_by_window.loc[
            breaks_by_window["dia_ordem"] == selected_break_day_order
        ]
        if not selected_breaks.empty:
            st.bar_chart(
                selected_breaks,
                x="pausa",
                y="posicoes_em_pausa",
                color="turno",
                y_label="Posições simultaneamente em pausa",
                alt="Posições simultaneamente em pausa por intervalo e turno no dia selecionado",
            )
        else:
            st.info("Nenhuma pausa está programada para o dia selecionado.")
        for shift in ("dia", "noite"):
            entries = []
            for layer in ("base", "relevo"):
                slots = staffing["pausas"][shift][layer][selected_break_day_order]
                formatted = ", ".join(
                    f"{start:02d}:00–{(start + 1) % 24:02d}:00 ({count} pessoas)"
                    for start, count in slots.items()
                ) or "sem pausas programadas"
                entries.append(f"- **{layer.capitalize()}:** {formatted}")
            st.markdown(
                f"**Turno {shift}, {selected_break_day}:**\n" + "\n".join(entries)
            )
        st.caption(
            "A tabela abaixo mantém as atribuições detalhadas de toda a semana."
        )
        st.dataframe(
            breaks,
            hide_index=True,
            width="stretch",
            alt="Pausas individuais escalonadas por turno, grupo e dia",
        )

    parameters = pd.DataFrame(
        [
            ("Conjunto de canais", canal),
            ("Espera-alvo (min)", tempo_max_espera),
            ("TMR (min)", tmr),
            ("Simultaneidade", simult),
            ("Efetividade (%)", efetividade_percentual),
            ("Aplicar férias", aplicar_ferias),
            ("Fator de férias", ferias_fator),
            ("Turnos 12x36", "Diurno 06:00–18:00; noturno 18:00–06:00"),
            ("Janela 5x2", "Segunda a sexta, 08:00–18:00"),
            ("Janela de almoço 5x2", "11:00–15:00; pausas individuais escalonadas"),
            ("Janela de refeição 12x36", "Diurno 11:00–15:00; noturno 21:00–01:00"),
            ("Grupo 5x2 08:00", "Turno 08:00–17:00"),
            ("Grupo 5x2 09:00", "Turno 09:00–18:00"),
        ],
        columns=["parâmetro", "valor"],
    )
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        parameters.to_excel(writer, sheet_name="parametros", index=False)
        df_need.to_excel(writer, sheet_name="necessidade_estimada", index=False)
        headcount_table.to_excel(
            writer, sheet_name="headcount_camadas", index=False
        )
        breaks.to_excel(writer, sheet_name="pausas", index=False)
        breaks_by_window.to_excel(writer, sheet_name="pausas_por_janela", index=False)
        df_coverage.to_excel(writer, sheet_name="cobertura_horaria", index=False)
    filename = (
        f"dimensionamento_{canal.lower().replace(' ', '_')}_"
        f"{datetime.now():%Y_%m_%d}.xlsx"
    )
    st.download_button(
        "Baixar resultado (.xlsx)",
        data=buffer.getvalue(),
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
