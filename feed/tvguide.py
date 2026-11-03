#!/usr/bin/env python3
"""
Chaîne exacte de chaque match, d'après le guide TV public français (xmltvfr.fr, mis à jour chaque jour) :
Canal+, Canal+ Foot, Canal+ Live 1…19, Ligue 1+ 1…10, DAZN, beIN Sports, RMC Sport, L'Équipe, M6, TF1…

Pour chaque match du flux, on cherche les programmes qui commencent à l'heure du coup d'envoi (de 25 min avant à
15 min après) et qui citent les deux équipes, même écrites autrement (« Atleti » / « Atlético Madrid »,
« Barça » / « FC Barcelone », « PSG » / « Paris-SG »). Les avant-matchs, débriefs et rediffusions sont écartés.
Résultat : event["tv"] = {"fr": ["canalplus-foot", "canalplus-live-1"]} (identifiants de channels.json).
"""

import io
import re
import sys
import unicodedata
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import channels as catalogue

XMLTV_URL = "https://xmltvfr.fr/xmltv/xmltv.zip"
BEFORE, AFTER = timedelta(minutes=25), timedelta(minutes=15)
NOT_LIVE = re.compile(r"\b(PLATEAU|AVANT ?MATCH|DEBRIEF|RESUME|RESUMES|MAGAZINE|CHAMPIONS CLUB|LE MAG|TEMPS ADDITIONNEL|"
                      r"HIGHLIGHTS|REPLAY|REDIFFUSION|BEST OF|ANALYSE|SOIREE|MULTIPLEX DES|ACCES)\b")
YOUTH = re.compile(r"\b(U ?1[5-9]|U ?2[0-3]|O ?1[5-9]|YOUTH|JEUNES|FEMININE?S?|WOMEN|WOMENS|FEMENIL|FEMMES|RESERVES?)\b")
STOP = {"FC", "CF", "AC", "AS", "SC", "RC", "SL", "CD", "UD", "SS", "SV", "AFC", "CLUB", "DE", "LA", "LE", "LES", "DU", "DES",
        "THE", "OF", "VFB", "VFL", "TSG", "FK", "SK", "NK", "PAE", "KV", "KRC", "RSC", "BC", "CFC", "OSC", "STADE", "1907",
        "SPORTING", "CALCIO", "REAL", "UNITED", "CITY", "ATHLETIC", "OLYMPIQUE", "RACING", "SPORT", "SPORTS", "FOOTBALL",
        "BALOMPIE", "1899", "1909", "1913", "04", "05", "96", "09", "MAN", "AND", "ET", "VS", "V"}
# Même club, autre langue ou surnom (tout en forme normalisée).
ALIAS = {
    "BARCELONE": "BARCELONA", "BARCA": "BARCELONA", "NAPLES": "NAPOLI", "SEVILLE": "SEVILLA", "BRUGES": "BRUGGE",
    "PRAGUE": "PRAHA", "ATHENES": "AEK", "MUNICH": "BAYERN", "MUNCHEN": "BAYERN", "LISBONNE": "LISBOA", "LISBON": "LISBOA",
    "ROME": "ROMA", "TURIN": "TORINO", "MILAN": "MILAN", "INTERNAZIONALE": "INTER", "ATLETI": "ATLETICO",
    "PSG": "PARIS", "OM": "MARSEILLE", "OL": "LYON", "LOSC": "LILLE", "OGCN": "NICE", "SRFC": "RENNES", "ASSE": "ETIENNE",
    "GLADBACH": "MONCHENGLADBACH", "BETIS": "BETIS", "BVB": "DORTMUND", "SPURS": "TOTTENHAM", "WOLVES": "WOLVERHAMPTON",
    "COME": "COMO", "UTD": "UNITED", "COPENHAGUE": "KOBENHAVN", "COPENHAGEN": "KOBENHAVN", "ANVERS": "ANTWERP",
    "BELGRADE": "ZVEZDA", "ETOILE": "ZVEZDA", "CRVENA": "ZVEZDA", "STAVANGER": "VIKING", "GLASGOW": "GLASGOW",
    "SALZBOURG": "SALZBURG", "VIENNE": "WIEN", "BALE": "BASEL", "GENES": "GENOA", "FLORENCE": "FIORENTINA",
    "LEVERKUSEN": "LEVERKUSEN", "EINDHOVEN": "PSV", "ROTTERDAM": "FEYENOORD", "ISTANBUL": "ISTANBUL", "BODO": "BODO",
}


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").replace("ø", "o").replace("Ø", "O").replace("æ", "ae").replace("ß", "ss")
    s = "".join(c for c in s if not unicodedata.combining(c)).upper()
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return [ALIAS.get(t, t) for t in s.split()]


def team_keys(names):
    """Mots qui désignent le club (« Atlético », « Madrid »…), sans les mots trop communs (FC, Real, United…)."""
    keys = set()
    for n in names:
        if not n:
            continue
        toks = norm(n)
        keys |= {t for t in toks if t not in STOP and len(t) >= 3}
        if len(n.strip()) <= 4 and n.strip().isupper():
            keys.add(ALIAS.get(n.strip().upper(), n.strip().upper()))
    if "INTER" in keys:
        keys -= {"MILANO", "MILAN"}  # l'Inter se reconnaît à « Inter », pas à « Milan »
    # Un nom fait seulement de mots communs (« Man United ») : on garde « UNITED » pour ne pas le perdre.
    if not keys:
        for n in names:
            keys |= {t for t in norm(n) if len(t) >= 3 and t not in {"FC", "AND", "THE"}}
    return keys


# Clubs d'une même ville : « Manchester » seul ne dit pas si c'est United ou City.
RIVALS = [{"UNITED", "CITY"}, {"REAL", "ATLETICO"}, {"INTER", "AC"}]


def rival(team_words, words):
    for group in RIVALS:
        mine = [m for m in group if m in team_words]
        if len(mine) == 1 and mine[0] not in words and any(o in words for o in group - {mine[0]}):
            return True
    return False


def same(a, b):
    return a == b or (min(len(a), len(b)) >= 5 and (a.startswith(b) or b.startswith(a)))


def score(keys, words):
    return sum(1 for k in keys if any(same(k, w) for w in words))


def _team_names(event, side):
    return [event.get(side)] + list(event.get(f"{side}Names", []))


def assign(events, programmes):
    """programmes : [(start_datetime, channel_id, texte)] → event["tv"]["fr"] = [ids], dans l'ordre du guide."""
    by_event = {}
    for start, channel_id, text in programmes:
        if NOT_LIVE.search(" ".join(norm(text))):
            continue
        words = norm(text)
        best, best_score = None, 0
        for e in events:
            if not e.get("away"):
                continue
            kickoff = datetime.fromtimestamp(e["start"] / 1000, timezone.utc)
            if not (kickoff - BEFORE <= start <= kickoff + AFTER):
                continue
            if YOUTH.search(" ".join(words)) and not YOUTH.search(" ".join(norm(f"{e.get('home')} {e.get('away')} {e.get('competition')}"))):
                continue
            hn, an = _team_names(e, "home"), _team_names(e, "away")
            if rival(norm(" ".join(filter(None, hn))), words) or rival(norm(" ".join(filter(None, an))), words):
                continue
            h, a = score(team_keys(hn), words), score(team_keys(an), words)
            if h and a and h + a > best_score:
                best, best_score = e, h + a
            elif h and a and h + a == best_score:
                best = None  # deux matchs aussi plausibles : on ne devine pas
        if best is not None:
            ids = by_event.setdefault(best["id"], [])
            if channel_id not in ids:
                ids.append(channel_id)
    for e in events:
        if e["id"] in by_event:
            e.setdefault("tv", {})["fr"] = by_event[e["id"]]
    return len(by_event)


def parse_xmltv(stream, cat, since, until):
    """Programmes des grandes chaînes reconnues (channels.json) entre [since, until]."""
    chans, progs = {}, []
    for _, el in ET.iterparse(stream, events=("end",)):
        if el.tag == "channel":
            r = catalogue.classify(cat, el.findtext("display-name") or "", default_country="FR")
            if r:
                chans[el.get("id")] = r["id"]
            el.clear()
        elif el.tag == "programme":
            cid = chans.get(el.get("channel"))
            if cid:
                start = datetime.strptime(el.get("start")[:20].strip(), "%Y%m%d%H%M%S %z")
                if since <= start <= until:
                    text = " ".join(filter(None, [el.findtext("title"), el.findtext("sub-title")]))
                    progs.append((start.astimezone(timezone.utc), cid, text))
            el.clear()
    return progs


def add_tv(events, cat, url=XMLTV_URL, path=None):
    now = datetime.now(timezone.utc)
    if path:
        stream = open(path, "rb")
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "popcorn-plus-feed"})
        with urllib.request.urlopen(req, timeout=180) as r:
            z = zipfile.ZipFile(io.BytesIO(r.read()))
        stream = z.open([n for n in z.namelist() if n.endswith(".xml")][0])
    with stream:
        progs = parse_xmltv(stream, cat, now - timedelta(hours=6), now + timedelta(days=10))
    return assign(events, progs)


if __name__ == "__main__":
    import json
    cat = catalogue.load(sys.argv[1])
    with open(sys.argv[2], encoding="utf-8") as f:
        feed = json.load(f)
    n = add_tv(feed["events"], cat, path=sys.argv[3] if len(sys.argv) > 3 else None)
    print(f"{n} matchs avec leur chaîne française")
    for e in feed["events"]:
        if "tv" in e:
            print(datetime.fromtimestamp(e["start"] / 1000).strftime("%a %d %H:%M"), e.get("home"), "-", e.get("away"), "→", e["tv"])
