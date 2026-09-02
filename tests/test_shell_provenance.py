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


def test_provenance_expands_static_loop_url_collection():
    provenance = shell_execution_provenance(
        'for path in /api /login /admin; do curl -sS "http://target.test${path}"; done'
    )

    assert provenance.urls == ()
    assert provenance.collection_urls == (
        "http://target.test/api",
        "http://target.test/login",
        "http://target.test/admin",
    )


def test_provenance_handles_empty_and_wrapper_only_commands():
    assert shell_execution_provenance("").parsed is False
    provenance = shell_execution_provenance("timeout --foreground 30")
    assert provenance.parsed is True
    assert provenance.executables == ("timeout",)


def test_provenance_extracts_literal_urls_but_rejects_dynamic_values():
    provenance = shell_execution_provenance(
        "curl https://target.test/api, ftp://files.test/archive; curl https://target.test/$PATH"
    )

    assert provenance.urls == ("https://target.test/api", "ftp://files.test/archive")


def test_provenance_rejects_dynamic_loop_collections_and_unrelated_templates():
    dynamic = shell_execution_provenance('for path in $PATHS; do curl "http://target.test/${path}"; done')
    unrelated = shell_execution_provenance('for path in /api; do curl "http://target.test/static"; done')

    assert dynamic.collection_urls == ()
    assert unrelated.collection_urls == ()


def test_provenance_handles_wrapper_options_and_absolute_output_path(tmp_path):
    output = tmp_path / "result.json"
    provenance = shell_execution_provenance(
        f"sudo -u root env MODE=test command curl https://target.test -o {output}"
    )

    assert provenance.executables == ("sudo", "env", "command", "curl")
    assert provenance.output_paths == (str(output),)
