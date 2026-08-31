#!/usr/bin/env python3
"""Tests for utils module functions."""

import os
import tempfile
from types import SimpleNamespace

from modules.handlers.utils import (
    create_output_directory,
    filter_none_values,
    get_output_path,
    get_tool_description,
    get_tool_name,
    get_tool_spec,
    sanitize_target_name,
    tool_append_description,
    tool_rename,
    update_latest_output_pointer,
    validate_output_path,
)


def test_get_tool_spec_supports_tool_spec_and_tool_spec_constant():
    lower = SimpleNamespace(tool_spec={"name": "lower"})

    class Upper:
        TOOL_SPEC = {"name": "upper"}

    assert get_tool_spec(lower) == {"name": "lower"}
    assert get_tool_spec(Upper()) == {"name": "upper"}
    assert get_tool_spec(SimpleNamespace()) is None


def test_get_tool_name_prefers_tool_spec_name_and_falls_back():
    assert get_tool_name(SimpleNamespace(tool_spec={"name": "spec_name"}, __name__="ignored")) == "spec_name"
    assert get_tool_name(SimpleNamespace(tool_spec={}, tool_name="tool_name")) == "tool_name"

    def fallback_function():
        return None

    assert get_tool_name(fallback_function) == "fallback_function"
    assert get_tool_name(SimpleNamespace()) == "SimpleNamespace"


def test_get_tool_description_prefers_tool_spec_and_falls_back():
    assert get_tool_description(SimpleNamespace(tool_spec={"description": "Spec description."})) == "Spec description."
    assert get_tool_description(SimpleNamespace(tool_spec={}, description="Attribute description.")) == "Attribute description."

    def documented_function():
        """Function description."""

    assert get_tool_description(documented_function).strip() == "Function description."

    class DocumentedTool:
        """Class description."""

    instance = DocumentedTool()
    instance.__doc__ = None
    assert get_tool_description(instance).strip() == "Class description."


def test_tool_rename_updates_tool_name_and_tool_spec():
    tool = SimpleNamespace(tool_name="old", tool_spec={"name": "old", "description": "desc"})

    tool_rename(tool, "new")

    assert tool.tool_name == "new"
    assert tool.tool_spec["name"] == "new"
    assert tool.tool_spec["description"] == "desc"


def test_tool_rename_updates_tool_spec_only_when_tool_name_missing():
    tool = SimpleNamespace(tool_spec={"name": "old"})

    tool_rename(tool, "new")

    assert not hasattr(tool, "tool_name")
    assert tool.tool_spec["name"] == "new"


def test_tool_append_description_appends_to_existing_description():
    tool = SimpleNamespace(tool_spec={"name": "demo", "description": "Base description."})

    tool_append_description(tool, "Extra guidance.")

    assert tool.tool_spec["description"] == "Base description.\n\nExtra guidance."


def test_tool_append_description_handles_missing_description_and_missing_spec():
    tool = SimpleNamespace(tool_spec={"name": "demo"})
    no_spec_tool = SimpleNamespace()

    tool_append_description(tool, "Only guidance.")
    tool_append_description(no_spec_tool, "Ignored guidance.")

    assert tool.tool_spec["description"] == "\n\nOnly guidance."
    assert not hasattr(no_spec_tool, "tool_spec")


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


class TestUpdateLatestOutputPointer:
    """Test latest operation pointer updates."""

    def test_creates_relative_latest_symlink(self, tmp_path):
        result = update_latest_output_pointer("example.com", "OP_20260706_120000", str(tmp_path))
        latest_path = tmp_path / "example.com" / "latest"

        assert result.success is True
        assert result.mode == "symlink"
        assert latest_path.is_symlink()
        assert os.readlink(latest_path) == "OP_20260706_120000"
        assert (tmp_path / "example.com" / "OP_20260706_120000").is_dir()

    def test_replaces_existing_latest_symlink(self, tmp_path):
        target_dir = tmp_path / "example.com"
        old_operation = target_dir / "OP_20260706_110000"
        old_operation.mkdir(parents=True)
        latest_path = target_dir / "latest"
        latest_path.symlink_to("OP_20260706_110000")

        result = update_latest_output_pointer("example.com", "OP_20260706_120000", str(tmp_path))

        assert result.success is True
        assert result.mode == "symlink"
        assert latest_path.is_symlink()
        assert os.readlink(latest_path) == "OP_20260706_120000"

    def test_replaces_existing_latest_fallback_file(self, tmp_path):
        target_dir = tmp_path / "example.com"
        target_dir.mkdir(parents=True)
        latest_path = target_dir / "latest"
        latest_path.write_text("/old/path\n", encoding="utf-8")

        result = update_latest_output_pointer("example.com", "OP_20260706_120000", str(tmp_path))

        assert result.success is True
        assert result.mode == "symlink"
        assert latest_path.is_symlink()
        assert os.readlink(latest_path) == "OP_20260706_120000"

    def test_writes_fallback_file_when_symlink_creation_fails(self, tmp_path, monkeypatch):
        def fail_symlink(_src, _dst):
            raise OSError("symlink unsupported")

        monkeypatch.setattr("modules.handlers.utils.os.symlink", fail_symlink)

        result = update_latest_output_pointer("example.com", "OP_20260706_120000", str(tmp_path))
        latest_path = tmp_path / "example.com" / "latest"
        operation_path = tmp_path / "example.com" / "OP_20260706_120000"

        assert result.success is True
        assert result.mode == "file"
        assert latest_path.is_file()
        assert latest_path.read_text(encoding="utf-8") == f"{operation_path.resolve()}\n"

    def test_preserves_existing_latest_directory(self, tmp_path):
        latest_path = tmp_path / "example.com" / "latest"
        latest_path.mkdir(parents=True)

        result = update_latest_output_pointer("example.com", "OP_20260706_120000", str(tmp_path))

        assert result.success is False
        assert result.mode == "skipped"
        assert latest_path.is_dir()

    def test_reports_failure_when_operation_directory_cannot_be_created(self, tmp_path, monkeypatch):
        def fail_makedirs(_path, exist_ok=False):
            raise OSError("permission denied")

        monkeypatch.setattr("modules.handlers.utils.os.makedirs", fail_makedirs)

        result = update_latest_output_pointer("example.com", "OP_20260706_120000", str(tmp_path))

        assert result.success is False
        assert result.mode == "failed"
        assert "Could not create operation directory" in result.message

    def test_reports_failure_when_symlink_and_fallback_file_fail(self, tmp_path, monkeypatch):
        def fail_symlink(_src, _dst):
            raise OSError("symlink unsupported")

        def fail_open(*_args, **_kwargs):
            raise OSError("write denied")

        monkeypatch.setattr("modules.handlers.utils.os.symlink", fail_symlink)
        monkeypatch.setattr("builtins.open", fail_open)

        result = update_latest_output_pointer("example.com", "OP_20260706_120000", str(tmp_path))

        assert result.success is False
        assert result.mode == "failed"
        assert "Could not update latest pointer" in result.message


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
