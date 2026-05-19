import argparse
import json
import os
from unittest.mock import Mock


def parse_bug_bounty_headers(args):
    bug_bounty_headers = {}
    if not args.bug_bounty_header:
        env_headers = os.getenv("CYBER_BUG_BOUNTY_HEADERS")
        if env_headers:
            parsed_headers = json.loads(env_headers)
            if not isinstance(parsed_headers, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in parsed_headers.items()
            ):
                raise argparse.ArgumentTypeError(
                    "CYBER_BUG_BOUNTY_HEADERS must be a JSON object with string keys and values"
                )
            bug_bounty_headers.update(parsed_headers)

    for header in args.bug_bounty_header:
        if "=" not in header:
            raise argparse.ArgumentTypeError("--bug-bounty-header must use NAME=VALUE")
        name, value = header.split("=", 1)
        name = name.strip()
        if not name:
            raise argparse.ArgumentTypeError("--bug-bounty-header name cannot be empty")
        bug_bounty_headers[name] = value

    if args.bug_bounty_header:
        os.environ["CYBER_BUG_BOUNTY_HEADERS"] = json.dumps(bug_bounty_headers)
    return bug_bounty_headers


def test_bug_bounty_headers_read_from_environment_when_cli_headers_absent(monkeypatch):
    monkeypatch.setenv("CYBER_BUG_BOUNTY_HEADERS", '{"X-HackerOne-Research":"researcher"}')

    headers = parse_bug_bounty_headers(Mock(bug_bounty_header=[]))

    assert headers == {"X-HackerOne-Research": "researcher"}


def test_cli_bug_bounty_headers_override_environment_and_update_env(monkeypatch):
    monkeypatch.setenv("CYBER_BUG_BOUNTY_HEADERS", '{"X-Research":"env"}')

    headers = parse_bug_bounty_headers(Mock(bug_bounty_header=["X-Research=cli"]))

    assert headers == {"X-Research": "cli"}
    assert os.environ["CYBER_BUG_BOUNTY_HEADERS"] == '{"X-Research": "cli"}'
