# COSMOS-AI

Agent IA autonome qui gère la page Facebook **Cosmos en Poche** (vulgarisation spatiale, Reels de 30 à 90 s).

## Principes

- **Un seul agent**, composé de modules (étapes) exécutés dans l'ordre. Ce ne sont pas des agents indépendants.
- **Fournisseurs d'IA interchangeables** : l'agent ne connaît que des interfaces (`ports.py`). Changer de fournisseur se fait dans `config.yaml`, sans toucher au code.
- **Gratuit d'abord** : Gemini (principal) puis Groq (secondaire), avec bascule automatique en cas de quota atteint, d'indisponibilité ou de délai dépassé. OpenAI est optionnel.
- **Validation puis autonomie** : en mode `manual`, chaque Reel attend ton accord. En mode `auto`, il est publié seul. On passe à `auto` quand la fiabilité est démontrée.
- **Mémoire éditoriale** : un sujet déjà traité n'est pas republié.

## État du projet

- Phase 1 terminée : fondations (structure, configuration, routeur, mémoire éditoriale, agent et validation, tests, CI).
- Phase 2 terminée : intégrations Gemini et Groq (API officielles), tests avec réponses simulées.
- Phase 3 terminée : veille (flux RSS), sujets populaires, choix du sujet, scénario aux consignes éditoriales de la page, vérification, brouillon avec sources.
- Pas encore branché : visuels, voix, montage, Facebook. Voir `docs/feuille-de-route.md`.

## Utilisation

```bash
pip install -e ".[dev]"
pytest                                  # lance les tests (sans clé, sans réseau)
python -m cosmos_ai check               # vérifie config.yaml et la présence des clés
python -m cosmos_ai ping                # vraie requête de test aux fournisseurs d'IA
python -m cosmos_ai demo                # cycle de démonstration, sans réseau
python -m cosmos_ai news                # lit les flux et classe les sujets populaires
python -m cosmos_ai run                 # produit un brouillon de Reel (rien n'est publié)
python -m cosmos_ai drafts              # liste les brouillons (puis : show 1, reject 1)
```

## Secrets

Les clés d'API ne vont jamais dans le dépôt. `config.yaml` ne contient que le nom de la variable d'environnement (`GEMINI_API_KEY`, `GROQ_API_KEY`).

## Documentation

- `docs/installation.md` : installation, configuration, dépannage
- `docs/fournisseurs-ia.md` : obtenir les clés, quotas, comportement du basculement
- `docs/contenu.md` : veille, tendances, scénario, vérification (et leurs limites)
- `docs/architecture.md` : architecture complète
- `docs/feuille-de-route.md` : phases de développement
- `docs/ajouter-un-fournisseur.md` : brancher un nouveau fournisseur d'IA
