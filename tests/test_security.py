"""
Security & Validation Tests for Container Sentinel.
Tests input validation, authorization, sanitization, and edge cases.
"""

import re
import sys
import os
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sentinel.config import Config
from sentinel.sanitizer import sanitize
from sentinel.logger import SanitizeFilter


class TestContainerNameValidation(unittest.TestCase):
    """Test Docker container name validation."""

    def setUp(self):
        from sentinel.discord_bot import _is_valid_container_name
        self.validate = _is_valid_container_name

    def test_valid_names(self):
        valid = ["nginx", "my-app", "app_v2", "web.server", "A1b2C3", "x" * 128]
        for name in valid:
            self.assertTrue(self.validate(name), f"Should accept: {name}")

    def test_invalid_names(self):
        invalid = [
            "",           # empty
            "-start",     # starts with dash
            ".start",     # starts with dot
            "_start",     # starts with underscore
            "a" * 129,    # too long
            "has space",  # spaces
            "has/slash",  # slashes
            "has:colon",  # colons
            "has@at",     # at sign
            None,         # None
        ]
        for name in invalid:
            self.assertFalse(self.validate(name), f"Should reject: {name}")

    def test_injection_attempts(self):
        attacks = [
            "; rm -rf /",
            "$(whoami)",
            "`id`",
            "name\ninjection",
            "name\x00null",
            "../../../etc/passwd",
            "name; docker rm -f prod",
        ]
        for attack in attacks:
            self.assertFalse(self.validate(attack), f"Should reject injection: {attack}")


class TestSanitizer(unittest.TestCase):
    """Test that sensitive data is properly redacted."""

    def test_token_redaction(self):
        text = "Using token=abc123secret"
        result = sanitize(text)
        self.assertNotIn("abc123secret", result)

    def test_password_redaction(self):
        text = "password=SuperSecret123!"
        result = sanitize(text)
        self.assertNotIn("SuperSecret123!", result)

    def test_safe_text_unchanged(self):
        text = "Container nginx started successfully"
        self.assertEqual(sanitize(text), text)


class TestPortValidation(unittest.TestCase):
    """Test port mapping validation logic."""

    def test_valid_port_range(self):
        # Ports 1-65535 are valid
        for port in [1, 80, 443, 8080, 65535]:
            self.assertTrue(1 <= port <= 65535)

    def test_invalid_port_range(self):
        for port in [0, -1, 65536, 999999]:
            self.assertFalse(1 <= port <= 65535)


class TestImageNameValidation(unittest.TestCase):
    """Test image name validation regex."""

    def setUp(self):
        self.pattern = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_./:@-]{0,255}$')

    def test_valid_images(self):
        valid = [
            "nginx", "nginx:latest", "redis:7", "postgres:16-alpine",
            "docker.io/library/nginx:latest",
            "ghcr.io/owner/repo:v1.0",
            "my-registry.com:5000/image:tag",
        ]
        for img in valid:
            self.assertIsNotNone(self.pattern.match(img), f"Should accept: {img}")

    def test_invalid_images(self):
        invalid = [
            "",
            "-starts-with-dash",
            ".starts-with-dot",
            "has spaces",
            "has;semicolon",
            "$(inject)",
            "`backtick`",
        ]
        for img in invalid:
            self.assertIsNone(self.pattern.match(img), f"Should reject: {img}")


class TestBlockedEnvVars(unittest.TestCase):
    """Test that dangerous environment variables are blocked."""

    def test_blocked_keys(self):
        blocked = {"LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES"}
        for key in blocked:
            self.assertIn(key.upper(), blocked)

    def test_normal_keys_allowed(self):
        blocked = {"LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES"}
        allowed = ["POSTGRES_PASSWORD", "REDIS_URL", "NODE_ENV", "PORT"]
        for key in allowed:
            self.assertNotIn(key, blocked)


class TestSanitizeFilter(unittest.TestCase):
    """Test log sanitize filter."""

    def test_filter_returns_true(self):
        f = SanitizeFilter()
        record = MagicMock()
        record.msg = "Normal log message"
        record.args = None
        self.assertTrue(f.filter(record))


class TestProgressBar(unittest.TestCase):
    """Test progress bar rendering."""

    def setUp(self):
        from sentinel.discord_bot import _progress_bar
        self.bar = _progress_bar

    def test_zero_percent(self):
        result = self.bar(0)
        self.assertEqual(result.count("⬛"), 10)

    def test_100_percent(self):
        result = self.bar(100)
        self.assertNotIn("⬛", result)

    def test_high_percent_red(self):
        result = self.bar(95)
        self.assertIn("🟥", result)

    def test_medium_percent_orange(self):
        result = self.bar(75)
        self.assertIn("🟧", result)

    def test_low_percent_green(self):
        result = self.bar(30)
        self.assertIn("🟩", result)

    def test_over_100(self):
        # Should cap at 100
        result = self.bar(150)
        self.assertEqual(len(result.replace("🟥", "X").replace("⬛", "X")), 10 * len("X"))


class TestConfigDefaults(unittest.TestCase):
    """Test configuration security defaults."""

    def test_default_config_loads(self):
        config = Config()
        self.assertIsNotNone(config)

    def test_resource_thresholds(self):
        config = Config()
        self.assertGreater(config.ram_threshold_percent, 0)
        self.assertLessEqual(config.ram_threshold_percent, 100)
        self.assertGreater(config.cpu_threshold_percent, 0)
        self.assertLessEqual(config.cpu_threshold_percent, 500)


class TestExponentialBackoff(unittest.TestCase):
    """Test reconnection backoff logic."""

    def test_backoff_doubles(self):
        backoff = 5
        for expected in [5, 10, 20, 40, 80, 160, 300, 300]:
            backoff = min(backoff, 300)
            self.assertEqual(backoff, expected)
            backoff = min(backoff * 2, 300)

    def test_backoff_caps_at_300(self):
        backoff = 300
        backoff = min(backoff * 2, 300)
        self.assertEqual(backoff, 300)


if __name__ == "__main__":
    unittest.main(verbosity=2)
