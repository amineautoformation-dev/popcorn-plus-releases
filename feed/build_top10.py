"""Top 10 Netflix de la semaine (officiel, publié par Netflix) pour la France et le Maroc.

Netflix publie chaque mardi un fichier de tous ses classements par pays (≈ 30 Mo) : on n'en garde que la dernière
semaine des pays utiles, dans un petit top10.json que les TV téléchargent avec les matchs. Les titres sont en anglais ;
chaque TV les retrouve sur TMDB.

Usage : python3 build_top10.py top10.json [fichier.tsv local, pour tester]
"""
import csv
import io
import json
import sys
import urllib.request
from datetime import datetime, timezone

SOURCE = "https://www.netflix.com/tudum/top10/data/all-weeks-countries.tsv"
COUNTRIES = ("FR", "MA")


def rows(local=None):
    if local:
        stream = open(local, encoding="utf-8", newline="")
    else:
        req = urllib.request.Request(SOURCE, headers={"User-Agent": "PopcornPlus-feed/1.0"})
        stream = io.TextIOWrapper(urllib.request.urlopen(req, timeout=120), encoding="utf-8", newline="")
    with stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            if row["country_iso2"] in COUNTRIES:
                yield row


def build(local=None):
    by_country = {}
    for r in rows(local):
        by_country.setdefault(r["country_iso2"], []).append(r)
    out = {"generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "Netflix Top 10", "countries": {}}
    for code, items in by_country.items():
        week = max(r["week"] for r in items)
        latest = [r for r in items if r["week"] == week]

        def ranked(category):
            entries = [r for r in latest if r["category"] == category]
            entries.sort(key=lambda r: int(r["weekly_rank"]))
            return [
                {
                    "rank": int(r["weekly_rank"]),
                    "title": r["show_title"],
                    **({"season": r["season_title"]} if r["season_title"] not in ("", "N/A") else {}),
                    "weeks": int(r["cumulative_weeks_in_top_10"] or 0),
                }
                for r in entries
            ]

        out["countries"][code] = {"week": week, "movies": ranked("Films"), "series": ranked("TV")}
    return out


if __name__ == "__main__":
    data = build(sys.argv[2] if len(sys.argv) > 2 else None)
    if not data["countries"]:
        sys.exit("Aucun classement trouvé : fichier Netflix vide ou format changé")
    with open(sys.argv[1], "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    for code, c in data["countries"].items():
        print(f"{code} semaine du {c['week']} : {len(c['movies'])} films, {len(c['series'])} séries")
