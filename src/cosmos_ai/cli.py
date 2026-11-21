"""Ligne de commande : `python -m cosmos_ai check|ping|demo`."""

from __future__ import annotations

import argparse

from .config import load_config
from .demo import run_demo
from .errors import AllProvidersFailed, CosmosError, ProviderNotAvailable
from .llm import build_router, provider_status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cosmos_ai")
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="vérifie la configuration et la présence des clés d'API")
    sub.add_parser("ping", help="teste les fournisseurs d'IA avec une vraie requête")
    sub.add_parser("demo", help="lance un cycle de démonstration (sans réseau)")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except CosmosError as exc:
        print(f"Erreur de configuration : {exc}")
        return 2

    if args.command == "check":
        print(f"Configuration valide. Mode : {config.mode}")
        print("Fournisseurs d'IA activés (ordre d'essai) :")
        for rank, provider in enumerate(config.llm.enabled_providers, start=1):
            print(f"  {rank}. {provider.name} : {provider_status(provider)}")
        print(f"Publications par jour : {config.posts_per_day}")
        return 0

    if args.command == "ping":
        try:
            router = build_router(config.llm)
        except ProviderNotAvailable as exc:
            print(f"Aucun fournisseur utilisable : {exc}")
            return 1
        try:
            answer = router.generate("Réponds uniquement par le mot OK.")
        except AllProvidersFailed as exc:
            print(f"Tous les fournisseurs ont échoué : {exc}")
            return 1
        print(f"Réponse de {router.last_used} : {answer.strip()[:100]}")
        return 0

    result = run_demo(config)
    print(f"Résultat du cycle : {result.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
