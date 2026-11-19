from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from staffing_model import (
    capacidade_5x2_por_hora,
    preparar_dados_pausas_por_dia,
)


def workbook_bytes(volume=0, missing_hour=False):
    rows = [
        {
            "dia_da_semana": day,
            "hora": hour,
            "volume_mensagens": volume if day == "segunda" and hour == 10 else 0,
        }
        for day in (
            "segunda",
            "terça",
            "quarta",
            "quinta",
            "sexta",
            "sábado",
            "domingo",
        )
        for hour in range(24)
    ]
    if missing_hour:
        rows.pop(0)
    workbook = BytesIO()
    pd.DataFrame(rows).to_excel(workbook, index=False, engine="xlsxwriter")
    return workbook.getvalue()


def synthetic_reference_workbook_bytes():
    days = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
    demand_by_interval = {
        ("segunda", 6): 5,
        ("segunda", 10): 120,
        ("segunda", 18): 5,
    }
    rows = [
        {
            "dia_da_semana": day,
            "hora": hour,
            "volume_mensagens": demand_by_interval.get((day, hour), 0),
        }
        for day in days
        for hour in range(24)
    ]
    workbook = BytesIO()
    pd.DataFrame(rows).to_excel(workbook, index=False, engine="xlsxwriter")
    return workbook.getvalue()


def test_synthetic_reference_workbook_is_self_contained_and_complete():
    demand = pd.read_excel(BytesIO(synthetic_reference_workbook_bytes()))

    assert len(demand) == 168
    assert not demand.duplicated(["dia_da_semana", "hora"]).any()
    assert demand["volume_mensagens"].gt(0).any()

class TestDimensionamentoApp(unittest.TestCase):
    def test_empty_state_guides_user_to_sidebar_upload(self):
        app_path = Path(__file__).resolve().parents[1] / "app_dimensionamento5.py"
        at = AppTest.from_file(str(app_path)).run(timeout=30)

        self.assertFalse(at.exception)
        self.assertEqual(len(at.file_uploader), 1)
        self.assertEqual(len(at.selectbox), 1)
        self.assertTrue(
            any(
                "Carregue a planilha na configuração lateral" in markdown.value
                for markdown in at.markdown
            )
        )

    def _run_with_upload(self, volume=0, missing_hour=False):
        app_path = Path(__file__).resolve().parents[1] / "app_dimensionamento5.py"
        at = AppTest.from_file(str(app_path)).run(timeout=30)
        at.file_uploader[0].upload(
            "demanda.xlsx",
            workbook_bytes(volume=volume, missing_hour=missing_hour),
        ).run(timeout=60)
        return at

    def _run_with_reference_workbook(self):
        app_path = Path(__file__).resolve().parents[1] / "app_dimensionamento5.py"
        at = AppTest.from_file(str(app_path)).run(timeout=30)
        at.file_uploader[0].upload(
            "exemplo_sintetico.xlsx",
            synthetic_reference_workbook_bytes(),
        ).run(timeout=60)
        return at

    def test_reference_workbook_headcount_matches_capacity_summary(self):
        at = self._run_with_reference_workbook()

        self.assertFalse(at.exception)
        self.assertEqual(at.metric[0].label, "HC efetivo total")
        self.assertEqual(at.metric[1].label, "Pico simultâneo planejado")

        headcount = at.dataframe[0].value
        self.assertEqual(
            list(headcount.columns),
            [
                "Camada",
                "Unidade",
                "Quantidade dimensionada",
                "HC efetivo",
            ],
        )
        total_hc = headcount.loc[
            headcount["Camada"] == "HC efetivo total", "HC efetivo"
        ].item()
        self.assertEqual(int(at.metric[0].value), total_hc)
        self.assertEqual(
            headcount.loc[
                headcount["Camada"] == "HC efetivo 12x36", "HC efetivo"
            ].item()
            + headcount.loc[
                headcount["Camada"] == "HC efetivo 5x2", "HC efetivo"
            ].item(),
            total_hc,
        )
        coverage = at.dataframe[1].value
        self.assertEqual(len(coverage), 168)
        peak = coverage.loc[coverage["capacidade_planejada"].idxmax()]
        self.assertEqual(int(at.metric[1].value), int(peak["capacidade_planejada"]))
        self.assertTrue(any(
            str(peak["dia_da_semana"]) in caption.value
            and f"{int(peak['hora']):02d}:00" in caption.value
            for caption in at.caption
        ))

    def test_upload_shows_layers_capacity_and_matching_charts(self):
        at = self._run_with_upload(volume=120)

        self.assertFalse(at.exception)
        self.assertEqual(
            [tab.label for tab in at.tabs],
            ["Headcount", "Cobertura horária", "Pausas"],
        )
        self.assertEqual(
            [metric.label for metric in at.metric],
            ["HC efetivo total", "Pico simultâneo planejado"],
        )
        self.assertEqual(len(at.dataframe), 3)
        self.assertEqual(len(at.get("vega_lite_chart")), 3)
        self.assertEqual(len(at.json), 0)
        self.assertEqual(at.get("download_button")[0].label, "Baixar resultado (.xlsx)")

    def test_coverage_chart_places_legends_below_the_plot(self):
        at = self._run_with_upload(volume=120)

        chart_spec = json.loads(at.get("vega_lite_chart")[0].proto.spec)
        self.assertEqual(chart_spec["encoding"]["color"]["legend"]["orient"], "bottom")
        self.assertEqual(
            chart_spec["encoding"]["strokeDash"]["legend"]["orient"], "bottom"
        )

        headcount = at.dataframe[0].value
        self.assertEqual(
            list(headcount.columns),
            ["Camada", "Unidade", "Quantidade dimensionada", "HC efetivo"],
        )
        self.assertEqual(
            headcount.loc[
                headcount["Camada"] == "HC efetivo total",
                "HC efetivo",
            ].item(),
            int(at.metric[0].value),
        )
        coverage = at.dataframe[1].value
        self.assertTrue(
            {
                "necessidade_estimada",
                "capacidade_12x36",
                "capacidade_5x2",
                "capacidade_planejada",
                "gap",
                "sobrecobertura",
            }.issubset(coverage.columns)
        )

    def test_zero_volume_has_zero_need_base_relief_and_effective_hc(self):
        at = self._run_with_upload()

        self.assertFalse(at.exception)
        coverage = at.dataframe[1].value
        self.assertEqual(coverage["necessidade_estimada"].sum(), 0)
        headcount = at.dataframe[0].value
        self.assertEqual(headcount["Quantidade dimensionada"].sum(), 0)
        self.assertEqual(headcount["HC efetivo"].sum(), 0)
        self.assertEqual(at.metric[0].label, "HC efetivo total")
        self.assertEqual(at.metric[0].value, "0")

    def test_missing_hour_is_filled_with_zero(self):
        at = self._run_with_upload(missing_hour=True)

        self.assertFalse(at.exception)
        coverage = at.dataframe[1].value
        self.assertEqual(len(coverage), 168)
        self.assertFalse(coverage.duplicated(["dia_ordem", "hora"]).any())
        missing = coverage.query("dia_ordem == 1 and hora == 0").iloc[0]
        self.assertEqual(missing["volume_mensagens"], 0)

    def test_chart_supports_multiple_days_and_keeps_full_week_table(self):
        at = self._run_with_upload(volume=120)
        days = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]

        self.assertFalse(at.exception)
        self.assertEqual(at.multiselect[0].label, "Dias da semana no gráfico")
        self.assertEqual(at.multiselect[0].value, days)

        at.multiselect[0].set_value(["segunda", "sábado"]).run(timeout=60)

        self.assertFalse(at.exception)
        self.assertEqual(at.multiselect[0].value, ["segunda", "sábado"])
        self.assertEqual(len(at.dataframe[1].value), 168)
        self.assertEqual(len(at.get("vega_lite_chart")), 3)

    def test_metric_filters_are_independent_and_keep_full_week_table(self):
        at = self._run_with_upload(volume=120)
        self.assertFalse(at.exception)

        metric_controls = {control.label: control for control in at.checkbox}
        need_label = "Necessidade estimada"
        capacity_label = "Capacidade planejada"
        self.assertTrue(metric_controls[need_label].value)
        self.assertTrue(metric_controls[capacity_label].value)
        self.assertEqual(len(at.get("vega_lite_chart")), 3)

        metric_controls[capacity_label].set_value(False).run(timeout=60)
        self.assertFalse(at.exception)
        self.assertEqual(len(at.get("vega_lite_chart")), 3)
        self.assertEqual(len(at.dataframe[1].value), 168)

        metric_controls = {control.label: control for control in at.checkbox}
        metric_controls[need_label].set_value(False).run(timeout=60)
        self.assertFalse(at.exception)
        self.assertEqual(len(at.get("vega_lite_chart")), 2)
        self.assertEqual(len(at.info), 1)
        self.assertEqual(len(at.dataframe[1].value), 168)

    def test_pause_table_shows_individual_5x2_lunch_assignments(self):
        at = self._run_with_upload(volume=120)
        self.assertFalse(at.exception)

        pause_table = at.dataframe[2].value
        self.assertTrue(
            {
                "turno",
                "camada",
                "dia_ordem",
                "inicio_pausa",
                "pausa",
                "quantidade",
            }.issubset(pause_table.columns)
        )
        five_by_two = pause_table[pause_table["turno"] == "5x2"]
        self.assertGreater(len(five_by_two), 0)
        self.assertTrue(
            five_by_two["camada"].str.contains("entrada 0[89]h").all()
        )
        self.assertTrue(set(five_by_two["inicio_pausa"]).issubset(
            {11, 12, 13, 14}
        ))
        self.assertEqual(len(at.dataframe[1].value), 168)
        headcount_labels = at.dataframe[0].value["Camada"].tolist()
        self.assertFalse(any("almoço 11:00" in label for label in headcount_labels))
        self.assertFalse(any("almoço 12:00" in label for label in headcount_labels))
        self.assertTrue(
            any(
                "Janelas 12x36: diurno 11h–15h e noturno 21h–01h" in caption.value
                for caption in at.caption
            )
        )

    def test_pause_chart_shows_simultaneous_positions_for_selected_day(self):
        at = self._run_with_reference_workbook()

        self.assertFalse(at.exception)
        day_selector = next(
            selectbox
            for selectbox in at.selectbox
            if selectbox.label == "Dia do turno para pausas"
        )
        self.assertEqual(day_selector.value, "segunda")

        pause_chart = json.loads(at.get("vega_lite_chart")[2].proto.spec)
        self.assertEqual(
            pause_chart["encoding"]["y"]["title"],
            "Posições simultaneamente em pausa",
        )
        daily_pauses = preparar_dados_pausas_por_dia(at.dataframe[2].value)
        raw_pauses = at.dataframe[2].value
        self.assertEqual(set(daily_pauses["dia_ordem"]), set(range(1, 8)))
        for row in daily_pauses.itertuples(index=False):
            expected = raw_pauses.loc[
                (raw_pauses["dia_ordem"] == row.dia_ordem)
                & (raw_pauses["turno"] == row.turno)
                & (raw_pauses["inicio_pausa"] == row.inicio_pausa)
                & (raw_pauses["pausa"] == row.pausa),
                "quantidade",
            ].sum()
            self.assertEqual(row.posicoes_em_pausa, expected)

        day_selector.set_value("terça").run(timeout=60)
        self.assertFalse(at.exception)
        updated_selector = next(
            selectbox
            for selectbox in at.selectbox
            if selectbox.label == "Dia do turno para pausas"
        )
        self.assertEqual(updated_selector.value, "terça")

    def test_xlsx_pause_summary_preserves_daily_pause_counts(self):
        captured = {}
        excel_writer = pd.ExcelWriter

        @contextmanager
        def capture_writer(buffer, *args, **kwargs):
            with excel_writer(buffer, *args, **kwargs) as writer:
                yield writer
            captured["bytes"] = buffer.getvalue()

        app_path = Path(__file__).resolve().parents[1] / "app_dimensionamento5.py"
        with patch("pandas.ExcelWriter", capture_writer):
            at = AppTest.from_file(str(app_path)).run(timeout=30)
            at.file_uploader[0].upload(
                "exemplo_sintetico.xlsx",
                synthetic_reference_workbook_bytes(),
            ).run(timeout=60)

        self.assertFalse(at.exception)
        exported = pd.ExcelFile(BytesIO(captured["bytes"]))
        self.assertEqual(
            exported.sheet_names,
            [
                "parametros",
                "necessidade_estimada",
                "headcount_camadas",
                "pausas",
                "pausas_por_janela",
                "cobertura_horaria",
            ],
        )
        pause_summary = pd.read_excel(
            BytesIO(captured["bytes"]), sheet_name="pausas_por_janela"
        )
        self.assertIn("dia_ordem", pause_summary.columns)
        expected_summary = preparar_dados_pausas_por_dia(at.dataframe[2].value)
        columns = [
            "dia_ordem",
            "turno",
            "inicio_pausa",
            "pausa",
            "posicoes_em_pausa",
        ]
        actual_summary = pause_summary[columns].sort_values(columns[:3]).reset_index(
            drop=True
        )
        expected_summary = expected_summary[columns].sort_values(columns[:3]).reset_index(
            drop=True
        )
        pd.testing.assert_frame_equal(actual_summary, expected_summary)

    def test_reference_workbook_layers_capacity_and_breaks_reconcile(self):
        at = self._run_with_reference_workbook()

        self.assertFalse(at.exception)
        headcount = at.dataframe[0].value
        coverage = at.dataframe[1].value
        pauses = at.dataframe[2].value
        base_day = int(
            headcount.loc[
                (headcount["Camada"] == "Base")
                & headcount["Unidade"].str.endswith("diurno"),
                "Quantidade dimensionada",
            ].sum()
        )
        relief_day = int(
            headcount.loc[
                (headcount["Camada"] == "Relevo")
                & headcount["Unidade"].str.endswith("diurno"),
                "HC efetivo",
            ].sum()
            / 2
        )
        base_night = int(
            headcount.loc[
                (headcount["Camada"] == "Base")
                & headcount["Unidade"].str.endswith("noturno"),
                "Quantidade dimensionada",
            ].sum()
        )
        relief_night = int(
            headcount.loc[
                (headcount["Camada"] == "Relevo")
                & headcount["Unidade"].str.endswith("noturno"),
                "HC efetivo",
            ].sum()
            / 2
        )
        hc_5x2 = int(
            headcount.loc[
                headcount["Camada"].isin(["Base", "Relevo"])
                & headcount["Unidade"].str.startswith("pessoas 5x2"),
                "HC efetivo",
            ].sum()
        )
        self.assertEqual(
            int(at.metric[0].value),
            (base_day + relief_day + base_night + relief_night) * 2 + hc_5x2,
        )

        for row in coverage.itertuples(index=False):
            day = int(row.dia_ordem)
            hour = int(row.hora)
            if 6 <= hour <= 17:
                shift = "dia"
                shift_day = day
                positions = base_day + relief_day
            elif hour >= 18 or hour <= 5:
                shift = "noite"
                shift_day = day if hour >= 18 else (day - 2) % 7 + 1
                positions = base_night + relief_night
            else:
                shift = ""
                shift_day = day
                positions = 0
            paused = pauses.loc[
                (pauses["turno"] == shift)
                & (pauses["dia_ordem"] == shift_day)
                & (pauses["inicio_pausa"] == hour),
                "quantidade",
            ].sum()
            self.assertEqual(row.capacidade_12x36, max(0, positions - paused))

        for row in coverage.itertuples(index=False):
            day = int(row.dia_ordem)
            hour = int(row.hora)
            expected_5x2 = 0
            for entry, label in ((8, "08:00"), (9, "09:00")):
                for layer in ("Base", "Relevo"):
                    unit = f"pessoas 5x2 — entrada {label}"
                    row_headcount = headcount.loc[
                        (headcount["Camada"] == layer)
                        & (headcount["Unidade"] == unit)
                    ].iloc[0]
                    quantity = int(
                        row_headcount["Quantidade dimensionada"]
                        if layer == "Base"
                        else row_headcount["HC efetivo"]
                    )
                    layer_prefix = layer.lower()
                    group_breaks = {
                        int(pause.inicio_pausa): int(pause.quantidade)
                        for pause in pauses.loc[
                            (pauses["turno"] == "5x2")
                            & (pauses["dia_ordem"] == day)
                            & (
                                pauses["camada"]
                                == f"{layer_prefix} — entrada {entry:02d}h"
                            )
                        ].itertuples(index=False)
                    }
                    expected_5x2 += capacidade_5x2_por_hora(
                        day,
                        hour,
                        quantity,
                        entry,
                        group_breaks,
                    )
            self.assertEqual(row.capacidade_5x2, expected_5x2)
            self.assertEqual(
                row.capacidade_planejada,
                row.capacidade_12x36 + row.capacidade_5x2,
            )
            self.assertEqual(
                row.gap,
                max(row.necessidade_estimada - row.capacidade_planejada, 0),
            )
            self.assertEqual(
                row.sobrecobertura,
                max(row.capacidade_planejada - row.necessidade_estimada, 0),
            )
