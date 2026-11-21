# COSMOS-AI

Agent IA autonome qui gère la page Facebook **Cosmos en Poche** (vulgarisation spatiale, Reels de 30 à 90 s).

## Principes

- **Un seul agent**, composé de modules (étapes) exécutés dans l'ordre. Ce ne sont pas des agents indépendants.
- **Fournisseurs d'IA interchangeables** : l'agent ne connaît que des interfaces (`ports.py`). Changer de fournisseur se fait dans `config.yaml`, sans toucher au code.
- **Gratuit d'abord** : l'ordre des fournisseurs gratuits est configurable, avec bascule automatique quand un quota est atteint. OpenAI est optionnel.
- **Validation puis autonomie** : en mode `manual`, chaque Reel attend ton accord. En mode `auto`, il est publié seul. On passe à `auto` quand la fiabilité est démontrée.
- **Mémoire éditoriale** : un sujet déjà traité n'est pas republié.

## État du projet

Phase 1 terminée : fondations (structure, configuration, routeur d'IA, mémoire éditoriale, agent et porte de validation, tests, CI).
Les vrais services (IA, voix, médias, Facebook) arrivent dans les phases suivantes : voir `docs/feuille-de-route.md`.

## Utilisation

```bash
pip install -e ".[dev]"
pytest                                  # lance les tests
python -m cosmos_ai check               # vérifie config.yaml
python -m cosmos_ai demo                # cycle de démonstration, sans réseau
```

## Secrets

Les clés d'API ne vont jamais dans le dépôt. `config.yaml` ne contient que le nom de la variable d'environnement (par exemple `GEMINI_API_KEY`).

## Documentation

- `docs/architecture.md` : architecture complète
- `docs/feuille-de-route.md` : phases de développement
- `docs/ajouter-un-fournisseur.md` : brancher un nouveau fournisseur d'IA
