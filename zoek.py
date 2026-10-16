#!/usr/bin/env python3
"""Zoekt hoge personen-MPV's (Berlingo, Partner, Kangoo ...) op Marktplaats en AutoScout24,
haalt per kenteken de RDW-gegevens op en schrijft docs/data.json. Alleen standaardbibliotheek."""
import json, math, re, time, urllib.request, urllib.error, urllib.parse, datetime, pathlib, sys

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept-Language": "nl-NL,nl;q=0.9"}
ROOT = pathlib.Path(__file__).parent
CFG = json.load(open(ROOT / "config.json"))
MAX = CFG["max_prijs"]
RDW = "https://opendata.rdw.nl/resource/"
MAANDEN = {m: i + 1 for i, m in enumerate("jan feb mrt apr mei jun jul aug sep okt nov dec".split())}
APK_RE = re.compile(r"apk[^0-9a-z]{0,12}(?:tot|t/m|geldig tot|t\.m\.?)?[^0-9a-z]{0,4}(\d{1,2})[-/. ](\d{1,2}|[a-z]{3,9})[-/. ](\d{2,4})", re.I)
APK_RE2 = re.compile(r"apk[^0-9]{0,15}(\d{1,2})[-/.](\d{4}|\d{2})\b", re.I)
KENTEKEN_RE = re.compile(r"\b(?:kenteken[: ]+)([A-Z0-9]{1,3}[- ]?[A-Z0-9]{2,3}[- ]?[A-Z0-9]{1,3})\b", re.I)
TITEL_VAN = re.compile(r"\b(cargo|express|maxi cargo|van|bestel\w*|l[12]h[12]|600|profit\+?|furgon\w*)\b", re.I)
EXPORT_RE = re.compile(r"export|duits kenteken|duitse kenteken|belgisch|belgische", re.I)


def haal(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            if i == tries - 1:
                print("FOUT", url[:90], e, file=sys.stderr)
                return ""
            time.sleep(10 if "403" in str(e) or "429" in str(e) else 2)


def apk_uit_tekst(t):
    """Zoek 'APK tot dd-mm-jjjj' in vrije tekst. Geeft YYYY-MM-DD (dag 28 bij alleen maand) of ''."""
    m = APK_RE.search(t or "")
    if m:
        mnd, jr = m.group(2), m.group(3)
        mnd = MAANDEN.get(mnd[:3].lower()) if not mnd.isdigit() else int(mnd)
        jr = int(jr) + (2000 if int(jr) < 100 else 0)
        if mnd and 1 <= mnd <= 12 and 2024 <= jr <= 2032:
            dag = int(m.group(1)) if 1 <= int(m.group(1)) <= 31 else 28
            return f"{jr}-{mnd:02d}-{dag:02d}"
    m = APK_RE2.search(t or "")
    if m and 1 <= int(m.group(1)) <= 12:
        jr = int(m.group(2)) + (2000 if int(m.group(2)) < 100 else 0)
        if 2024 <= jr <= 2032:
            return f"{jr}-{int(m.group(1)):02d}-28"
    return ""


def schoon_kenteken(k):
    k = re.sub(r"[^A-Z0-9]", "", (k or "").upper())
    return k if 5 <= len(k) <= 8 and re.search(r"\d", k) and re.search(r"[A-Z]", k) else ""


def attr(l, key):
    for a in l.get("attributes", []) + l.get("extendedAttributes", []):
        if a["key"] == key:
            return a.get("value", "")
    return ""


def heeft_trekhaak(tekst):
    t = (tekst or "").lower()
    return "trekhaak" in t and not re.search(r"(geen|zonder) trekhaak", t)


def grijs_kenteken(tekst, url="", titel=""):
    t = (tekst or "").lower()
    return (bool(TITEL_VAN.search(titel)) or "/bestelauto" in url or "bestelauto" in t or "grijs kenteken" in t
            or "grijskenteken" in t or "bedrijfswagen" in t or "2 zits" in t or "2-zits" in t or "tweezits" in t)


# ---------------------------------------------------------------- Marktplaats
def marktplaats(model):
    uit, off = [], 0
    trefwoord = [w for w in model["mp"].split() if w not in ("citroen", "peugeot", "opel")]
    while off < 600:
        q = urllib.parse.urlencode({"query": model["mp"], "l1CategoryId": 91, "limit": 100, "offset": off,
                                    "sortBy": "PRICE", "sortOrder": "INCREASING"})
        txt = haal("https://www.marktplaats.nl/lrp/api/search?" + q)
        if not txt:
            break
        ls = json.loads(txt).get("listings", [])
        if not ls:
            break
        for l in ls:
            p = l["priceInfo"].get("priceCents", 0) / 100
            pt = l["priceInfo"].get("priceType", "")
            if p > MAX:
                continue
            titel = l["title"]
            if not any(w in titel.lower() for w in trefwoord):
                continue
            jaar, km = attr(l, "constructionYear"), attr(l, "mileage")
            if not jaar or not km:      # geen auto maar onderdeel/aanbieding
                continue
            opties = " ".join(sum([a.get("values", []) for a in l.get("attributes", [])], []))
            tekst = titel + " " + l.get("description", "") + " " + l.get("categorySpecificDescription", "")
            url = "https://www.marktplaats.nl" + l.get("vipUrl", "")
            kt = KENTEKEN_RE.search(tekst)
            uit.append({
                "bron": "Marktplaats", "id": "mp-" + l["itemId"], "model": model["naam"], "titel": titel,
                "prijs": round(p), "vraagprijs": round(p),
                "prijs_twijfel": p < 100 or pt in ("SEE_DESCRIPTION", "ON_REQUEST"),
                "prijstype": {"FIXED": "vast", "MIN_BID": "bieden", "FREE": "gratis", "FAST_BID": "bieden",
                              "SEE_DESCRIPTION": "zie omschrijving"}.get(pt, pt.lower()),
                "jaar": int(jaar), "km": int(km), "brandstof": attr(l, "fuel"), "apk": apk_uit_tekst(tekst), "apk_bron": "tekst" if apk_uit_tekst(tekst) else "",
                "kenteken": schoon_kenteken(kt.group(1)) if kt else "", "trekhaak": heeft_trekhaak(opties + " " + tekst),
                "lat": l["location"].get("latitude"), "lon": l["location"].get("longitude"),
                "plaats": l["location"].get("cityName", ""), "verkoper": l.get("sellerInformation", {}).get("sellerName", ""),
                "type_verkoper": "", "grijs": grijs_kenteken(tekst, url, titel), "marge": "marge" in tekst.lower(),
                "export": bool(EXPORT_RE.search(tekst)), "geplaatst": l.get("date", ""),
                "foto": ("https:" + l["imageUrls"][0]) if l.get("imageUrls") else "",
                "url": url, "tekst": re.sub(r"\s+", " ", l.get("description", ""))[:240],
            })
        if ls[-1]["priceInfo"].get("priceCents", 0) / 100 > MAX:
            break
        off += 100
        time.sleep(0.7)
    return uit


def mp_detail(a, oud):
    """Kenteken en biedingen uit de advertentiepagina. Vaste prijs + bekend kenteken: niet opnieuw ophalen."""
    o = oud.get(a["id"]) or {}
    if o.get("kenteken"):
        a["kenteken"] = o["kenteken"]
    if a.get("kenteken") and a["prijstype"] != "bieden" and not o.get("biedingen_actief"):
        return
    time.sleep(0.6)
    h = haal(a["url"], tries=3)
    if not h:                           # mislukt: neem eerdere biedingen over
        for k in ("biedingen_actief", "biedingen_aantal", "hoogste_bod", "laatste_bod_datum", "biedingen"):
            if k in o:
                a[k] = o[k]
        if o.get("prijstype") == "hoogste bod" and a["prijs"] < 100:
            a["prijs"], a["prijstype"], a["prijs_twijfel"] = o["prijs"], "hoogste bod", False
        return
    m = re.search(r'"licensePlate":"([^"]+)"', h)
    if m and schoon_kenteken(m.group(1)):
        a["kenteken"] = schoon_kenteken(m.group(1))
    i = h.find('"bidsInfo":')
    if i >= 0:
        try:
            info, _ = json.JSONDecoder().raw_decode(h[i + len('"bidsInfo":'):])
        except Exception:
            return
        bieden = []
        for b in info.get("bids", []):
            eur = b["value"] / 100
            if eur <= 50000:            # onzin-biedingen (bijv. €9.999.902) negeren
                bieden.append({"bedrag": round(eur), "datum": b["date"], "naam": (b.get("user") or {}).get("nickname", "")})
        a["biedingen_actief"] = bool(info.get("isBiddingEnabled"))
        a["biedingen_aantal"] = len(info.get("bids", []))
        if bieden:
            top = max(bieden, key=lambda b: (b["bedrag"], b["datum"]))
            a["hoogste_bod"] = top
            a["laatste_bod_datum"] = max(b["datum"] for b in bieden)
            a["biedingen"] = sorted(bieden, key=lambda b: b["datum"], reverse=True)[:40]
            if a["prijs"] < 100:           # vraagprijs ontbreekt: toon het hoogste bod als prijs
                a["prijs"], a["prijstype"], a["prijs_twijfel"] = top["bedrag"], "hoogste bod", False


# ---------------------------------------------------------------- AutoScout24
def autoscout(model):
    uit = []
    mk, md = model["as"]
    for pag in range(1, 8):
        h = haal(f"https://www.autoscout24.nl/lst/{mk}/{md}?atype=C&cy=NL&priceto={MAX}&sort=price&desc=0&page={pag}")
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', h, re.S)
        if not m:
            break
        pp = json.loads(m.group(1))["props"]["pageProps"]
        for l in pp.get("listings", []):
            v = l["vehicle"]
            if l["location"].get("countryCode") != "NL":
                continue
            det = {d["ariaLabel"]: d["data"] for d in l.get("vehicleDetails", [])}
            km = re.sub(r"\D", "", det.get("Kilometerstand", "") or "0")
            jr = re.search(r"(\d{4})", det.get("Bouwjaar", ""))
            uit.append({
                "bron": "AutoScout24", "id": "as-" + l["id"], "model": model["naam"],
                "titel": f'{v.get("make","")} {v.get("model","")} {v.get("modelVersionInput") or ""}'.strip(),
                "prijs": l["price"]["priceRaw"], "vraagprijs": l["price"]["priceRaw"],
                "prijs_twijfel": l["price"]["priceRaw"] < 100, "prijstype": "vast",
                "jaar": int(jr.group(1)) if jr else 0, "km": int(km or 0), "brandstof": v.get("fuel", ""),
                "apk": "", "apk_bron": "", "kenteken": "", "trekhaak": False,
                "postcode": (l["location"].get("zip") or "").upper().replace(" ", ""),
                "plaats": l["location"].get("city", "").title(), "verkoper": "",
                "type_verkoper": "handelaar" if l["seller"]["type"] == "Dealer" else "particulier",
                "grijs": bool(TITEL_VAN.search(v.get("modelVersionInput") or "")), "marge": False, "export": False,
                "geplaatst": "", "foto": (l.get("images") or [""])[0],
                "url": "https://www.autoscout24.nl" + l["url"].split("?")[0], "tekst": "",
            })
        if pag >= pp.get("numberOfPages", 1):
            break
        time.sleep(0.7)
    return uit


def as_detail(a, oud):
    """Detailpagina: omschrijving, carrosserietype, kenteken (verzekeringsoptie) en trekhaak."""
    o = oud.get(a["id"])
    if o and o.get("verrijkt") and "kenteken" in o:
        for k in ("apk", "apk_bron", "tekst", "grijs", "marge", "export", "carrosserie", "kenteken", "trekhaak"):
            if k in o:
                a[k] = o[k]
        a["verrijkt"] = True
        return
    h = haal(a["url"], tries=2)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', h, re.S)
    if not m:
        return
    try:
        det = json.loads(m.group(1))["props"]["pageProps"]["listingDetails"]
    except Exception:
        return
    s = json.dumps(det, ensure_ascii=False)
    desc = re.sub(r"<br\s*/?>", " ", det.get("description") or "")
    veh = det.get("vehicle", {})
    bt = (veh.get("bodyType") or {}).get("formatted", "") if isinstance(veh.get("bodyType"), dict) else ""
    if not bt:
        mm = re.search(r'"bodyType": ?\{"formatted": ?"([^"]+)"', s)
        bt = mm.group(1) if mm else ""
    a["carrosserie"] = bt
    a["kenteken"] = schoon_kenteken(veh.get("licensePlate") or (det.get("identifier") or {}).get("licensePlate"))
    a["apk"] = apk_uit_tekst(desc)
    a["apk_bron"] = "tekst" if a["apk"] else ""
    a["trekhaak"] = bool(re.search(r'"id": ?"Trekhaak"', s)) or heeft_trekhaak(desc)
    a["tekst"] = re.sub(r"\s+", " ", desc)[:240]
    a["marge"] = "marge" in desc.lower()
    a["export"] = bool(EXPORT_RE.search(desc))
    a["grijs"] = bt.lower() in ("bestelwagen", "bedrijfswagen", "bestelbus") or grijs_kenteken(desc, "", a["titel"])
    a["verrijkt"] = True
    time.sleep(0.4)


# ---------------------------------------------------------------- RDW
def rdw_json(dataset, where, select=""):
    q = {"$where": where, "$limit": 5000}
    if select:
        q["$select"] = select
    t = haal(RDW + dataset + ".json?" + urllib.parse.urlencode(q))
    try:
        return json.loads(t) if t else []
    except Exception:
        return []


def datum(s):
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if s and len(s) >= 8 and s[:8].isdigit() else ""


def rdw_ophalen(kentekens):
    uit, gebreken_namen = {}, {}
    kl = sorted(kentekens)
    for i in range(0, len(kl), 40):
        deel = kl[i:i + 40]
        w = "kenteken in(" + ",".join(f"'{k}'" for k in deel) + ")"
        for r in rdw_json("m9d7-ebf2", w):
            uit[r["kenteken"]] = {
                "voertuigsoort": r.get("voertuigsoort", ""), "inrichting": r.get("inrichting", ""),
                "merk": r.get("merk", ""), "type": r.get("handelsbenaming", ""),
                "apk": r.get("vervaldatum_apk_dt", "")[:10], "verzekerd": r.get("wam_verzekerd", ""),
                "tenaam": datum(r.get("datum_tenaamstelling")), "nl_sinds": datum(r.get("datum_eerste_tenaamstelling_in_nederland")),
                "eerste_toelating": datum(r.get("datum_eerste_toelating")), "zitplaatsen": r.get("aantal_zitplaatsen", ""),
                "massa_rijklaar": r.get("massa_rijklaar", ""), "trek_geremd": r.get("maximum_trekken_massa_geremd", ""),
                "trek_ongeremd": r.get("maximum_massa_trekken_ongeremd", ""), "cilinderinhoud": r.get("cilinderinhoud", ""),
                "tellerstand": r.get("tellerstandoordeel", ""), "terugroep": r.get("openstaande_terugroepactie_indicator", ""),
                "export": r.get("export_indicator", ""), "wok": r.get("wacht_op_keuren", ""),
                "tenaamstellen": r.get("tenaamstellen_mogelijk", ""), "taxi": r.get("taxi_indicator", ""),
                "zuinigheid": r.get("zuinigheidsclassificatie", ""), "kleur": r.get("eerste_kleur", ""),
            }
        for r in rdw_json("8ys7-d773", w):
            if r.get("brandstof_volgnummer") == "1" and r["kenteken"] in uit:
                uit[r["kenteken"]]["brandstof"] = r.get("brandstof_omschrijving", "")
                uit[r["kenteken"]]["euro"] = r.get("uitlaatemissieniveau", "")
        per = {}
        for r in rdw_json("a34c-vvps", w):
            per.setdefault(r["kenteken"], []).append(r)
        for k, rs in per.items():
            if k not in uit:
                continue
            laatste = max(r["meld_datum_door_keuringsinstantie"] for r in rs)
            uit[k]["laatste_keuring"] = datum(laatste)
            ids = [r["gebrek_identificatie"] for r in rs if r["meld_datum_door_keuringsinstantie"] == laatste]
            uit[k]["gebreken_ids"] = ids
    alle_ids = {g for v in uit.values() for g in v.get("gebreken_ids", [])}
    if alle_ids:
        for r in rdw_json("hx2c-gt7k", "gebrek_identificatie in(" + ",".join(f"'{g}'" for g in sorted(alle_ids)) + ")"):
            gebreken_namen[r["gebrek_identificatie"]] = r.get("gebrek_omschrijving", "")
    for v in uit.values():
        v["gebreken"] = [gebreken_namen.get(g, "gebrek " + g) for g in v.pop("gebreken_ids", [])]
    return uit


def pas_rdw_toe(a, r):
    a["rdw"] = r
    if r["apk"]:
        a["apk"], a["apk_bron"] = r["apk"], "RDW"
    # gele kentekens = personenauto; alles anders is (grijs) bedrijfs-/bestelauto
    a["grijs"] = r["voertuigsoort"] != "Personenauto"
    a["export"] = a["export"] or r["export"] == "Ja"


# ---------------------------------------------------------------- afstand
GEO_PAD = ROOT / "docs" / "geocache.json"


def geocode(zoek, cache):
    """Postcode of plaatsnaam -> [lat, lon] via PDOK (gratis, geen sleutel). Resultaat wordt gecachet."""
    if zoek in cache:
        return cache[zoek]
    is_pc = re.fullmatch(r"\d{4}[A-Z]{2}", zoek)
    q = urllib.parse.urlencode({"q": ("postcode:" + zoek) if is_pc else zoek, "fl": "centroide_ll", "rows": 1,
                                "fq": "type:postcode" if is_pc else "type:woonplaats"})
    t = haal("https://api.pdok.nl/bzk/locatieserver/search/v3_1/free?" + q, tries=2)
    try:
        m = re.search(r"POINT\(([\d.]+) ([\d.]+)\)", t)
        cache[zoek] = [float(m.group(2)), float(m.group(1))] if m else None
    except Exception:
        cache[zoek] = None
    return cache[zoek]


def hemelsbreed(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(x))


def bereken_afstanden(lijst):
    thuis = CFG.get("thuis")
    if not thuis:
        return
    try:
        cache = json.load(open(GEO_PAD))
    except Exception:
        cache = {}
    for a in lijst:
        punt = None
        if a.get("lat") and a.get("lon"):
            punt = [a["lat"], a["lon"]]
        elif a.get("postcode"):
            punt = geocode(a["postcode"], cache)
        if not punt and a.get("plaats"):
            punt = geocode(a["plaats"], cache)
        if punt:
            a["afstand_km"] = round(hemelsbreed([thuis["lat"], thuis["lon"]], punt))
    GEO_PAD.write_text(json.dumps(cache, separators=(",", ":")), encoding="utf-8")


# ---------------------------------------------------------------- historie
def nu_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="minutes")


def probe(a):
    """Staat de advertentie nog online? 'online' / 'weg' / '?' (onbekend, bijv. geblokkeerd)."""
    try:
        with urllib.request.urlopen(urllib.request.Request(a["url"], headers=UA), timeout=25) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return "weg" if e.code in (404, 410) else "?"
    except Exception:
        return "?"
    return "online" if a["id"].split("-", 1)[1] in body else "weg"


def likes_laden():
    try:
        v = json.load(open(ROOT / "docs" / "likes.json"))
        return {k for k, x in v.items() if x.get("v") == "like"}
    except Exception:
        return set()


def sleutel(a):
    return a.get("kenteken") or a["id"]


BEWAAR_DAGEN = 30        # niet-gelikete offline auto's bewaren we zo lang


def main():
    pad = ROOT / "docs" / "data.json"
    oud = {}
    if pad.exists():
        try:
            oud = {a["id"]: a for a in json.load(open(pad))["auto"]}
        except Exception:
            pass
    nu = nu_iso()
    likes = likes_laden()
    alle, bronstatus = {}, {}
    for model in CFG["modellen"]:
        for naam, fn in (("Marktplaats", marktplaats), ("AutoScout24", autoscout)):
            try:
                res = fn(model)
                bronstatus[naam] = bronstatus.get(naam, 0) + len(res)
                for a in res:
                    alle[a["id"]] = a
            except Exception as e:
                print("FOUT", naam, model["naam"], e, file=sys.stderr)
                bronstatus.setdefault(naam, 0)
    lijst = list(alle.values())
    for a in lijst:                     # serieel en rustig: Marktplaats blokkeert snelle verzoeken
        mp_detail(a, oud) if a["bron"] == "Marktplaats" else as_detail(a, oud)
    bereken_afstanden(lijst)
    kentekens = {a["kenteken"] for a in lijst if a.get("kenteken")}
    rdw = rdw_ophalen(kentekens) if kentekens else {}
    for a in lijst:
        if a.get("kenteken") in rdw:
            pas_rdw_toe(a, rdw[a["kenteken"]])
        o = oud.get(a["id"])
        a["online"], a["gemist"], a["laatst_gezien"], a["offline_sinds"] = True, 0, nu, None
        a["eerst_gezien"] = o["eerst_gezien"] if o else nu
        a["gebeurtenissen"] = list(o.get("gebeurtenissen") or [{"d": o["eerst_gezien"], "t": "online"}]) if o else [{"d": nu, "t": "online"}]
        if o and not o.get("online", True):
            a["gebeurtenissen"].append({"d": nu, "t": "weer online"})
        ph = list(o.get("prijshistorie", [])) if o else []
        if not ph and o:
            ph = [{"d": o["eerst_gezien"], "p": o["prijs"], "t": o.get("prijstype", "")}]
        if not ph or ph[-1]["p"] != a["prijs"] or ph[-1]["t"] != a["prijstype"]:
            ph.append({"d": nu, "p": a["prijs"], "t": a["prijstype"]})
        a["prijshistorie"] = ph
    # Dezelfde auto op meerdere sites (zelfde kenteken of model/jaar/km/prijs): één regel met extra link
    uniek = {}
    for a in sorted(lijst, key=lambda a: a["bron"] != "Marktplaats"):
        sl = ("kt", a["kenteken"]) if a.get("kenteken") else (a["model"], a["jaar"], a["km"], a["prijs"]) if a["km"] else a["id"]
        if sl in uniek:
            uniek[sl].setdefault("ook_op", []).append({"bron": a["bron"], "url": a["url"], "prijs": a["prijs"]})
            if not uniek[sl].get("apk"):
                uniek[sl]["apk"] = a.get("apk", "")
            uniek[sl]["trekhaak"] = uniek[sl]["trekhaak"] or a["trekhaak"]
        else:
            uniek[sl] = a
    # Auto's van een eerdere run die nu niet meer in de zoekresultaten staan
    for oid, o in oud.items():
        if oid in alle:
            continue
        o = dict(o)
        if o.get("online", True):
            if bronstatus.get(o["bron"], 0) == 0:       # bron faalde: niets concluderen
                uniek[("oud", oid)] = o
                continue
            o["gemist"] = o.get("gemist", 0) + 1
            if o["gemist"] >= 2:
                st = probe(o)
                if st == "weg":
                    o["online"], o["offline_sinds"] = False, nu
                    o["gebeurtenissen"] = o.get("gebeurtenissen", []) + [{"d": nu, "t": "offline"}]
                elif st == "online":
                    o["gemist"] = 0               # nog online (bijv. prijs boven zoekgrens)
        elif not o.get("offline_sinds"):
            o["offline_sinds"] = nu
        if not o.get("online", True) and sleutel(o) not in likes and o["id"] not in likes:
            ouder = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(o["offline_sinds"].replace("Z", "+00:00"))).days
            if ouder > BEWAAR_DAGEN:
                continue
        uniek[("oud", oid)] = o
    uit = {"bijgewerkt": nu, "max_prijs": MAX, "thuis": CFG.get("thuis", {}).get("naam", ""), "bronnen": bronstatus, "rdw_gevonden": len(rdw), "kentekens": len(kentekens),
           "auto": sorted(uniek.values(), key=lambda a: a["prijs"])}
    pad.write_text(json.dumps(uit, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{sum(a.get('online', True) for a in uit['auto'])} online, {sum(not a.get('online', True) for a in uit['auto'])} offline, bronnen: {bronstatus}, kentekens: {len(kentekens)}, RDW: {len(rdw)}")


if __name__ == "__main__":
    main()
