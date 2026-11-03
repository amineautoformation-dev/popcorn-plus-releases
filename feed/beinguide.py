#!/usr/bin/env python3
"""
Guide TV officiel de beIN MENA (bein.com, public, sans clé) : beIN Sports 1…9, English, Xtra 1…9, Max 1…6 et
Alkass 1…8, sur 7 jours, avec pour chaque programme la chaîne, l'heure (heure du Qatar) et l'indication « en direct ».

Sert à deux choses :
  1. confirmer la chaîne arabe ou anglaise exacte des matchs déjà connus (event["tv"]["ar"|"en"]) ;
  2. ajouter les matchs en direct des compétitions absentes des calendriers gratuits (Ligue Europa, Ligue Conférence,
     Ligue des champions AFC, compétitions CAF, championnat du Qatar…), un événement par match, toutes chaînes réunies.
Les rediffusions (« live » = 0), magazines et matchs féminins ou de futsal ne créent rien.
"""

import hashlib
import html
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import tvguide

QATAR = timezone(timedelta(hours=3))
URL = ("https://www.bein.com/en/epg-ajax-template/?action=epg_fetch&category=sports&cdate={day}&language=EN"
       "&loadindex=0&mins=00&offset=0&postid=25356&serviceidentity=bein.net")
DAYS = 7

# Compétition du titre beIN → (clé des droits TV, nom affiché). Seules celles-ci créent des matchs.
COMPETITIONS = [
    (r"UEFA EUROPA LEAGUE", "uel", "Ligue Europa"),
    (r"UEFA CONFERENCE LEAGUE", "uecl", "Ligue Europa Conférence"),
    (r"AFC CHAMPIONS LEAGUE ELITE", "afc", "Ligue des champions AFC"),
    (r"AFC CHAMPIONS LEAGUE TWO", "afc", "Ligue des champions AFC Two"),
    (r"CAF .*CHAMPIONS LEAGUE", "caf", "Ligue des champions CAF"),
    (r"CAF .*CONFEDERATION", "caf", "Coupe de la Confédération CAF"),
    (r"AFRICA CUP OF NATIONS|AFCON", "caf", "Coupe d'Afrique des nations"),
    (r"QATAR STARS LEAGUE|QSL|EXPO STARS LEAGUE", "qsl", "Qatar Stars League"),
    (r"ARAB CUP", "arabcup", "Coupe arabe"),
    (r"WORLD CUP QUALIF", "intl", "Qualifications Coupe du monde"),
]
SKIP = re.compile(r"WOMEN|FEMININ|FUTSAL|U ?1[5-9]|U ?2[0-3]|YOUTH|BEACH|ESPORT")


def channel_id(logo):
    """Logo de la chaîne sur bein.com → identifiant du catalogue (channels.json)."""
    f = logo.upper()
    num = lambda: (re.search(r"0?([1-9])", f.split("SPORTS", 1)[-1] if "SPORTS" in f else f) or [None, None])[1]
    if "ALKASS" in f:
        m = re.search(r"ALKASS_(\d)", f)
        return (f"alkass-{m.group(1)}", "ar") if m else None
    if any(k in f for k in ("NEWS", "FTA", "4K", "FRENCH")):
        return None  # infos, chaîne gratuite, 4K événementielle, beIN français MENA : hors catalogue
    n = num()
    if not n:
        return None
    if "ENGLISH" in f:
        return f"bein-en-{n}", "en"
    if "XTRA" in f:
        return f"bein-xtra-{n}", "ar"
    if "MAX" in f:
        return f"bein-max-{n}", "ar"
    return f"bein-{n}", "ar"


def fetch(day):
    req = urllib.request.Request(URL.format(day=day), headers={"User-Agent": "Mozilla/5.0 (popcorn-plus-feed)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def parse(page):
    """→ [(début UTC, identifiant, langue, en_direct, titre)]."""
    logos = {}
    for m in re.finditer(r"id=['\"]?channels_(\d+)", page):
        img = re.search(r"src=['\"]?([^'\" >]+)", page[m.start():m.start() + 800])
        if img:
            logos[m.group(1)] = img.group(1).rsplit("/", 1)[-1]
    out = []
    for live, parent, title, start in re.findall(
            r"<li live='(\d)'[^>]*parent='slider_(\d+)'.*?<p class=title>([^<]*)</p>.*?data-start='([^']*)'", page, re.S):
        ch = channel_id(logos.get(parent, ""))
        if not ch:
            continue
        t = datetime.strptime(start, "%Y-%m-%d %H:%M:%S").replace(tzinfo=QATAR).astimezone(timezone.utc)
        out.append((t, ch[0], ch[1], live == "1", html.unescape(title).strip()))
    return out


def split_title(title):
    """« Slavia Praha vs Arsenal  -  UEFA Champions League MD4 » → (domicile, extérieur, compétition)."""
    m = re.match(r"(.+?)\s+vs\.?\s+(.+?)\s+[-–]\s+(.+)$", title)
    return (m.group(1).strip(), m.group(2).strip(), m.group(3).strip()) if m else None


def ms(dt):
    return int(dt.timestamp() * 1000)


def add_bein(events, days=DAYS, pages=None):
    """Complète events (sur place) ; renvoie (matchs confirmés, matchs ajoutés)."""
    now = datetime.now(timezone.utc)
    programmes = []
    for i in range(days):
        day = (now + timedelta(days=i)).astimezone(QATAR).strftime("%Y-%m-%d")
        try:
            programmes += parse(pages[i] if pages else fetch(day))
        except Exception as e:  # un jour en panne ne bloque pas le fichier
            print(f"beIN {day} : {e}", file=sys.stderr)
        if not pages:
            time.sleep(1)
    live = [p for p in programmes if p[3] and now - timedelta(hours=3) < p[0]]

    # 1. Confirmations pour les matchs déjà connus (même règle que le guide français).
    confirmed = set()
    for start, cid, lang, _, title in live:
        words = tvguide.norm(title)
        best, best_score = None, 0
        for e in events:
            if not e.get("away"):
                continue
            kickoff = datetime.fromtimestamp(e["start"] / 1000, timezone.utc)
            if not (kickoff - tvguide.BEFORE <= start <= kickoff + timedelta(minutes=20)):
                continue
            hn, an = tvguide._team_names(e, "home"), tvguide._team_names(e, "away")
            if tvguide.rival(tvguide.norm(" ".join(filter(None, hn))), words) or tvguide.rival(tvguide.norm(" ".join(filter(None, an))), words):
                continue
            h, a = tvguide.score(tvguide.team_keys(hn), words), tvguide.score(tvguide.team_keys(an), words)
            if h and a and h + a > best_score:
                best, best_score = e, h + a
            elif h and a and h + a == best_score:
                best = None
        if best is not None:
            ids = best.setdefault("tv", {}).setdefault(lang, [])
            if cid not in ids:
                ids.append(cid)
            confirmed.add(id(best))
            continue
        # 2. Match d'une compétition absente des calendriers : nouvel événement.
        parts = split_title(title)
        if not parts or SKIP.search(title.upper()):
            continue
        home, away, comp = parts
        league = next(((k, name) for rx, k, name in COMPETITIONS if re.search(rx, comp.upper())), None)
        if not league:
            continue
        # Coupes d'Europe : jamais de coup d'envoi entre 1 h et 9 h au Qatar ; un « direct » de nuit est un différé.
        if league[0] in ("uel", "uecl") and 1 <= start.astimezone(QATAR).hour < 9:
            continue
        # Même affiche diffusée plusieurs fois en 36 h (beIN English marque parfois « direct » un différé de nuit) :
        # un seul match, à l'heure de la première diffusion.
        teams = f"{' '.join(tvguide.norm(home))}|{' '.join(tvguide.norm(away))}"
        e = next((x for x in events if x.get("_teams") == teams and abs(x["start"] - ms(start)) < 36 * 3600 * 1000), None)
        if e is not None and ms(start) < e["start"]:
            e["start"], e["end"] = ms(start), ms(start + timedelta(hours=2))
        if e is None:
            key = hashlib.sha1(f"{teams}|{start:%Y%m%d}".encode()).hexdigest()[:12]
            e = {"id": f"bein-{key}", "league": league[0], "sport": "football", "sportLabel": "Football",
                 "competition": league[1], "home": home, "away": away,
                 "start": ms(start), "end": ms(start + timedelta(hours=2)), "tv": {}, "_teams": teams}
            md = re.search(r"\bMD ?(\d+)", comp)
            if md:
                e["stage"] = f"Journée {md.group(1)}"
            events.append(e)
        ids = e["tv"].setdefault(lang, [])
        if cid not in ids:
            ids.append(cid)
    added = sum(1 for e in events if str(e.get("id", "")).startswith("bein-"))
    return len(confirmed), added


if __name__ == "__main__":
    import json
    with open(sys.argv[1], encoding="utf-8") as f:
        feed = json.load(f)
    for e in feed["events"]:
        e.pop("tv", None)
    print(add_bein(feed["events"], days=int(sys.argv[2]) if len(sys.argv) > 2 else DAYS))
    for e in sorted(feed["events"], key=lambda e: e["start"]):
        if "tv" in e and ("ar" in e["tv"] or "en" in e["tv"]):
            print(datetime.fromtimestamp(e["start"] / 1000).strftime("%a %d %H:%M"), e["league"], e.get("home"), "-", e.get("away"), "→", e["tv"])
