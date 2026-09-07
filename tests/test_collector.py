from types import SimpleNamespace

import agent.collector as collector
from agent.config import get_sampling_interval


def _mock_available_metrics(monkeypatch):
    monkeypatch.setattr(
        collector.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(
            percent=62.5,
            used=625,
            available=375,
            total=1000,
        ),
    )
    monkeypatch.setattr(
        collector.psutil,
        "swap_memory",
        lambda: SimpleNamespace(percent=10.0, used=100, total=1000),
    )
    monkeypatch.setattr(
        collector.psutil,
        "disk_usage",
        lambda _path: SimpleNamespace(
            percent=40.0,
            used=400,
            free=600,
            total=1000,
        ),
    )
    monkeypatch.setattr(
        collector.psutil,
        "disk_io_counters",
        lambda: SimpleNamespace(
            read_bytes=1000,
            write_bytes=2000,
            read_count=10,
            write_count=20,
        ),
    )
    monkeypatch.setattr(
        collector.psutil,
        "net_io_counters",
        lambda: SimpleNamespace(
            bytes_sent=3000,
            bytes_recv=4000,
            packets_sent=30,
            packets_recv=40,
        ),
    )
    monkeypatch.setattr(
        collector.psutil,
        "net_if_stats",
        lambda: {"Ethernet": SimpleNamespace(isup=True)},
    )
    monkeypatch.setattr(collector.psutil, "boot_time", lambda: 1.0)
    monkeypatch.setattr(
        collector.psutil,
        "cpu_freq",
        lambda: SimpleNamespace(current=3200.0),
    )
    monkeypatch.setattr(
        collector.psutil,
        "cpu_percent",
        lambda interval, percpu=False: [10.0, 30.0] if percpu else 20.0,
    )
    monkeypatch.setattr(
        collector.psutil,
        "cpu_count",
        lambda logical=True: 8 if logical else 4,
    )
    monkeypatch.setattr(
        collector,
        "_collect_process_metrics",
        lambda: {
            "process_count": 50,
            "thread_count": 500,
            "process_snapshots": {
                "cpu": [
                    {
                        "pid": 10,
                        "process_name": "worker.exe",
                        "cpu_percent": 12.0,
                        "memory_percent": 2.0,
                    }
                ],
                "memory": [],
            },
        },
    )
    monkeypatch.setattr(
        collector,
        "_collect_disk_partitions",
        lambda: [
            {
                "mountpoint": "C:\\",
                "percent": 40.0,
                "used_bytes": 400,
                "free_bytes": 600,
                "total_bytes": 1000,
            }
        ],
    )
    monkeypatch.setattr(
        collector,
        "_collect_windows_user_state",
        lambda: (15.0, "active", "Code.exe"),
    )
    monkeypatch.setattr(
        collector,
        "_collect_battery",
        lambda: {
            "battery_percent": 80.0,
            "battery_charging": True,
            "ac_power_connected": True,
            "battery_seconds_remaining": 3600.0,
        },
    )
    monkeypatch.setattr(collector, "_collect_cpu_temperature", lambda: 55.0)
    monkeypatch.setattr(
        collector,
        "_collect_gpu_metrics",
        lambda: {
            "gpu_utilization_percent": 25.0,
            "gpu_memory_percent": 35.0,
            "gpu_temperature_celsius": 60.0,
        },
    )


def test_collect_metrics_returns_expanded_fields(monkeypatch):
    _mock_available_metrics(monkeypatch)

    sample = collector.collect_metrics("test-device")

    assert sample["device_id"] == "test-device"
    assert sample["timestamp_utc"].endswith("+00:00")
    assert sample["cpu_percent"] == 20.0
    assert sample["cpu_per_core_percent"] == [10.0, 30.0]
    assert sample["cpu_physical_cores"] == 4
    assert sample["cpu_logical_cores"] == 8
    assert sample["process_count"] == 50
    assert sample["thread_count"] == 500
    assert sample["ram_available_bytes"] == 375
    assert sample["swap_percent"] == 10.0
    assert sample["disk_free_bytes"] == 600
    assert sample["network_interface_available"] is True
    assert sample["battery_percent"] == 80.0
    assert sample["user_state"] == "active"
    assert sample["foreground_process_name"] == "Code.exe"
    assert sample["cpu_temperature_celsius"] == 55.0
    assert sample["process_snapshots"]["cpu"][0]["pid"] == 10


def test_collect_metrics_uses_none_when_hardware_is_unavailable(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise OSError("metric is unavailable")

    for name in (
        "virtual_memory",
        "swap_memory",
        "disk_usage",
        "disk_io_counters",
        "net_io_counters",
        "net_if_stats",
        "boot_time",
        "cpu_freq",
        "cpu_percent",
        "cpu_count",
    ):
        monkeypatch.setattr(collector.psutil, name, unavailable)
    monkeypatch.setattr(collector, "_collect_process_metrics", unavailable)
    monkeypatch.setattr(collector, "_collect_disk_partitions", unavailable)
    monkeypatch.setattr(
        collector,
        "_collect_windows_user_state",
        lambda: (None, None, None),
    )
    monkeypatch.setattr(
        collector,
        "_collect_battery",
        lambda: {
            "battery_percent": None,
            "battery_charging": None,
            "ac_power_connected": None,
            "battery_seconds_remaining": None,
        },
    )
    monkeypatch.setattr(collector, "_collect_cpu_temperature", lambda: None)
    monkeypatch.setattr(
        collector,
        "_collect_gpu_metrics",
        lambda: {
            "gpu_utilization_percent": None,
            "gpu_memory_percent": None,
            "gpu_temperature_celsius": None,
        },
    )

    sample = collector.collect_metrics("test-device")

    assert sample["cpu_percent"] is None
    assert sample["ram_percent"] is None
    assert sample["swap_percent"] is None
    assert sample["disk_percent"] is None
    assert sample["network_upload_bytes_per_second"] is None
    assert sample["battery_percent"] is None
    assert sample["cpu_temperature_celsius"] is None
    assert sample["gpu_utilization_percent"] is None
    assert sample["uptime_seconds"] is None
    assert sample["process_snapshots"] == {"cpu": [], "memory": []}


def test_counter_rates_use_difference_and_elapsed_time():
    calculator = collector.CounterRateCalculator()

    first = calculator.calculate({"read": 1000, "write": 2000}, 10.0)
    second = calculator.calculate({"read": 1400, "write": 2600}, 12.0)
    reset = calculator.calculate({"read": 100, "write": 2700}, 14.0)

    assert first == {"read": None, "write": None}
    assert second == {"read": 200.0, "write": 300.0}
    assert reset["read"] is None
    assert reset["write"] == 50.0


def test_process_cpu_is_normalized_after_legitimate_multicore_measurement():
    assert collector._normalize_process_cpu_percent(250.0, 8) == 31.25
    assert collector._normalize_process_cpu_percent(800.0, 8) == 100.0
    assert collector._normalize_process_cpu_percent(800.000004, 8) == 100.0
    assert collector._normalize_process_cpu_percent(801.0, 8) is None


def test_top_cpu_processes_exclude_pid_zero_and_system_idle_process(monkeypatch):
    class FakeProcess:
        def __init__(
            self,
            pid: int,
            name: str,
            raw_cpu_percent: float,
            memory_percent: float,
        ) -> None:
            self.info = {
                "pid": pid,
                "name": name,
                "memory_percent": memory_percent,
            }
            self.raw_cpu_percent = raw_cpu_percent
            self.cpu_calls = 0

        def cpu_percent(self, interval=None):
            self.cpu_calls += 1
            return 0.0 if self.cpu_calls == 1 else self.raw_cpu_percent

        def num_threads(self):
            return 1

    processes = [
        FakeProcess(0, "System", 800.0, 0.0),
        FakeProcess(99, "System Idle Process", 720.0, 0.0),
        FakeProcess(10, "worker.exe", 240.0, 2.0),
        FakeProcess(11, "helper.exe", 80.0, 1.0),
    ]
    monkeypatch.setattr(
        collector.psutil,
        "process_iter",
        lambda _attrs: processes,
    )
    monkeypatch.setattr(
        collector.psutil,
        "cpu_count",
        lambda logical=True: 8,
    )
    monkeypatch.setattr(
        collector.psutil,
        "pids",
        lambda: [process.info["pid"] for process in processes],
    )
    monkeypatch.setattr(collector.time, "sleep", lambda _seconds: None)

    result = collector._collect_process_metrics()
    top_cpu = result["process_snapshots"]["cpu"]

    assert [process["pid"] for process in top_cpu] == [10, 11]
    assert [process["cpu_percent"] for process in top_cpu] == [30.0, 10.0]


def test_sampling_interval_defaults_to_30_seconds_and_allows_override(monkeypatch):
    monkeypatch.delenv("SMARTOPS_INTERVAL_SECONDS", raising=False)
    assert get_sampling_interval() == 30

    monkeypatch.setenv("SMARTOPS_INTERVAL_SECONDS", "2")
    assert get_sampling_interval() == 2
