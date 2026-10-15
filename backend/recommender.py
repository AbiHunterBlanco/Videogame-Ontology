"""Explainable, ontology-driven game recommender.

Every suggestion carries the *reasons* it was made, expressed as ontology
relations. There is no learned model: the weights below are the whole algorithm.

Ontology axioms are applied in code rather than by an external reasoner:
`similarTo` is symmetric and `sequelOf`/`prequelOf` are inverses, so a relation
asserted in one direction is honoured in both.
"""
from __future__ import annotations

from typing import Any

from owlready2 import Ontology

from .ontology import clean_url, display_name, find_game, first, instances, names

# Relation used to match games -> (label for explanations, weight)
FEATURES: dict[str, tuple[str, int]] = {
    "hasGenre": ("genre", 3),
    "developedBy": ("developer", 2),
    "hasGamemode": ("game mode", 1),
    "inFranchise": ("franchise", 3),
}
SIMILAR_WEIGHT = 4   # explicit `similarTo` assertion (symmetric)
SEQUEL_WEIGHT = 5    # `sequelOf` / `prequelOf` (inverse pair)


def _features(game: Any) -> dict[str, set]:
    return {rel: set(getattr(game, rel, [])) for rel in FEATURES}


def link(owned: Any, cand: Any) -> tuple[str, int] | None:
    """Explicit relation between a library game and a candidate, with axioms applied."""
    if cand in getattr(owned, "similarTo", []) or owned in getattr(cand, "similarTo", []):
        return f"Marked similar to {display_name(owned)}", SIMILAR_WEIGHT
    if owned in getattr(cand, "sequelOf", []) or cand in getattr(owned, "prequelOf", []):
        return f"Sequel to {display_name(owned)}", SEQUEL_WEIGHT
    if cand in getattr(owned, "sequelOf", []) or owned in getattr(cand, "prequelOf", []):
        return f"Prequel to {display_name(owned)}", SEQUEL_WEIGHT
    return None


def recommend(onto: Ontology, library: list[str], limit: int = 10,
              max_age: int | None = None) -> list[dict]:
    """Rank games the user doesn't own by overlap with their library."""
    owned = [g for g in (find_game(onto, t) for t in library) if g is not None]
    if not owned:
        return []

    profile: dict[str, set] = {rel: set() for rel in FEATURES}
    for g in owned:
        for rel, vals in _features(g).items():
            profile[rel] |= vals
    owned_set = set(owned)

    results = []
    for cand in instances(onto, "VideoGame"):
        if cand in owned_set:
            continue
        age = first(cand, "ageRating")
        if max_age is not None and age is not None and age > max_age:
            continue

        score, reasons = 0, []
        feats = _features(cand)
        for rel, (label, weight) in FEATURES.items():
            shared = feats[rel] & profile[rel]
            if shared:
                score += weight * len(shared)
                reasons.append(f"Shares {label}: {', '.join(sorted(names(shared)))}")

        links = [(g, link(g, cand)) for g in owned]
        links = [(g, l) for g, l in links if l]
        for _, (text, weight) in links:
            score += weight
            reasons.insert(0, text)

        if score == 0:
            continue
        linked = {g for g, _ in links}
        because = [display_name(g) for g in owned
                   if g in linked or any(feats[r] & _features(g)[r] for r in FEATURES)]
        results.append({
            "name": cand.name, "display_name": display_name(cand), "score": score,
            "reason": reasons[0], "reasons": reasons, "because": because,
            "ageRating": int(age) if age is not None else None,
            "image": clean_url(first(cand, "gameImage")),
        })

    results.sort(key=lambda r: (-r["score"], r["display_name"]))
    return results[:limit]
