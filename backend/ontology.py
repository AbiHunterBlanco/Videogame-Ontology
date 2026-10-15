"""Ontology loading plus small, defensive accessors around Owlready2."""
from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path
from typing import Any, Iterable

from owlready2 import Ontology, declare_datatype, get_ontology

DEFAULT_ONTOLOGY_PATH = Path(__file__).parent / "data" / "video_game.rdf"

# API node type -> (ontology class, relation used to find a node's games).
# Streamer is the reverse direction: Streamer --streamsGame--> VideoGame.
NODE_TYPES: dict[str, tuple[str, str | None]] = {
    "Game": ("VideoGame", None),
    "Franchise": ("Franchise", "inFranchise"),
    "Developer": ("Developer", "developedBy"),
    "Director": ("Director", "directedBy"),
    "Streamer": ("Streamer", None),
}


def _parse_datetime(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


# xsd:dateTimeStamp (used for some release dates) isn't supported by Owlready2
# out of the box; without this those values fail to parse and the dates vanish.
declare_datatype(dt.datetime, "http://www.w3.org/2001/XMLSchema#dateTimeStamp",
                 _parse_datetime, lambda d: d.isoformat())


def load_ontology(path: str | os.PathLike | None = None) -> Ontology:
    """Load the video game ontology from disk (path or $GAME_ONTOLOGY_PATH)."""
    path = Path(path or os.environ.get("GAME_ONTOLOGY_PATH", DEFAULT_ONTOLOGY_PATH))
    # The ontology declares owl:imports for DBpedia. Mark it as loaded so
    # Owlready2 doesn't try to download the full DBpedia ontology.
    get_ontology("http://dbpedia.org/ontology/").loaded = True
    return get_ontology(str(path.resolve())).load()


def first(inst: Any, prop: str, default: Any = None) -> Any:
    """Value of a property, whether Owlready2 returns it scalar or as a list."""
    val = getattr(inst, prop, None)
    if isinstance(val, (list, set, tuple)):
        return val[0] if len(val) else default
    return default if val is None else val


_NAME_PROPS = ("gameTitle", "franchiseName", "developerName", "directorName", "streamerName", "gamerName")
_FIXES = (("Play Station", "PlayStation"), ("Sci Fi", "Sci-Fi"), ("i OS", "iOS"))


def humanise(name: str) -> str:
    """'ArrowheadGameStudios' -> 'Arrowhead Game Studios' (fallback when no label exists)."""
    out = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name.replace("_", " "))
    for a, b in _FIXES:
        out = out.replace(a, b)
    return out


def display_name(inst: Any) -> str:
    for prop in _NAME_PROPS:
        val = first(inst, prop)
        if val:
            return str(val)
    label = first(inst, "label")
    return str(label) if label else humanise(inst.name)


def names(items: Iterable[Any] | Any | None) -> list[str]:
    if items is None:
        return []
    if not isinstance(items, (list, set, tuple)):
        items = [items]
    return [display_name(i) for i in items if i is not None]


def format_date(val: Any) -> str | None:
    if isinstance(val, (dt.date, dt.datetime)):
        return val.isoformat()[:10]
    return str(val) if val else None


def instances(onto: Ontology, class_name: str) -> list[Any]:
    cls = getattr(onto, class_name, None)
    return list(cls.instances()) if cls is not None else []


def clean_url(val: Any) -> str | None:
    """First well-formed URL in a value (guards against two URLs pasted into one literal)."""
    m = re.match(r"https?://.+?(?=https?://|\s|$)", str(val).strip()) if val else None
    return m.group(0) if m else None


def find_entity(onto: Ontology, class_name: str, ident: str) -> Any | None:
    """Exact lookup by IRI fragment within a class.

    Doesn't assume the ontology's own namespace, so individuals that reuse
    external IRIs (e.g. a DBpedia resource) resolve too.
    """
    return next((i for i in instances(onto, class_name) if i.name == ident), None)


def find_game(onto: Ontology, ident: str) -> Any | None:
    """Resolve a game by internal id ('HelldiversII') or title ('Helldivers 2')."""
    wanted = ident.strip().lower()
    for game in instances(onto, "VideoGame"):
        if wanted in (game.name.lower(), display_name(game).lower()):
            return game
    return None
