#!/usr/bin/env python3
"""
Fichier quotidien des événements sportifs de Popcorn+ (events.json), produit par GitHub Actions.

Un seul appel par jour aux calendriers sportifs pour tous les utilisateurs : les TV ne téléchargent que ce petit
fichier public, puis rapprochent elles-mêmes chaque match des chaînes de LEUR playlist grâce à la table des droits
TV (rights), embarquée ici pour pouvoir la corriger sans publier une nouvelle version de l'app.

Sources (sans ESPN, cf. popcornautonomeproject/03-conformite.md) :
  - football-data.org (clé gratuite, variable FOOTBALL_DATA_TOKEN) : 5 grands championnats et Ligue des champions ;
  - Jolpica (sans clé) : Formule 1 ;
  - TheSportsDB (clé TSDB_KEY, « 123 » par défaut, gratuite et limitée) : Botola Pro, NBA, NFL, UFC, MotoGP ;
  - tennis.json (tenu à la main d'après les calendriers officiels ATP/WTA) : un événement par jour de grand tournoi.
Aucun score n'est lu ni publié.

Usage : python3 build_events.py rights.json events.json
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

DAYS = 10
NOW = datetime.now(timezone.utc)
UNTIL = NOW + timedelta(days=DAYS)

# football-data.org : code de compétition → (clé des droits TV, nom affiché)
FOOTBALL = {
    "PL": ("epl", "Premier League"),
    "PD": ("laliga", "LaLiga"),
    "FL1": ("ligue1", "Ligue 1"),
    "SA": ("seriea", "Serie A"),
    "BL1": ("bundesliga", "Bundesliga"),
    "CL": ("ucl", "Ligue des champions"),
}

# TheSportsDB : identifiant de ligue → (clé des droits TV, sport, libellé du sport, compétition, durée en heures)
TSDB = {
    4520: ("botola", "football", "Football", "Botola Pro", 2),
    4668: ("roshn", "football", "Football", "Saudi Pro League", 2),
    4829: ("egypt", "football", "Football", "Championnat d'Égypte", 2),
    4720: ("caf", "football", "Football", "Ligue des champions de la CAF", 2),
    4387: ("nba", "basketball", "Basketball", "NBA", 2.5),
    4391: ("nfl", "american", "Football américain", "NFL", 3.5),
    4443: ("ufc", "mma", "UFC & MMA", "UFC", 5),
    4407: ("motogp", "motor", "Sports mécaniques", "MotoGP", 2),
}

STAGES = {
    "GROUP_STAGE": "Phase de groupes", "LEAGUE_STAGE": "Phase de ligue", "PLAYOFFS": "Barrages",
    "LAST_16": "Huitièmes de finale", "QUARTER_FINALS": "Quarts de finale", "SEMI_FINALS": "Demi-finales",
    "FINAL": "Finale",
}


# Problèmes rencontrés : le fichier est quand même publié avec ce qui a marché, puis la tâche GitHub échoue pour
# qu'un e-mail prévienne (voir problems.txt dans .github/workflows/feed.yml).
PROBLEMS = []
MIN_EVENTS = 10  # en dessous, quelque chose est cassé : le fichier n'est pas publié, la TV garde le précédent


def problem(message):
    print(message, file=sys.stderr)
    PROBLEMS.append(message)


def get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "popcorn-plus-feed", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def ms(dt):
    return int(dt.timestamp() * 1000)


def football():
    token = os.environ.get("FOOTBALL_DATA_TOKEN")
    if not token:
        problem("football-data.org : pas de clé (secret FOOTBALL_DATA_TOKEN), football européen absent")
        return []
    q = urllib.parse.urlencode({
        "competitions": ",".join(FOOTBALL), "dateFrom": NOW.date().isoformat(), "dateTo": UNTIL.date().isoformat(),
    })
    data = get(f"https://api.football-data.org/v4/matches?{q}", {"X-Auth-Token": token})
    out = []
    for m in data.get("matches", []):
        if m.get("status") in ("FINISHED", "POSTPONED", "CANCELLED", "AWARDED"):
            continue
        code = m["competition"]["code"]
        league, name = FOOTBALL.get(code, (None, m["competition"]["name"]))
        start = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
        stage = STAGES.get(m.get("stage")) or (f"Journée {m['matchday']}" if m.get("matchday") else None)
        home, away = m["homeTeam"], m["awayTeam"]
        out.append({
            "id": f"fd-{m['id']}", "league": league, "sport": "football", "sportLabel": "Football", "competition": name,
            "home": home.get("shortName") or home.get("name"), "away": away.get("shortName") or away.get("name"),
            # Noms complets et abréviations : servent à retrouver le match dans les guides TV et les chaînes « événement ».
            "homeNames": [n for n in (home.get("name"), home.get("tla")) if n], "awayNames": [n for n in (away.get("name"), away.get("tla")) if n],
            "homeBadge": home.get("crest"), "awayBadge": away.get("crest"),
            "start": ms(start), "end": ms(start + timedelta(hours=2)), "stage": stage,
        })
    return out


def formula1():
    data = get("https://api.jolpi.ca/ergast/f1/current.json")
    out = []
    for race in data["MRData"]["RaceTable"]["Races"]:
        sessions = [("Course", race.get("date"), race.get("time"))]
        if race.get("Qualifying"):
            sessions.append(("Qualifications", race["Qualifying"].get("date"), race["Qualifying"].get("time")))
        if race.get("Sprint"):
            sessions.append(("Sprint", race["Sprint"].get("date"), race["Sprint"].get("time")))
        for label, day, time in sessions:
            if not day or not time:
                continue
            start = datetime.fromisoformat(f"{day}T{time.replace('Z', '')}+00:00")
            if not NOW - timedelta(hours=3) <= start <= UNTIL:
                continue
            out.append({
                "id": f"f1-{race['season']}-{race['round']}-{label}", "league": "f1", "sport": "motor",
                "sportLabel": "Sports mécaniques", "competition": "Formule 1", "home": race["raceName"],
                "away": None, "start": ms(start), "end": ms(start + timedelta(hours=2 if label == "Course" else 1)), "stage": label,
            })
    return out


def thesportsdb():
    key = os.environ.get("TSDB_KEY") or "123"
    out = []
    for lid, (league, sport, sport_label, competition, hours) in TSDB.items():
        time.sleep(4)  # clé gratuite : 30 requêtes par minute au plus, et d'autres utilisateurs sur la même adresse GitHub
        try:
            events = get(f"https://www.thesportsdb.com/api/v1/json/{key}/eventsnextleague.php?id={lid}").get("events") or []
        except Exception as e:  # une ligue en panne ne bloque pas le fichier
            problem(f"TheSportsDB {competition} : {e}")
            continue
        for e in events:
            if not e.get("strTimestamp"):
                continue
            start = datetime.fromisoformat(e["strTimestamp"].replace("Z", "")).replace(tzinfo=timezone.utc)
            if start > UNTIL:
                continue
            out.append({
                "id": f"tsdb-{e['idEvent']}", "league": league, "sport": sport, "sportLabel": sport_label,
                "competition": competition, "home": e.get("strHomeTeam") or e.get("strEvent"), "away": e.get("strAwayTeam"),
                "homeBadge": e.get("strHomeTeamBadge"), "awayBadge": e.get("strAwayTeamBadge"),
                "posterWide": e.get("strThumb") if sport == "mma" else None,
                "start": ms(start), "end": ms(start + timedelta(hours=hours)), "stage": e.get("strRound") and f"Journée {e['strRound']}",
            })
    return out


def tennis():
    """Un événement par journée de grand tournoi (heure locale du tournoi), « Jour N », puis demi-finales et finale."""
    from zoneinfo import ZoneInfo
    from datetime import date, time as dtime
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tennis.json")
    with open(path, encoding="utf-8") as f:
        tournaments = json.load(f)["tournaments"]
    last = max(date.fromisoformat(t["end"]) for t in tournaments)
    if last < NOW.date() + timedelta(days=60):
        problem(f"tennis.json : calendrier des tournois jusqu'au {last.isoformat()} seulement, à compléter (saison suivante)")
    out = []
    for t in tournaments:
        first, last = date.fromisoformat(t["start"]), date.fromisoformat(t["end"])
        hh, mm = map(int, t["daily"].split(":"))
        day, n = first, 1
        while day <= last:
            start = datetime.combine(day, dtime(hh, mm), ZoneInfo(t["tz"])).astimezone(timezone.utc)
            end = start + timedelta(hours=t["hours"])
            if end >= NOW and start <= UNTIL:
                stage = "Finale" if day == last else "Demi-finales" if day == last - timedelta(days=1) else f"Jour {n}"
                out.append({
                    "id": f"tennis-{t['start']}-{t['name']}-{day}".replace(" ", "-").lower(), "league": t["rights"],
                    "sport": "tennis", "sportLabel": "Tennis", "competition": t["name"], "home": t["name"], "away": None,
                    "start": ms(start), "end": ms(end), "stage": f"{stage} · {t['category']}",
                    "featured": True if t.get("featured") else None,
                })
            day += timedelta(days=1)
            n += 1
    return out


def main(rights_path, out_path):
    here = os.path.dirname(os.path.abspath(__file__))
    events = []
    for source in (football, formula1, thesportsdb, tennis):
        try:
            events += source()
        except Exception as e:
            problem(f"{source.__name__} : {e}")
    # Catalogue des grandes chaînes et chaîne exacte de chaque match d'après le guide TV français.
    sys.path.insert(0, here)
    import channels as catalogue
    import tvguide
    with open(os.path.join(here, "channels.json"), encoding="utf-8") as f:
        channel_list = [{k: v for k, v in c.items() if k != "rx"} for c in json.load(f)["channels"]]
    with open(os.path.join(here, "tv_rights.json"), encoding="utf-8") as f:
        tv_rights = {k: v for k, v in json.load(f).items() if k != "about"}
    import beinguide
    try:
        confirmed, added = beinguide.add_bein(events)
        print(f"guide beIN : {confirmed} matchs confirmés en arabe/anglais, {added} matchs ajoutés (Europa, Conférence, AFC, CAF…)")
    except Exception as e:
        problem(f"guide beIN : {e}")
    try:
        print(f"guide TV : {tvguide.add_tv(events, catalogue.load(os.path.join(here, 'channels.json')))} matchs avec leur chaîne française")
    except Exception as e:
        problem(f"guide TV : {e}")
    events = [{k: v for k, v in e.items() if v is not None and not k.startswith("_")} for e in sorted(events, key=lambda e: e["start"])]
    with open(rights_path, encoding="utf-8") as f:
        rights = json.load(f)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "generated": ms(NOW), "events": events, "rights": rights,
                   "channels": channel_list, "tvRights": tv_rights}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(events)} événements écrits dans {out_path}")

    if os.environ.get("TEST_ALERTE") == "true":
        problem("Test de l'alerte demandé à la main : tout va bien, ce message vérifie seulement que l'e-mail arrive.")
    with open(os.path.join(os.path.dirname(os.path.abspath(out_path)), "problems.txt"), "w", encoding="utf-8") as f:
        f.writelines(p + "\n" for p in PROBLEMS)
    if len(events) < MIN_EVENTS:
        sys.exit(f"Seulement {len(events)} événements : sources en panne, fichier non publié (les TV gardent le précédent).")


if __name__ == "__main__":
    main(*sys.argv[1:3])
