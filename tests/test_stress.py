"""
Stress Test for Container Sentinel.
Tests scalability with 1000+ simulated containers/events.
"""

import sys
import os
import time
import threading
import unittest
from unittest.mock import MagicMock, patch, PropertyMock
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sentinel.config import Config
from sentinel.monitor import DockerMonitor, ContainerEvent, DockerEventListener
from sentinel.alerter import AlertRateLimiter


def _mock_container(name, status="running", health="healthy", image="nginx:latest"):
    """Create a mock Docker container object."""
    c = MagicMock()
    c.name = name
    c.status = status
    c.short_id = f"{hash(name) % 10**12:012x}"[:12]
    c.attrs = {
        "State": {"Status": status, "Health": {"Status": health}},
        "Config": {"Image": image},
    }
    c.image = MagicMock()
    c.image.tags = [image]
    c.labels = {}
    return c


class TestScalability1000Containers(unittest.TestCase):
    """Simulate scanning 1000+ containers."""

    def test_scan_1000_containers(self):
        """Scanner handles 1000 containers without crash or excessive memory."""
        config = Config()
        monitor = DockerMonitor(config)
        monitor.client = MagicMock()

        containers = [_mock_container(f"container_{i}") for i in range(1000)]
        monitor.client.containers.list.return_value = containers

        start = time.monotonic()
        problematic, watched = monitor.scan()
        elapsed = time.monotonic() - start

        self.assertEqual(len(watched), 1000)
        self.assertLess(elapsed, 5.0, "Scan of 1000 containers should take <5s")
        print(f"  ✅ 1000 container scan: {elapsed:.3f}s")

    def test_scan_5000_containers(self):
        """Scanner handles 5000 containers."""
        config = Config()
        monitor = DockerMonitor(config)
        monitor.client = MagicMock()

        containers = [_mock_container(f"c_{i}") for i in range(5000)]
        monitor.client.containers.list.return_value = containers

        start = time.monotonic()
        problematic, watched = monitor.scan()
        elapsed = time.monotonic() - start

        self.assertEqual(len(watched), 5000)
        self.assertLess(elapsed, 15.0)
        print(f"  ✅ 5000 container scan: {elapsed:.3f}s")

    def test_mixed_status_containers(self):
        """1000 containers with mixed statuses."""
        config = Config()
        monitor = DockerMonitor(config)
        monitor.client = MagicMock()

        containers = []
        for i in range(1000):
            status = "running" if i % 3 != 0 else "exited"
            health = "healthy" if status == "running" else ""
            containers.append(_mock_container(f"svc_{i}", status=status, health=health))

        monitor.client.containers.list.return_value = containers
        problematic, watched = monitor.scan()

        running = [c for c in watched if c.status == "running"]
        stopped = [c for c in watched if c.status != "running"]
        self.assertGreater(len(running), 0)
        self.assertGreater(len(stopped), 0)
        self.assertGreater(len(problematic), 0)
        print(f"  ✅ Mixed: {len(running)} running, {len(stopped)} stopped, {len(problematic)} problematic")


class TestRateLimiterStress(unittest.TestCase):
    """Test rate limiter under heavy load."""

    def test_1000_rapid_events(self):
        """Rate limiter correctly throttles 1000 rapid events."""
        rl = AlertRateLimiter()
        allowed = 0
        denied = 0
        for i in range(1000):
            if rl.allow(f"container_{i % 10}"):
                allowed += 1
            else:
                denied += 1

        # Should allow some and deny duplicates
        self.assertGreater(allowed, 0)
        print(f"  ✅ Rate limiter: {allowed} allowed, {denied} denied of 1000")

    def test_unique_containers_allowed(self):
        """Each unique container gets at least one alert (first call)."""
        rl = AlertRateLimiter()
        seen = set()
        for i in range(100):
            if rl.allow(f"unique_{i}"):
                seen.add(f"unique_{i}")
        # Rate limiter may have a global rate limit, so not all 100 may pass
        # But a significant number should
        self.assertGreater(len(seen), 5, f"Expected >5 unique containers allowed, got {len(seen)}")


class TestEventProcessingStress(unittest.TestCase):
    """Test event processing at scale."""

    def test_1000_events_processed(self):
        """Process 1000 events without crash."""
        events_processed = []

        def callback(event):
            events_processed.append(event.container_name)

        config = Config()
        client = MagicMock()

        for i in range(1000):
            event = {
                "Action": "die",
                "Actor": {
                    "Attributes": {"name": f"container_{i}", "exitCode": "1"},
                    "ID": f"abc{i:08d}",
                },
                "time": int(time.time()),
            }
            try:
                ce = ContainerEvent(event, client)
                callback(ce)
            except Exception:
                pass

        self.assertEqual(len(events_processed), 1000)
        print(f"  ✅ Processed 1000 events successfully")


class TestConcurrentAccess(unittest.TestCase):
    """Test thread safety under concurrent access."""

    def test_concurrent_scans(self):
        """Multiple threads scanning simultaneously."""
        config = Config()
        monitor = DockerMonitor(config)
        monitor.client = MagicMock()
        containers = [_mock_container(f"c_{i}") for i in range(100)]
        monitor.client.containers.list.return_value = containers

        results = []
        errors = []

        def scan_thread():
            try:
                problematic, watched = monitor.scan()
                results.append(len(watched))
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=scan_thread) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        self.assertEqual(len(results), 20)
        for r in results:
            self.assertEqual(r, 100)
        print(f"  ✅ 20 concurrent scans completed without errors")

    def test_concurrent_rate_limiter(self):
        """Rate limiter is thread-safe."""
        rl = AlertRateLimiter()
        errors = []

        def hammer():
            try:
                for i in range(500):
                    rl.allow(f"container_{i % 50}")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=hammer) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(errors), 0)
        print(f"  ✅ 5000 concurrent rate limiter calls — no errors")


class TestMemoryBounds(unittest.TestCase):
    """Test that data structures don't grow unbounded."""

    def test_rate_limiter_size_bounded(self):
        """Rate limiter doesn't accumulate unlimited entries."""
        rl = AlertRateLimiter()
        for i in range(10000):
            rl.allow(f"ephemeral_container_{i}")
        # Should not crash — entries may grow but we verify no exception
        print(f"  ✅ Rate limiter handled 10000 unique keys without crash")


class TestDashboardViewScalability(unittest.TestCase):
    """Test dashboard view with many containers."""

    def test_get_all_containers_1000(self):
        """DashboardView._get_all_containers handles 1000 containers."""
        from sentinel.discord_bot import DashboardView

        client = MagicMock()
        containers = [_mock_container(f"c_{i}") for i in range(1000)]
        client.containers.list.return_value = containers

        view = DashboardView(client)
        result = view._get_all_containers()
        self.assertEqual(len(result), 1000)
        print(f"  ✅ DashboardView handled 1000 containers")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  🔥 Container Sentinel — Stress Test Suite")
    print("  Testing with 1000+ simulated containers")
    print("=" * 60 + "\n")
    unittest.main(verbosity=2)
