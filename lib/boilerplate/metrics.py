"""Wrapper for Prometheus client."""

from __future__ import annotations

import contextlib
import logging
import timeit
from typing import TYPE_CHECKING

import prometheus_client
import prometheus_client.context_managers
import prometheus_client.decorator
import prometheus_client.utils

from lib.boilerplate.logging import LogColor

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from types import TracebackType
    from typing import Any

    Metric = prometheus_client.metrics.MetricWrapperBase


# Re-implementation of prometheus_client.context_managers.Timer
class CancellableTimer:
    """Observe the time spent inside the context but re-raise exceptions."""

    def __init__(self, metric: prometheus_client.Histogram, callback_name: str) -> None:
        self._metric = metric
        self._callback_name = callback_name

        self.cancelled = False
        self.elapsed_time = 0

    def __enter__(self):
        self._start = timeit.default_timer()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not exc_type and not self.cancelled:
            # Time can go backwards
            self.elapsed_time = max(timeit.default_timer() - self._start, 0)
            callback = getattr(self._metric, self._callback_name)
            callback(self.elapsed_time)

    def __call__[Function: Callable[..., Any]](self, f: Function) -> Function:  # noqa: D102
        def wrapped(func: Function, *args, **kwargs) -> Function:
            # Thread safe and re-entrant
            with self.__class__(self._metric, self._callback_name):
                return func(*args, **kwargs)

        return prometheus_client.decorator.decorate(f, wrapped)

    def labels(self, *args, **labels) -> None:
        """Set metric labels inside the measured context (when not available upon entering)."""
        self._metric = self._metric.labels(*args, **labels)

    def cancel(self) -> None:
        """Cancel the elapsed time observation from within the measured context."""
        self.cancelled = True


class Metrics:
    """Prometheus metrics collected in memory.

    Disable the storing of observations if the metrics are never collected/scraped by Prometheus.
    Otherwise the in-memory collector registry would keep growing without any limits over the time.
    """

    # <=.005, .01, .025, .05, .075, .1, .25, .5, .75, 1.0, 2.5, 5.0, 7.5, 10.0, >10.0
    DEFAULT_BUCKETS = prometheus_client.Histogram.DEFAULT_BUCKETS
    CONTENT_TYPE = prometheus_client.CONTENT_TYPE_LATEST
    INF = prometheus_client.utils.INF

    def __init__(self, store_observations: bool = True, colorize_logs: bool = True) -> None:  # noqa: FBT001, FBT002
        self.store_observations = store_observations

        self.logger = logging.getLogger()
        self.color = LogColor(colorize_logs)

        self._registry = prometheus_client.CollectorRegistry(auto_describe=True)

        self._histograms: dict[str, prometheus_client.Histogram] = {}
        self._counters: dict[str, prometheus_client.Counter] = {}
        self._gauges: dict[str, prometheus_client.Gauge] = {}

        if self.store_observations:
            prometheus_client.platform_collector.PlatformCollector(registry=self._registry)
            prometheus_client.process_collector.ProcessCollector(registry=self._registry)
            prometheus_client.gc_collector.GCCollector(registry=self._registry)

    def __repr__(self) -> str:
        histograms = ", ".join(self._histograms)
        counters = ", ".join(self._counters)
        gauges = ", ".join(self._gauges)

        message = f"Histograms: {histograms} Counters: {counters} Gauges: {gauges}"

        if not self.store_observations:
            message = f"{message}, observations are not stored in the registry"

        return message

    def collect(self) -> bytes:
        """Serialize the observed measurements into Prometheus plain-text format."""
        if not self.store_observations:
            self.logger.warning(
                f"{self.color.yellow}Collected/scraped metrics are empty because storing of the observations is disabled{self.color.default}"
            )

        # Empties the registry
        payload: bytes = prometheus_client.exposition.generate_latest(self._registry)

        return payload

    def add_histogram(
        self,
        name: str,
        description: str,
        buckets: Sequence[float],
        labels: list[str] | None = None,
    ) -> None:
        """Add a metric of type Histogram (observe value, measure elapsed time).

        Adjust the buckets according to expected values -- default bucket ranges are OK for HTTP request durations.
        """
        self._histograms[name] = prometheus_client.Histogram(
            name, description, labels or [], buckets=buckets, registry=self._registry
        )

    def add_counter(
        self,
        name: str,
        description: str,
        labels: list[str] | None = None,
    ) -> None:
        """Add a metric of type Counter (increment-only)."""
        self._counters[name] = prometheus_client.Counter(
            name, description, labels or [], registry=self._registry
        )

    def add_gauge(
        self,
        name: str,
        description: str,
        labels: list[str] | None = None,
    ) -> None:
        """Add a metric of type Gauge (increment, decrement, set)."""
        self._gauges[name] = prometheus_client.Gauge(
            name, description, labels or [], registry=self._registry
        )

    def increment(self, counter_or_gauge: str, value: float = 1, **labels: str) -> None:
        """Increment given counter or gauge."""
        if not self.store_observations:
            return

        metric = self._counters.get(counter_or_gauge) or self._gauges[counter_or_gauge]

        if labels:
            metric = metric.labels(**labels)

        metric.inc(value)

    def decrement(self, gauge: str, value: float = 1, **labels: str) -> None:
        """Increment given gauge."""
        if not self.store_observations:
            return

        metric = self._gauges[gauge]

        if labels:
            metric = metric.labels(**labels)

        metric.dec(value)

    def set(self, gauge: str, value: float, **labels: str) -> None:
        """Set the value of given gauge."""
        if not self.store_observations:
            return

        metric = self._gauges[gauge]

        if labels:
            metric = metric.labels(**labels)

        metric.set(value)

    def observe(self, histogram: str, value: float, **labels: str) -> None:
        """Store an observation to given histogram."""
        if not self.store_observations:
            return

        metric = self._histograms[histogram]

        if labels:
            metric = metric.labels(**labels)

        metric.observe(value)

    @contextlib.contextmanager
    def elapsed_time(self, histogram: str, **labels: str) -> Iterator[CancellableTimer]:
        """Observe the time spent inside the provided context manager."""
        metric = self._histograms[histogram]

        if labels:
            metric = metric.labels(**labels)

        timer = CancellableTimer(metric, "observe")

        with timer:
            yield timer

            if not self.store_observations:
                timer.cancel()
