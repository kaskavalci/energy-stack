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
    "Actual PV — yesterday and today",
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
        self.assertEqual(actual.get("days_to_show"), 2)
        self.assertEqual(actual.get("period"), "hour")
        self.assertEqual(actual.get("chart_type"), "line")
        self.assertEqual(actual.get("stat_types"), ["mean"])
        entities = actual.get("entities")
        self.assertIsInstance(entities, list)
        self.assertEqual(
            [entity.get("entity") for entity in entities],
            ["sensor.emhass_pv_power_w"],
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

    def test_dashboard_has_no_custom_card_or_apexcharts_resource(self) -> None:
        custom_types = [
            node["type"]
            for node in iter_mappings(self.dashboard)
            if isinstance(node.get("type"), str)
            and node["type"].startswith("custom:")
        ]
        resources = self.dashboard.get("resources", [])
        apexcharts_resources = [
            resource
            for resource in resources
            if "apexcharts" in (
                resource if isinstance(resource, str) else str(resource.get("url", ""))
            ).lower()
        ]

        self.assertEqual(custom_types, [])
        self.assertEqual(apexcharts_resources, [])


if __name__ == "__main__":
    unittest.main()
