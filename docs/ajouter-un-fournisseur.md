# Ajouter un fournisseur d'IA

Les fournisseurs HTTP (Gemini, Groq) partagent un socle, `HttpLLMProvider` (`src/cosmos_ai/llm/base.py`), qui gère les délais d'attente, les nouvelles tentatives, la traduction des erreurs et la protection de la clé.

1. **Écrire l'adaptateur** dans `src/cosmos_ai/llm/` : une classe qui hérite de `HttpLLMProvider` et définit
   - `name`, `default_model`, `default_env` ;
   - `_build_request(prompt, system)` : renvoie `(url, en-têtes, corps JSON)` ;
   - `_extract_text(data)` : extrait le texte de la réponse JSON.

   Exemple : `gemini.py` ou `groq.py` (une vingtaine de lignes chacun).
2. **L'enregistrer** dans `src/cosmos_ai/llm/providers.py` : `register_provider("nom", _factory(MaClasse))`.
3. **L'activer** dans `config.yaml` : ajouter une entrée dans `llm.providers` (la position dans la liste = la priorité).
4. **Tester** avec `FakeTransport` (`tests/fakes.py`) : aucune requête réelle, aucune clé.

Pour un fournisseur qui n'utilise pas HTTP (modèle local, par exemple), il suffit d'une classe avec un attribut `name` et une méthode `generate(prompt, *, system=None) -> str`, qui lève `QuotaExceeded` (quota atteint) ou `LLMError` (autre panne).

Aucun autre fichier ne change : ni l'agent, ni les modules.
