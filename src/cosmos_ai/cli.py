"""Ligne de commande : `python -m cosmos_ai check|demo`."""

from __future__ import annotations

import argparse

from .config import load_config
from .demo import run_demo
from .errors import CosmosError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cosmos_ai")
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="vérifie la configuration")
    sub.add_parser("demo", help="lance un cycle de démonstration (sans réseau)")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except CosmosError as exc:
        print(f"Erreur de configuration : {exc}")
        return 2

    if args.command == "check":
        names = ", ".join(p.name for p in config.llm.enabled_providers)
        print(f"Configuration valide. Mode : {config.mode}")
        print(f"Fournisseurs d'IA activés (ordre d'essai) : {names}")
        print(f"Publications par jour : {config.posts_per_day}")
        return 0

    result = run_demo(config)
    print(f"Résultat du cycle : {result.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
