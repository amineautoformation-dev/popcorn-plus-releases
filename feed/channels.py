#!/usr/bin/env python3
"""
Reconnaissance des grandes chaînes dans les noms des fournisseurs, quel que soit l'habillage :
« FR: CANAL+ LIVE 3 ᴴᴰ », « 8K: beIN SP⚽RTS 1 ENGLISH ᴴᴰ ◉ », « UK| TNT SPORTS 1 HEVC 4K »…

Même algorithme que l'app (ChannelNames.kt) : les deux sont testés sur les mêmes exemples.
  1. décorations retirées : lettres en exposant (ᴴᴰ, ᴿᴬᵂ, ³⁸⁴⁰ᴾ, ʰᵉᵛᶜ), émojis, symboles (◉ ▶ ★) ; ⚽ au milieu d'un mot = O ;
  2. préfixes « FR: », « UK| », « 8K: » mis de côté (ils donnent le pays) ;
  3. majuscules, sans accents, qualité retirée (HD, 4K, HEVC, RAW, 50 FPS…) ;
  4. premier motif du catalogue (channels.json) qui correspond au nom normalisé.

Usage (rapport sur une liste de chaînes Xtream) :
  python3 channels.py channels.json get_live_streams.json get_live_categories.json rapport.csv
"""

import csv
import json
import re
import sys
import unicodedata

# Lettres et chiffres en exposant ou « petites capitales » : habillage du fournisseur (ᴴᴰ, ᴿᴬᵂ, ⁴ᴷ, ʰᵉᵛᶜ…).
DECOR_RANGES = [(0x02B0, 0x02FF), (0x1D00, 0x1DBF), (0x2070, 0x209F), (0x2C7C, 0x2C7D), (0xA700, 0xA71F)]
DECOR_CHARS = {0x00B2, 0x00B3, 0x00B9, 0x00AA, 0x00BA}
BALL_IN_WORD = re.compile(r"(?<=[^\W\d_])⚽️?(?=[^\W\d_])")
PREFIX = re.compile(r"^\s*[\[(]?\s*([A-Z0-9+&]{1,6}|\d{1,2}/\d{1,2})\s*[\])]?\s*[:|]\s*")
TAGS = re.compile(
    r"\b(UHD|FHD|HD|SD|LQ|HQ|4K|8K|2K|HEVC|H ?26[45]|AVC|RAW|HDR|DOLBY|VIP|BACKUP|ALT|MULTI|LOCAL|SAT|MOBIL|ONLY|"
    r"\d{3,4} ?P|\d{2,3} ?FPS|FPS|PLUS ?HD|NEW|TEST)\b")
SPACES = re.compile(r"\s+")

# Préfixes qui ne sont pas des pays : bouquets ou qualités.
NOT_COUNTRY = {"VIP", "PRIME", "GOLD", "RK", "T2", "SAT", "F", "TV", "8K", "4K", "HD", "SD", "SPO", "GOBX", "NOW", "C+",
               "AMP", "MEO", "DSTV", "AT&T", "MAX", "PPV", "ASIA", "AFR", "LA", "TS", "24/7", "AF"}
COUNTRY_ALIAS = {"GB": "UK", "EN": "UK", "ENG": "UK", "KSA": "SA", "UAE": "AE", "MENA": "AR", "ARAB": "AR", "FRA": "FR",
                 "AF": "AF", "AFR": "AF"}


def _decor(c):
    o = ord(c)
    return o in DECOR_CHARS or any(a <= o <= b for a, b in DECOR_RANGES)


def normalize(raw):
    """→ (codes de préfixe, nom normalisé)."""
    s = BALL_IN_WORD.sub("O", raw)
    s = "".join(" " if _decor(c) else c for c in s)
    s = unicodedata.normalize("NFKC", s)
    codes = []
    for _ in range(3):
        m = PREFIX.match(s.upper())
        if not m:
            break
        codes.append(m.group(1))
        s = s[m.end():]
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = s.upper().replace("'", "").replace("’", "")
    # Tout ce qui n'est ni lettre, ni chiffre, ni « + » devient une espace (émojis, ◉, |, #, tirets…).
    s = "".join(c if (c.isalnum() or c in "+&") else " " for c in s)
    s = s.replace("&", " ")
    s = TAGS.sub(" ", s)
    s = SPACES.sub(" ", s).strip()
    s = re.sub(r"\s*\+\s*", "+ ", s).strip()      # « CANAL +SPORT », « CANAL+SPORT » → « CANAL+ SPORT »
    s = re.sub(r"\+ $", "+", s)
    s = re.sub(r"(\d) \+", r"\1+", s)               # « LIGUE 1 + » → « LIGUE 1+ »
    return codes, SPACES.sub(" ", s).strip()


def country(codes, category):
    """Pays de la chaîne : préfixe de la catégorie (« AR| BEIN SPORTS »), sinon du nom."""
    cat_codes, _ = normalize(category or "") if category else ([], "")
    for code in cat_codes + codes:
        code = COUNTRY_ALIAS.get(code, code)
        if code not in NOT_COUNTRY and re.fullmatch(r"[A-Z]{2,3}", code):
            return code
    return None


def load(path):
    with open(path, encoding="utf-8") as f:
        entries = json.load(f)["channels"]
    for e in entries:
        e["rx"] = re.compile(e["match"])
    return entries


def classify(catalogue, raw_name, category=None, default_country=None):
    """→ {id, name, lang, family} ou None si ce n'est pas une grande chaîne connue."""
    if raw_name.lstrip().startswith("#"):
        return None  # ligne de séparation du fournisseur (« ##### LIGUE1+ ᴮᴱ ##### »), pas une chaîne
    codes, n = normalize(raw_name)
    c = country(codes, category) or default_country
    for e in catalogue:
        m = e["rx"].match(n)
        if not m:
            continue
        if "countries" in e and c not in e["countries"]:
            continue
        fill = lambda t: re.sub(r"\$(\d)", lambda g: (m.group(int(g.group(1))) or "").strip(), t)
        cid = re.sub(r"[^a-z0-9]+", "-", fill(e["id"]).lower()).strip("-")
        return {"id": cid, "name": fill(e["name"]).replace("  ", " ").strip(), "lang": e["lang"], "family": e.get("family"), "country": c}
    return None


def report(catalogue_path, streams_path, categories_path, out_path):
    cat = load(catalogue_path)
    with open(streams_path, encoding="utf-8") as f:
        streams = json.load(f)
    with open(categories_path, encoding="utf-8") as f:
        cats = {c["category_id"]: c["category_name"] for c in json.load(f)}
    found = {}
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "nom officiel", "langue", "pays", "nom chez le fournisseur", "catégorie"])
        for s in streams:
            r = classify(cat, s["name"], cats.get(str(s.get("category_id"))))
            if r:
                w.writerow([r["id"], r["name"], r["lang"], r["country"] or "", s["name"], cats.get(str(s.get("category_id")), "")])
                found.setdefault(r["id"], []).append(s["name"])
    return found


if __name__ == "__main__":
    found = report(*sys.argv[1:5])
    print(f"{len(found)} grandes chaînes reconnues ({sum(len(v) for v in found.values())} versions)")
