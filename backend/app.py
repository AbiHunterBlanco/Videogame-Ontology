"""Flask REST API over the video game ontology.

Run from the repository root:  python -m backend.app
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from .ontology import (NODE_TYPES, clean_url, display_name, find_entity, first,
                       format_date, instances, load_ontology, names)
from .recommender import recommend

log = logging.getLogger(__name__)
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def _links(items) -> list[dict]:
    return [{"id": i.name, "name": display_name(i)} for i in items]


def game_summary(g) -> dict:
    price = first(g, "price", 0.0)
    return {
        "name": g.name,
        "title": display_name(g),
        "image": clean_url(first(g, "gameImage")),
        "description": first(g, "description", ""),
        "genres": names(g.hasGenre),
        "platforms": names(g.availableOn),
        "gamemodes": names(g.hasGamemode),
        "developers": names(g.developedBy),
        "franchises": names(g.inFranchise),
        "directors": names(g.directedBy),
        "price": float(price),
        "ageRating": int(first(g, "ageRating", 0)),
        "releaseDate": format_date(first(g, "releaseDate")),
    }


def game_detail(onto, g) -> dict:
    price, age = first(g, "price"), first(g, "ageRating")
    release = first(g, "releaseDate")
    return {
        "type": "Game",
        "name": g.name,
        "title": display_name(g),
        "description": first(g, "description", "No description available."),
        "price": f"${price:.2f}" if price else "Free to Play",
        "releaseDate": format_date(release) if release else "TBA",
        "ageRating": int(age) if age is not None else "Not Rated",
        "playerRating": first(g, "playerRating", "Unrated"),
        "platforms": names(g.availableOn),
        "genres": names(g.hasGenre),
        "developers": names(g.developedBy),
        "directors": names(g.directedBy),
        "image": clean_url(first(g, "gameImage")),
        "trailer": clean_url(first(g, "gameTrailer")),
        # ids + names, so the UI can navigate the graph (game -> developer -> games ...)
        "entities": {
            "Genre": _links(g.hasGenre), "Platform": _links(g.availableOn),
            "Developer": _links(g.developedBy), "Franchise": _links(g.inFranchise),
            "Director": _links(g.directedBy), "Distributor": _links(g.distributedBy),
        },
        "recommendations": recommend(onto, [g.name], limit=6),
    }


def create_app(ontology_path: str | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    CORS(app)
    onto = load_ontology(ontology_path)

    @app.errorhandler(Exception)
    def on_error(exc):  # JSON errors instead of HTML tracebacks
        log.exception("Unhandled error")
        return jsonify({"error": "Internal server error"}), 500

    @app.after_request
    def referrer_policy(resp):  # YouTube embeds need the page origin as a referrer
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return resp

    @app.get("/")
    def index():
        return send_from_directory(FRONTEND_DIR, "index.html")

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "games": len(instances(onto, "VideoGame"))})

    @app.get("/games")
    def games():
        """All games; optional filters: q, genre, platform, developer, max_age."""
        q = request.args.get("q", "").lower()
        max_age = request.args.get("max_age", type=int)
        wanted = {k: request.args.get(k) for k in ("genre", "platform", "developer")}
        out = []
        for g in instances(onto, "VideoGame"):
            s = game_summary(g)
            if q and q not in s["title"].lower():
                continue
            if max_age is not None and s["ageRating"] > max_age:
                continue
            if wanted["genre"] and wanted["genre"] not in s["genres"]:
                continue
            if wanted["platform"] and wanted["platform"] not in s["platforms"]:
                continue
            if wanted["developer"] and wanted["developer"] not in s["developers"]:
                continue
            out.append(s)
        return jsonify(sorted(out, key=lambda x: x["title"].lower()))

    @app.get("/search")
    def search():
        raw = request.args.get("q", "").strip().lower()
        if not raw:
            return jsonify([])
        underscored = raw.replace(" ", "_")
        results = []
        for api_type, (cls_name, _) in NODE_TYPES.items():
            for inst in instances(onto, cls_name):
                if raw in display_name(inst).lower() or underscored in inst.name.lower():
                    results.append({"id": inst.name, "name": display_name(inst), "type": api_type})
        return jsonify(results[:15])

    @app.get("/node/<node_type>/<name>")
    def node(node_type, name):
        if node_type not in NODE_TYPES:
            return jsonify({"error": f"Unknown node type '{node_type}'"}), 404
        cls_name, relation = NODE_TYPES[node_type]
        entity = find_entity(onto, cls_name, name)
        if entity is None:
            return jsonify({"error": "Not found"}), 404

        if node_type == "Game":
            return jsonify(game_detail(onto, entity))

        if node_type == "Streamer":
            related = list(entity.streamsGame)
        else:
            related = [g for g in instances(onto, "VideoGame")
                       if entity in getattr(g, relation, [])]
        return jsonify({
            "type": node_type,
            "title": display_name(entity),
            "comment": first(entity, "comment"),
            "related_games": sorted((game_summary(g) for g in related), key=lambda x: x["title"]),
        })

    @app.get("/filter-options")
    def filter_options():
        def of(cls_name):
            return sorted({n for n in names(instances(onto, cls_name)) if n})
        languages = sorted({str(l) for g in instances(onto, "VideoGame")
                            for l in getattr(g, "language", [])})
        return jsonify({
            "genres": of("Genre"), "platforms": of("Platform"),
            "gamemodes": of("Gamemode"), "developers": of("Developer"),
            "languages": languages,
        })

    @app.post("/recommendations/personalised")
    def personalised():
        """Body: {"library": ["Valorant", ...], "max_age": 16 (optional), "limit": 10}"""
        data = request.get_json(silent=True) or {}
        library = data.get("library", [])
        if not isinstance(library, list) or not all(isinstance(t, str) for t in library):
            return jsonify({"error": "'library' must be a list of game titles"}), 400
        limit = max(1, min(int(data.get("limit", 10)), 50))
        return jsonify(recommend(onto, library, limit=limit, max_age=data.get("max_age")))

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    create_app().run(port=int(os.environ.get("PORT", 5000)),
                     debug=os.environ.get("FLASK_DEBUG") == "1")
