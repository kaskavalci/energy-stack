#!/usr/bin/env python3
from __future__ import annotations

import importlib.metadata
import logging

import numpy as np
import pandas as pd
from emhass.forecast import Forecast
from emhass.retrieve_hass import RetrieveHass
from zoneinfo import ZoneInfo


ENTITY_ID = "sensor.emhass_house_load_sample_w"
HORIZON = 32
GAP_START = 10
GAP_END_EXCLUSIVE = 16


def resample(missing_bins: set[int]) -> tuple[pd.DataFrame, np.ndarray]:
    bin_values = np.linspace(100.0, 720.0, HORIZON)
    index = pd.date_range(
        "2026-07-29T00:00:00Z",
        periods=HORIZON * 30,
        freq="1min",
    )
    raw = pd.DataFrame(
        {ENTITY_ID: np.repeat(bin_values, 30)},
        index=index,
    )
    missing_mask = np.zeros(len(raw), dtype=bool)
    for bin_index in missing_bins:
        missing_mask[bin_index * 30 : (bin_index + 1) * 30] = True
    raw = raw.loc[~missing_mask]
    resampled = Forecast.resample_data(
        raw,
        pd.Timedelta("30min"),
        pd.Timedelta("1min"),
    )
    return resampled, bin_values


def prepare(resampled: pd.DataFrame) -> pd.Series:
    retrieve = object.__new__(RetrieveHass)
    retrieve.df_final = resampled
    retrieve.var_list = [ENTITY_ID]
    retrieve.logger = logging.getLogger("emhass-load-gap-contract")
    retrieve.time_zone = ZoneInfo("UTC")
    prepared = retrieve.prepare_data(
        ENTITY_ID,
        load_negative=False,
        set_zero_min=False,
        var_replace_zero=[],
        var_interp=[ENTITY_ID],
    )
    if prepared is not True:
        raise AssertionError("RetrieveHass.prepare_data returned non-success")
    return retrieve.df_final[f"{ENTITY_ID}_positive"]


def native_naive(prepared: pd.Series) -> pd.Series:
    forecast = object.__new__(Forecast)
    forecast.forecast_dates = pd.date_range(
        "2026-07-30T00:00:00Z",
        periods=HORIZON,
        freq="30min",
    )
    forecast.freq = pd.Timedelta("30min")
    forecast.logger = logging.getLogger("emhass-load-gap-contract")
    result = forecast._get_load_forecast_naive(prepared.to_frame())
    if list(result.columns) != ["yhat"]:
        raise AssertionError(f"unexpected forecast columns: {list(result.columns)}")
    return result["yhat"]


def assert_output(output: pd.Series, expected: np.ndarray) -> None:
    if len(output) != HORIZON:
        raise AssertionError(f"forecast length {len(output)} != {HORIZON}")
    if not np.isfinite(output.to_numpy()).all():
        raise AssertionError("forecast contains a non-finite value")
    np.testing.assert_allclose(output.to_numpy(), expected)


def assert_case(missing_bins: set[int]) -> tuple[np.ndarray, np.ndarray]:
    resampled, bin_values = resample(missing_bins)
    expected = (
        pd.Series(resampled[ENTITY_ID].to_numpy())
        .interpolate(method="linear", limit=None)
        .to_numpy()
    )
    prepared = prepare(resampled)
    np.testing.assert_allclose(prepared.to_numpy(), expected)
    output = native_naive(prepared)
    assert_output(output, expected)
    return bin_values, expected


def main() -> int:
    complete_values, complete_expected = assert_case(set())
    np.testing.assert_allclose(complete_expected, complete_values)

    missing_bins = set(range(GAP_START, GAP_END_EXCLUSIVE))
    bin_values, expected = assert_case(missing_bins)
    interpolated = expected[GAP_START:GAP_END_EXCLUSIVE]
    lower = bin_values[GAP_START - 1]
    upper = bin_values[GAP_END_EXCLUSIVE]
    if not ((interpolated > lower) & (interpolated < upper)).all():
        raise AssertionError("interpolated values escaped their nonzero brackets")
    if (interpolated == 0).any():
        raise AssertionError("generic NaN-to-zero fill reached the load gap")

    incompatible = expected.copy()
    incompatible[GAP_START:GAP_END_EXCLUSIVE] = 0.0
    try:
        assert_output(pd.Series(incompatible), expected)
    except AssertionError:
        negative_control = "pass"
    else:
        raise AssertionError(
            "negative control did not reject zero-filled interpolation"
        )

    version = importlib.metadata.version("emhass")
    print(
        "EMHASS_LOAD_GAP_CONTRACT_OK "
        f"version={version} horizon={HORIZON} gap_bins="
        f"{GAP_END_EXCLUSIVE - GAP_START} "
        f"negative_control={negative_control}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
