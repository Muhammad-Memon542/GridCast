"""RQ1 regime analysis (pre-registration §1, §7; M2-T2, M2-T3).

The comparable series is HOEP before go-live and real-time OZP averaged to hourly after
it. They are different quantities (pre-registration §8); results are stated with that
limitation. Tests operate on daily aggregates (mean, log standard deviation) and on hourly
values (KS, tail index, spike counts).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

import numpy as np
import pandas as pd
import ruptures as rpt
import statsmodels.api as sm
from scipy import stats

from gridcast.timeutil.ieso import TS_DTYPE

EST = "Etc/GMT+5"


def comparable_series(hoep: pd.DataFrame, rt_hourly: pd.DataFrame, go_live: date) -> pd.DataFrame:
    """Hourly ``ts_utc, price, regime``: HOEP before ``go_live``, hourly RT OZP from it."""
    old = hoep.loc[hoep["delivery_date"] < go_live, ["ts_utc", "hoep"]].rename(
        columns={"hoep": "price"}
    )
    rt = rt_hourly[(rt_hourly["delivery_date"] >= go_live) & (rt_hourly["n_intervals"] == 12)]
    rt = rt.sort_values("source").drop_duplicates(
        "ts_utc", keep="last"
    )  # 'ieso' sorts after 'gridstatus'
    new = rt[["ts_utc", "lmp_mean"]].rename(columns={"lmp_mean": "price"})
    out = pd.concat([old.assign(regime="old"), new.assign(regime="new")], ignore_index=True)
    out["ts_utc"] = out["ts_utc"].astype(TS_DTYPE)
    return out.dropna(subset=["price"]).sort_values("ts_utc").reset_index(drop=True)


def daily_stats(hourly: pd.DataFrame) -> pd.DataFrame:
    """Per IESO day: mean price, log standard deviation, number of hours."""
    day = hourly["ts_utc"].dt.tz_convert(EST).dt.date
    grouped = hourly.groupby(day)["price"]
    out = pd.DataFrame({"mean": grouped.mean(), "sd": grouped.std(), "hours": grouped.size()})
    out["log_sd"] = np.log(out["sd"].clip(lower=1e-6))
    out.index.name = "day"
    return out


@dataclass(frozen=True)
class BreakTest:
    statistic: float
    p_value: float

    def as_dict(self) -> dict[str, float]:
        return {k: float(v) for k, v in asdict(self).items()}


def chow_test(y: pd.Series, break_at: date) -> BreakTest:
    """Classic Chow F-test for a level shift (constant-only regression) at ``break_at``."""
    values = y.dropna()
    post = np.array([d >= break_at for d in values.index])
    if post.all() or not post.any():
        raise ValueError("break date must fall inside the series")
    x = values.to_numpy(dtype="float64")
    ssr_pooled = float(np.sum((x - x.mean()) ** 2))
    ssr_split = float(
        np.sum((x[~post] - x[~post].mean()) ** 2) + np.sum((x[post] - x[post].mean()) ** 2)
    )
    k, n = 1, x.size
    f = ((ssr_pooled - ssr_split) / k) / (ssr_split / (n - 2 * k))
    return BreakTest(statistic=f, p_value=float(1.0 - stats.f.cdf(f, k, n - 2 * k)))


def shift_test_hac(y: pd.Series, break_at: date, maxlags: int = 7) -> BreakTest:
    """Level-shift t-test with Newey-West (HAC) errors: robust to daily autocorrelation."""
    values = y.dropna()
    post = np.array([float(d >= break_at) for d in values.index])
    model = sm.OLS(values.to_numpy(dtype="float64"), sm.add_constant(post))
    fit = model.fit(cov_type="HAC", cov_kwds={"maxlags": maxlags})
    return BreakTest(statistic=float(fit.tvalues[1]), p_value=float(fit.pvalues[1]))


def detect_breaks(y: pd.Series, min_size: int = 30, penalty_factor: float = 2.0) -> list[date]:
    """Multiple mean breaks (PELT, L2 cost, BIC-type penalty): Bai-Perron-style search."""
    values = y.dropna()
    signal = values.to_numpy(dtype="float64").reshape(-1, 1)
    n = signal.shape[0]
    penalty = penalty_factor * np.log(n) * float(np.var(signal))
    ends = rpt.Pelt(model="l2", min_size=min_size).fit(signal).predict(pen=penalty)
    return [values.index[i] for i in ends[:-1]]


def ks_test(before: pd.Series, after: pd.Series) -> BreakTest:
    result = stats.ks_2samp(before.dropna(), after.dropna())
    return BreakTest(statistic=float(result.statistic), p_value=float(result.pvalue))


@dataclass(frozen=True)
class TailIndex:
    alpha: float
    ci_low: float
    ci_high: float
    k: int

    def as_dict(self) -> dict[str, float]:
        return {k: float(v) for k, v in asdict(self).items()}


def hill(values: np.ndarray, k: int) -> float:
    """Hill estimator of the tail index alpha from the k largest positive values."""
    x = np.sort(np.asarray(values, dtype="float64")[np.asarray(values) > 0])[::-1]
    if k < 2 or k >= x.size:
        raise ValueError("need 2 <= k < number of positive values")
    return float(1.0 / np.mean(np.log(x[:k] / x[k])))


def tail_index(
    values: pd.Series, top_fraction: float = 0.05, reps: int = 1000, seed: int = 0
) -> TailIndex:
    """Hill alpha on the top ``top_fraction`` of positive values, with a bootstrap 95% CI."""
    x = values.dropna().to_numpy(dtype="float64")
    positive = x[x > 0]
    k = max(int(top_fraction * positive.size), 2)
    rng = np.random.default_rng(seed)
    boot = [hill(rng.choice(positive, positive.size, replace=True), k) for _ in range(reps)]
    low, high = np.percentile(boot, [2.5, 97.5])
    return TailIndex(alpha=hill(positive, k), ci_low=float(low), ci_high=float(high), k=k)


def monthly_spike_counts(hourly: pd.DataFrame, threshold: float) -> pd.Series:
    month = hourly["ts_utc"].dt.tz_convert(EST).dt.strftime("%Y-%m")
    return (hourly["price"] > threshold).groupby(month).sum().astype("int64")


def rolling_volatility(daily_mean: pd.Series, window: int = 30) -> pd.Series:
    return daily_mean.rolling(window, min_periods=window).std()
