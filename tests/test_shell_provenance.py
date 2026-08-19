"""Tests for deterministic static shell execution provenance."""

from modules.tools.shell_provenance import shell_execution_provenance


def test_provenance_extracts_wrapped_command_and_operation_outputs(tmp_path):
    provenance = shell_execution_provenance(
        f"cd {tmp_path}\ntimeout 60 katana -u http://target.test/ -o crawl.txt 2> crawl.log",
        str(tmp_path.parent),
    )

    assert provenance.parsed is True
    assert provenance.executables == ("cd", "timeout", "katana")
    assert provenance.output_paths == (str(tmp_path / "crawl.txt"), str(tmp_path / "crawl.log"))


def test_provenance_discovers_chained_wrappers_and_pipeline_commands(tmp_path):
    provenance = shell_execution_provenance(
        "env MODE=test nice -n 5 httpx -u http://target.test/ -o result.txt | grep status",
        str(tmp_path),
    )

    assert provenance.parsed is True
    assert provenance.executables == ("env", "nice", "httpx", "grep")
    assert provenance.output_paths == (str(tmp_path / "result.txt"),)


def test_provenance_rejects_malformed_or_dynamic_output_paths(tmp_path):
    malformed = shell_execution_provenance("katana -u 'unterminated", str(tmp_path))
    dynamic = shell_execution_provenance("katana -u http://target.test/ -o $OUTPUT", str(tmp_path))

    assert malformed == type(malformed)((), (), False)
    assert dynamic.parsed is True
    assert dynamic.output_paths == ()
