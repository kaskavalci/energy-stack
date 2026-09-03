"""Structural regression tests for the EMHASS trading dashboard."""

from __future__ import annotations

import re
import unittest
from collections.abc import Iterator, Mapping
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = REPO_ROOT / "home-assistant" / "dashboards" / "emhass_trading.yaml"
PV_CARD_TITLES = {
    "PV actual vs forecast — yesterday and today",
    "Today's PV forecast",
}
IF_NO_ROWS = "{%- if ns.count == 0 -%}"
ELSE = "{%- else -%}"
ENDIF = "{%- endif -%}"
ENDFOR = "{%- endfor -%}"
FORECAST_ROWS_LOOP = "{%- for row in rows -%}"
TODAY_ROW_IF = "{%- if (row.date | string)[:10] == today -%}"
FORECAST_TABLE_ROW = (
    '<tr><td>{{ (row.date | string)[11:16] }}</td><td align="right">'
    "{{ row.p_pv_forecast | float(0) | round(0) }} W</td></tr>"
)
EXPECTED_CONTROL_TAGS = [
    "{%- set rows = state_attr('sensor.p_pv_forecast', 'forecasts') or [] -%}",
    "{%- set today = now().strftime('%Y-%m-%d') -%}",
    "{%- set ns = namespace(count=0) -%}",
    FORECAST_ROWS_LOOP,
    TODAY_ROW_IF,
    "{%- set ns.count = ns.count + 1 -%}",
    ENDIF,
    ENDFOR,
    IF_NO_ROWS,
    ELSE,
    FORECAST_ROWS_LOOP,
    TODAY_ROW_IF,
    ENDIF,
    ENDFOR,
    ENDIF,
]
EXPECTED_EMPTY_HTML = "<p><em>No PV forecast rows for today.</em></p>"
EXPECTED_TABLE_HTML = """
<table width="100%">
<thead><tr><th>Time</th><th align="right">Forecast</th></tr></thead>
<tbody>
<tr><td>{{ (row.date | string)[11:16] }}</td><td align="right">{{ row.p_pv_forecast | float(0) | round(0) }} W</td></tr>
</tbody>
</table>
"""
CONTROL_TAG = re.compile(r"{%.*?%}", re.DOTALL)


def load_dashboard() -> Mapping[str, Any]:
    with DASHBOARD.open(encoding="utf-8") as stream:
        document = YAML(typ="safe").load(stream)
    if not isinstance(document, Mapping):
        raise TypeError("dashboard root must be a mapping")
    return document


def iter_mappings(value: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from iter_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_mappings(child)


def pv_panel_candidates(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    views = document.get("views", [])
    trading_views = [
        view
        for view in views
        if isinstance(view, Mapping) and view.get("path") == "emhass-trading"
    ]
    candidates: list[Mapping[str, Any]] = []
    for view in trading_views:
        for card in view.get("cards", []):
            if not isinstance(card, Mapping):
                continue
            child_titles = {
                child.get("title")
                for child in card.get("cards", [])
                if isinstance(child, Mapping)
            }
            if child_titles & PV_CARD_TITLES:
                candidates.append(card)
    return candidates


def normalized_html(fragment: str) -> str:
    return " ".join(CONTROL_TAG.sub("", fragment).split())


def forecast_html_branches(content: str) -> tuple[str, str]:
    _, conditional = content.split(IF_NO_ROWS, 1)
    empty_branch, populated_branch = conditional.split(ELSE, 1)
    populated_branch, suffix = populated_branch.rsplit(ENDIF, 1)
    if suffix.strip():
        raise ValueError("unexpected template content after the forecast branch")
    return empty_branch, populated_branch


class BalanceCheckingHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.open_tags: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.open_tags.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.open_tags:
            self.errors.append(f"unexpected closing tag </{tag}>")
        elif self.open_tags[-1] != tag:
            self.errors.append(
                f"closing tag </{tag}> does not match <{self.open_tags[-1]}>"
            )
        else:
            self.open_tags.pop()


def html_balance_errors(fragment: str) -> list[str]:
    parser = BalanceCheckingHtmlParser()
    parser.feed(CONTROL_TAG.sub("", fragment))
    parser.close()
    return parser.errors + [f"unclosed tag <{tag}>" for tag in reversed(parser.open_tags)]


class NativePvPanelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dashboard = load_dashboard()

    def pv_panel(self) -> Mapping[str, Any]:
        candidates = pv_panel_candidates(self.dashboard)
        self.assertEqual(
            len(candidates),
            1,
            "expected exactly one PV actual/forecast panel in the trading view",
        )
        return candidates[0]

    def test_panel_is_one_native_stack_with_exactly_two_ordered_cards(self) -> None:
        panel = self.pv_panel()

        self.assertEqual(panel.get("type"), "vertical-stack")
        cards = panel.get("cards")
        self.assertIsInstance(cards, list)
        self.assertEqual(len(cards), 2)
        self.assertEqual(
            [card.get("type") for card in cards],
            ["statistics-graph", "markdown"],
        )

    def test_actual_history_has_the_exact_compact_statistics_contract(self) -> None:
        actual = self.pv_panel()["cards"][0]

        self.assertEqual(actual.get("type"), "statistics-graph")
        self.assertEqual(actual.get("title"), "PV actual vs forecast — yesterday and today")
        self.assertEqual(actual.get("days_to_show"), 2)
        self.assertEqual(actual.get("period"), "hour")
        self.assertEqual(actual.get("chart_type"), "line")
        self.assertEqual(actual.get("stat_types"), ["mean"])
        entities = actual.get("entities")
        self.assertIsInstance(entities, list)
        self.assertEqual(
            [entity.get("entity") for entity in entities],
            ["sensor.emhass_pv_power_w", "sensor.p_pv_forecast"],
        )
        self.assertEqual(
            [entity.get("name") for entity in entities],
            ["Actual PV (hourly mean)", "Forecast PV (hourly mean)"],
        )

    def test_forecast_card_has_exact_subscription_and_today_only_logic(self) -> None:
        forecast = self.pv_panel()["cards"][1]

        self.assertEqual(forecast.get("type"), "markdown")
        self.assertEqual(forecast.get("entity_id"), ["sensor.p_pv_forecast"])
        content = forecast.get("content")
        self.assertIsInstance(content, str)
        self.assertEqual(CONTROL_TAG.findall(content), EXPECTED_CONTROL_TAGS)

    def test_forecast_empty_and_table_branches_are_exact_and_balanced(self) -> None:
        content = self.pv_panel()["cards"][1]["content"]
        empty_branch, populated_branch = forecast_html_branches(content)

        self.assertEqual(normalized_html(empty_branch), EXPECTED_EMPTY_HTML)
        self.assertEqual(
            normalized_html(populated_branch), normalized_html(EXPECTED_TABLE_HTML)
        )
        self.assertEqual(html_balance_errors(empty_branch), [])
        self.assertEqual(html_balance_errors(populated_branch), [])

    def test_forecast_table_row_is_nested_inside_today_filter(self) -> None:
        content = self.pv_panel()["cards"][1]["content"]
        _, populated_branch = forecast_html_branches(content)

        loop_position = populated_branch.index(FORECAST_ROWS_LOOP)
        today_if_position = populated_branch.index(TODAY_ROW_IF, loop_position)
        row_position = populated_branch.index(FORECAST_TABLE_ROW, today_if_position)
        matching_endif_position = populated_branch.index(ENDIF, today_if_position)
        endfor_position = populated_branch.index(ENDFOR, matching_endif_position)

        self.assertLess(loop_position, today_if_position)
        self.assertLess(today_if_position, row_position)
        self.assertLess(row_position, matching_endif_position)
        self.assertLess(matching_endif_position, endfor_position)

    def test_dashboard_allows_only_expected_custom_cards(self) -> None:
        custom_types = [
            node["type"]
            for node in iter_mappings(self.dashboard)
            if isinstance(node.get("type"), str)
            and node["type"].startswith("custom:")
        ]
        self.assertEqual(
            set(custom_types),
            {"custom:config-template-card", "custom:apexcharts-card"},
        )
        self.assertEqual(custom_types.count("custom:config-template-card"), 1)
        self.assertEqual(custom_types.count("custom:apexcharts-card"), 2)
        self.assertEqual(self.dashboard.get("resources", []), [])

    def trading_view(self) -> Mapping[str, Any]:
        views = self.dashboard.get("views", [])
        return next(
            view
            for view in views
            if isinstance(view, Mapping) and view.get("path") == "emhass-trading"
        )

    def card_titles(self) -> list[str]:
        return [
            card.get("title")
            for card in self.trading_view().get("cards", [])
            if isinstance(card, Mapping) and card.get("title")
        ]

    def test_prices_card_includes_net_electricity_profit(self) -> None:
        prices = next(
            card
            for card in self.trading_view().get("cards", [])
            if isinstance(card, Mapping) and card.get("title") == "Prices"
        )
        entities = prices.get("entities")
        self.assertIsInstance(entities, list)
        self.assertEqual(
            [row.get("entity") for row in entities if isinstance(row, Mapping)],
            [
                "sensor.unit_load_cost",
                "sensor.unit_prod_price",
                "sensor.zonneplan_current_tariff_group",
                "sensor.emhass_net_electricity_profit_today",
                "sensor.emhass_planned_profit_today",
                "sensor.emhass_net_electricity_profit_month",
            ],
        )
        named = {
            row["entity"]: row.get("name")
            for row in entities
            if isinstance(row, Mapping) and "name" in row
        }
        self.assertEqual(named["sensor.zonneplan_current_tariff_group"], "Tariff group")
        self.assertEqual(named["sensor.emhass_net_electricity_profit_today"], "Net profit today")
        self.assertEqual(
            named["sensor.emhass_planned_profit_today"],
            "Planned today (covered slots)",
        )
        self.assertEqual(
            named["sensor.emhass_net_electricity_profit_month"], "Net profit this month"
        )

    def test_removed_active_step_and_pv_model_panels(self) -> None:
        titles = self.card_titles()
        self.assertNotIn("Active step (first row only)", titles)
        self.assertNotIn("PV — model vs actual", titles)

    def test_actual_panel_drops_status_duplicates(self) -> None:
        actual = next(
            card
            for card in self.trading_view().get("cards", [])
            if isinstance(card, Mapping) and card.get("title") == "Actual (Sigen / P1)"
        )
        entities = [
            row.get("entity")
            for row in actual.get("entities", [])
            if isinstance(row, Mapping)
        ]
        self.assertEqual(
            entities,
            [
                "sensor.emhass_house_load_sample_w",
                "__P1_POWER_CONSUMPTION_ENTITY__",
                "__P1_POWER_PRODUCTION_ENTITY__",
                "sensor.net_electricity_today",
            ],
        )
        for duplicate in (
            "sensor.__SIGEN_PLANT_SLUG___plant_battery_soc",
            "sensor.__SIGEN_PLANT_SLUG___plant_battery_power",
            "__P1_POWER_NET_ENTITY__",
        ):
            self.assertNotIn(duplicate, entities)

    def test_p1_grid_load_history_graph_uses_sample_sensor(self) -> None:
        graph = next(
            card
            for card in self.trading_view().get("cards", [])
            if isinstance(card, Mapping)
            and card.get("title") == "P1 grid/load vs EMHASS plan step (24 h)"
        )
        entities = [
            row.get("entity") if isinstance(row, Mapping) else row
            for row in graph.get("entities", [])
        ]
        self.assertIn("sensor.emhass_house_load_sample_w", entities)
        self.assertNotIn("sensor.emhass_house_load_power_w", entities)

    def test_plan_vs_battery_inverts_sigen_power_for_emhass_sign(self) -> None:
        """Keep Sigen entity history; invert in ApexCharts to match EMHASS sign."""
        graph = next(
            card
            for card in self.trading_view().get("cards", [])
            if isinstance(card, Mapping)
            and card.get("type") == "custom:apexcharts-card"
            and isinstance(card.get("header"), Mapping)
            and card["header"].get("title") == "Plan vs battery (24 h)"
        )
        self.assertEqual(graph.get("graph_span"), "24h")
        series = graph.get("series")
        self.assertIsInstance(series, list)
        self.assertEqual(len(series), 2)
        plan, actual = series
        self.assertEqual(plan.get("entity"), "sensor.p_batt_forecast")
        self.assertEqual(
            actual.get("entity"),
            "sensor.__SIGEN_PLANT_SLUG___plant_battery_power",
        )
        self.assertTrue(actual.get("invert"))
        self.assertEqual(actual.get("name"), "Actual battery (EMHASS sign)")
        entity_ids = [row.get("entity") for row in series]
        self.assertNotIn("sensor.emhass_battery_power_emhass_sign_w", entity_ids)

    def test_planned_vs_realized_profit_7d_statistics_graph(self) -> None:
        cards = self.trading_view().get("cards", [])
        prices_idx = next(
            i
            for i, card in enumerate(cards)
            if isinstance(card, Mapping) and card.get("title") == "Prices"
        )
        graph = cards[prices_idx + 1]
        self.assertIsInstance(graph, Mapping)
        self.assertEqual(graph.get("type"), "statistics-graph")
        self.assertEqual(
            graph.get("title"), "Planned vs realized profit — last 7 days"
        )
        self.assertEqual(graph.get("days_to_show"), 7)
        self.assertEqual(graph.get("period"), "day")
        self.assertEqual(graph.get("chart_type"), "bar")
        self.assertEqual(graph.get("stat_types"), ["state"])
        entities = graph.get("entities")
        self.assertIsInstance(entities, list)
        self.assertEqual(len(entities), 2)
        self.assertEqual(
            entities[0].get("entity"),
            "sensor.emhass_planned_profit_today",
        )
        self.assertEqual(
            entities[0].get("name"),
            "Planned (covered slots, EUR)",
        )
        self.assertEqual(
            entities[1].get("entity"),
            "sensor.emhass_net_electricity_profit_today",
        )
        self.assertEqual(entities[1].get("name"), "Realized (EUR)")

    def test_import_price_forecast_uses_full_zonneplan_horizon(self) -> None:
        wrapper = next(
            c
            for c in self.trading_view().get("cards", [])
            if isinstance(c, Mapping) and c.get("type") == "custom:config-template-card"
        )
        self.assertEqual(
            wrapper.get("entities"),
            ["sensor.zonneplan_current_electricity_tariff"],
        )
        variables = wrapper.get("variables")
        self.assertIsInstance(variables, Mapping)
        price_span = variables.get("price_span")
        self.assertIsInstance(price_span, str)
        self.assertIn("sensor.zonneplan_current_electricity_tariff", price_span)
        self.assertIn(".attributes.forecast", price_span)
        self.assertIn("rows[rows.length - 1].datetime", price_span)

        card = wrapper.get("card")
        self.assertIsInstance(card, Mapping)
        self.assertEqual(card.get("type"), "custom:apexcharts-card")
        self.assertEqual(card.get("header", {}).get("title"), "Import price forecast")
        self.assertEqual(card.get("graph_span"), "${price_span}")
        series = card.get("series")
        self.assertIsInstance(series, list)
        self.assertEqual(len(series), 1)
        price = series[0]
        self.assertEqual(
            price.get("entity"), "sensor.zonneplan_current_electricity_tariff"
        )
        self.assertEqual(price.get("extend_to"), False)
        self.assertEqual(price.get("float_precision"), 2)
        self.assertEqual(card.get("yaxis")[0].get("decimals"), 2)
        generator = price.get("data_generator")
        self.assertIsInstance(generator, str)
        self.assertIn("entity.attributes.forecast", generator)
        self.assertIn("electricity_price", generator)
        self.assertIn("/ 1e7", generator)
        self.assertNotIn("unit_load_cost_forecasts", generator)
        self.assertNotIn("Import price (published steps)", self.card_titles())


if __name__ == "__main__":
    unittest.main()
