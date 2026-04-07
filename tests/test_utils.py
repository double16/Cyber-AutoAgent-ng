#!/usr/bin/env python3
"""Tests for utils module functions."""

import os
import tempfile

from modules.handlers.utils import (
    create_output_directory,
    filter_none_values,
    get_output_path,
    sanitize_target_name,
    validate_output_path,
)


class TestGetOutputPath:
    """Test get_output_path function."""

    def test_get_output_path_default(self, outputs_dir):
        """Test get_output_path with default parameters."""
        result = get_output_path("example_com", "OP_20240101_120000")
        expected = str((outputs_dir / "example_com" / "OP_20240101_120000").resolve())
        assert result == expected

    def test_get_output_path_with_subdir(self, outputs_dir):
        """Test get_output_path with subdirectory."""
        result = get_output_path("example_com", "OP_20240101_120000", "logs")
        expected = str((outputs_dir / "example_com" / "OP_20240101_120000" / "logs").resolve())
        assert result == expected

    def test_get_output_path_with_base_dir(self):
        """Test get_output_path with custom base directory."""
        base_dir = "/tmp/outputs"
        result = get_output_path("example_com", "OP_20240101_120000", "logs", base_dir)
        expected = os.path.join(base_dir, "example_com", "OP_20240101_120000", "logs")
        assert result == expected

    def test_get_output_path_no_subdir(self, outputs_dir):
        """Test get_output_path without subdirectory."""
        result = get_output_path("example_com", "OP_20240101_120000", "")
        expected = str((outputs_dir / "example_com" / "OP_20240101_120000").resolve())
        assert result == expected


class TestSanitizeTargetName:
    """Test sanitize_target_name function."""

    def test_sanitize_simple_domain(self):
        """Test sanitizing simple domain."""
        result = sanitize_target_name("example.com")
        assert result == "example.com"

    def test_sanitize_https_url(self):
        """Test sanitizing HTTPS URL."""
        result = sanitize_target_name("https://example.com")
        assert result == "example.com"

    def test_sanitize_http_url(self):
        """Test sanitizing HTTP URL."""
        result = sanitize_target_name("http://example.com")
        assert result == "example.com"

    def test_sanitize_ftp_url(self):
        """Test sanitizing FTP URL."""
        result = sanitize_target_name("ftp://example.com")
        assert result == "example.com"

    def test_sanitize_url_with_port(self):
        """Test sanitizing URL with port."""
        result = sanitize_target_name("https://example.com:8080")
        assert result == "example.com_8080"

    def test_sanitize_localhost_with_port(self):
        """Test sanitizing localhost URL with port."""
        result = sanitize_target_name("http://localhost:64279")
        assert result == "localhost_64279"

    def test_sanitize_url_with_path(self):
        """Test sanitizing URL with path."""
        result = sanitize_target_name("https://example.com/path/to/resource")
        assert result == "example.com"

    def test_sanitize_ip_address(self):
        """Test sanitizing IP address."""
        result = sanitize_target_name("192.168.1.1")
        assert result == "192.168.1.1"

    def test_sanitize_ip_with_port(self):
        """Test sanitizing IP with port."""
        result = sanitize_target_name("192.168.1.1:8080")
        assert result == "192.168.1.1_8080"

    def test_sanitize_special_characters(self):
        """Test sanitizing string with special characters."""
        result = sanitize_target_name("test@example.com:8080/path?query=value")
        assert result == "test_example.com_8080"

    def test_sanitize_consecutive_underscores(self):
        """Test sanitizing string with consecutive special characters."""
        result = sanitize_target_name("test___example@@@com")
        assert result == "test_example_com"

    def test_sanitize_leading_trailing_chars(self):
        """Test sanitizing string with leading/trailing unsafe chars."""
        result = sanitize_target_name("_..example.com.._")
        assert result == "example.com"

    def test_sanitize_empty_string(self):
        """Test sanitizing empty string."""
        result = sanitize_target_name("")
        assert result == "unknown_target"

    def test_sanitize_only_special_chars(self):
        """Test sanitizing string with only special characters."""
        result = sanitize_target_name("@#$%^&*()")
        assert result == "unknown_target"


class TestValidateOutputPath:
    """Test validate_output_path function."""

    def test_validate_path_within_base(self):
        """Test validating path within base directory."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_path = os.path.join(tmp_dir, "subdir", "file.txt")
            assert validate_output_path(test_path, tmp_dir) is True

    def test_validate_path_outside_base(self):
        """Test validating path outside base directory."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_path = "/etc/passwd"
            assert validate_output_path(test_path, tmp_dir) is False

    def test_validate_path_traversal_attack(self):
        """Test validating path traversal attack."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_path = os.path.join(tmp_dir, "..", "..", "etc", "passwd")
            assert validate_output_path(test_path, tmp_dir) is False

    def test_validate_same_path(self):
        """Test validating same path as base."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            assert validate_output_path(tmp_dir, tmp_dir) is True

    def test_validate_invalid_path(self):
        """Test validating invalid path."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Path with null byte should be invalid
            test_path = tmp_dir + "\x00malicious"
            assert validate_output_path(test_path, tmp_dir) is False


class TestCreateOutputDirectory:
    """Test create_output_directory function."""

    def test_create_new_directory(self):
        """Test creating new directory."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_path = os.path.join(tmp_dir, "new_dir")
            assert create_output_directory(test_path) is True
            assert os.path.exists(test_path)
            assert os.path.isdir(test_path)

    def test_create_existing_directory(self):
        """Test creating existing directory."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # tmp_dir already exists
            assert create_output_directory(tmp_dir) is True

    def test_create_nested_directory(self):
        """Test creating nested directory structure."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_path = os.path.join(tmp_dir, "level1", "level2", "level3")
            assert create_output_directory(test_path) is True
            assert os.path.exists(test_path)
            assert os.path.isdir(test_path)

    def test_create_directory_permission_error(self):
        """Test creating directory with permission error."""
        # Try to create directory in root (should fail on most systems)
        test_path = "/root/test_dir_no_permission"
        result = create_output_directory(test_path)
        # Should return False on permission error
        assert result is False


class TestFilterNoneValues:
    """Test filter_none_values function."""

    def test_filter_none_values_mixed(self):
        """Test with mixed None and non-None values."""
        input_dict = {"a": 1, "b": None, "c": "hello", "d": None, "e": []}
        expected = {"a": 1, "c": "hello", "e": []}
        assert filter_none_values(input_dict) == expected

    def test_filter_none_values_all_none(self):
        """Test with all None values."""
        input_dict = {"a": None, "b": None}
        expected = {}
        assert filter_none_values(input_dict) == expected

    def test_filter_none_values_no_none(self):
        """Test with no None values."""
        input_dict = {"a": 1, "b": 2}
        expected = {"a": 1, "b": 2}
        assert filter_none_values(input_dict) == expected

    def test_filter_none_values_empty(self):
        """Test with empty dictionary."""
        input_dict = {}
        expected = {}
        assert filter_none_values(input_dict) == expected
