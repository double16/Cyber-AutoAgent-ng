"""Opt-in isolated generation pilot; defaults to scripted SDK preflight only."""

import argparse
import json
from pathlib import Path

from modules.techniques.generation_pilot import run_generation
from modules.techniques.generation_pilot_coordinator import identity, preflight, run
from modules.techniques.generation_pilot_offline import run_offline_generation


def local_worker(case, directory, budget, *, model):
    import ollama

    from modules.techniques.diagnostics import DiagnosticSession
    from modules.techniques.roles import TechniqueRoleRunner

    session = DiagnosticSession(quiet=True, verbose=True)
    runner = TechniqueRoleRunner("ollama", model, diagnostics=session)
    return run_generation(case, directory, runner, budget, transport=(ollama.AsyncClient, "chat"),
                          diagnostics=session)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", action="store_true", help="Requires explicit authorization in the frozen protocol")
    args = parser.parse_args(argv)
    protocol = json.loads(args.protocol.read_text())
    cases = json.loads(args.cases.read_text())
    identity(cases, protocol, Path.cwd())
    if args.run and (protocol.get("status") != "authorized"
                     or type(protocol.get("authorized_provider_request_cap")) is not int
                     or not 1 <= protocol["authorized_provider_request_cap"] <= 30):
        parser.error("Live generation requires a new explicit allowance of at most 30 requests")
    if protocol["provider"] != "ollama":
        parser.error("This pilot transport adapter supports only Ollama")
    if args.run:
        from dotenv import load_dotenv

        load_dotenv(Path.cwd() / ".env", override=False)
    root = args.output.resolve()
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(mode=0o700)
    (root / "protocol.json").write_text(json.dumps(protocol, indent=2))
    (root / "cases.json").write_text(json.dumps(cases, indent=2))
    receipt = preflight(cases, protocol, root / "preflight", run_offline_generation)
    if args.run:
        run(cases, protocol, receipt, root / "live",
            lambda case, directory, budget: local_worker(case, directory, budget, model=protocol["model"]))
    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
