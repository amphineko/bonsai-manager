from __future__ import annotations

from threading import Lock

from config import Config
from lib.models.health import HealthCheckReport
from lib.services import IndexedAggregateService


class McpContext:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._indexed_service: IndexedAggregateService | None = None
        self._service_lock = Lock()
        self._health_report: HealthCheckReport | None = None
        self._health_lock = Lock()

    @property
    def indexed(self) -> IndexedAggregateService:
        with self._service_lock:
            if self._indexed_service is None:
                self._indexed_service = IndexedAggregateService.from_config(self.config)
            return self._indexed_service

    def check_health(self) -> HealthCheckReport:
        with self._service_lock:
            service = self._indexed_service
        if service is None:
            report = IndexedAggregateService.check_health_from_config(self.config)
        else:
            report = service.check_health()
        return self.set_health_report(report)

    def set_health_report(self, report: HealthCheckReport) -> HealthCheckReport:
        with self._health_lock:
            self._health_report = report
        return report

    def check_health_once(self) -> HealthCheckReport:
        with self._health_lock:
            report = self._health_report
        if report is None:
            return self.check_health()
        return report

    def close(self) -> None:
        with self._service_lock:
            service = self._indexed_service
            self._indexed_service = None
        if service is not None:
            service.close()
