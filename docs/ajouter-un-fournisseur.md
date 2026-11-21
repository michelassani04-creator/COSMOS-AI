# Ajouter un fournisseur d'IA

1. **Écrire l'adaptateur** : une classe avec un attribut `name` et une méthode `generate(prompt, *, system=None) -> str`.
   Elle doit lever `QuotaExceeded` quand le quota est atteint, et `LLMError` pour toute autre panne du fournisseur.
2. **L'enregistrer** : `register_provider("mon_fournisseur", lambda cfg: MonAdaptateur(cfg))`.
3. **L'activer** dans `config.yaml` : ajouter une entrée dans `llm.providers` (la position dans la liste = la priorité).
4. **Tester** : l'adaptateur se teste sans réseau en simulant les réponses du service.

Aucun autre fichier ne change : ni l'agent, ni les modules.
