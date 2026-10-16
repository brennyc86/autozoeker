#!/usr/bin/env python3
"""Zoekt hoge personen-MPV's (Berlingo, Partner, Kangoo ...) op Marktplaats en AutoScout24
en schrijft docs/data.json. Alleen standaardbibliotheek."""
import json, re, time, urllib.request, urllib.parse, datetime, pathlib, sys

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept-Language": "nl-NL,nl;q=0.9"}
ROOT = pathlib.Path(__file__).parent
CFG = json.load(open(ROOT / "config.json"))
MAX = CFG["max_prijs"]
APK_RE = re.compile(r"apk[^0-9a-z]{0,12}(?:tot|t/m|geldig tot|t\.m\.?)?[^0-9a-z]{0,4}(\d{1,2})[-/. ](\d{1,2}|[a-z]{3,9})[-/. ](\d{2,4})", re.I)
APK_RE2 = re.compile(r"apk[^0-9]{0,15}(\d{1,2})[-/.](\d{4}|\d{2})\b", re.I)
MAANDEN = {m: i + 1 for i, m in enumerate("jan feb mrt apr mei jun jul aug sep okt nov dec".split())}


def haal(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            if i == tries - 1:
                print("FOUT", url[:90], e, file=sys.stderr)
                return ""
            time.sleep(2)


def apk_uit_tekst(t):
    """Zoek 'APK tot dd-mm-jjjj' in vrije tekst. Geeft YYYY-MM of ''."""
    m = APK_RE.search(t or "")
    if m:
        mnd, jr = m.group(2), m.group(3)
        mnd = MAANDEN.get(mnd[:3].lower()) if not mnd.isdigit() else int(mnd)
        jr = int(jr) + (2000 if int(jr) < 100 else 0)
        if mnd and 1 <= mnd <= 12 and 2024 <= jr <= 2032:
            return f"{jr}-{mnd:02d}"
    m = APK_RE2.search(t or "")
    if m and 1 <= int(m.group(1)) <= 12:
        jr = int(m.group(2)) + (2000 if int(m.group(2)) < 100 else 0)
        if 2024 <= jr <= 2032:
            return f"{jr}-{int(m.group(1)):02d}"
    return ""


def attr(l, key):
    for a in l.get("attributes", []) + l.get("extendedAttributes", []):
        if a["key"] == key:
            return a.get("value", "")
    return ""


TITEL_VAN = re.compile(r"\b(cargo|express|maxi cargo|van|bestel\w*|l[12]h[12]|600|profit\+?|furgon\w*)\b", re.I)


def grijs_kenteken(tekst, url="", titel=""):
    t = (tekst or "").lower()
    return (bool(TITEL_VAN.search(titel)) or "/bestelauto" in url or "bestelauto" in t or "grijs kenteken" in t or "grijskenteken" in t
            or "bedrijfswagen" in t or "2 zits" in t or "2-zits" in t or "tweezits" in t)


def marktplaats(model):
    uit, off = [], 0
    while off < 600:
        q = urllib.parse.urlencode({"query": model["mp"], "l1CategoryId": 91, "limit": 100, "offset": off,
                                    "sortBy": "PRICE", "sortOrder": "INCREASING"})
        txt = haal("https://www.marktplaats.nl/lrp/api/search?" + q)
        if not txt:
            break
        d = json.loads(txt)
        ls = d.get("listings", [])
        if not ls:
            break
        for l in ls:
            p = l["priceInfo"].get("priceCents", 0) / 100
            pt = l["priceInfo"].get("priceType", "")
            if p > MAX and pt != "MIN_BID":
                continue
            if p > MAX:
                continue
            titel = l["title"]
            if not any(w in titel.lower() for w in model["mp"].replace("citroen ", "").replace("peugeot ", "").replace("opel ", "").split()):
                continue
            jaar = attr(l, "constructionYear")
            km = attr(l, "mileage")
            if not jaar or not km:      # geen auto maar onderdeel/aanbieding
                continue
            tekst = titel + " " + l.get("description", "") + " " + l.get("categorySpecificDescription", "")
            url = "https://www.marktplaats.nl" + l.get("vipUrl", "")
            uit.append({
                "bron": "Marktplaats", "id": "mp-" + l["itemId"], "model": model["naam"], "titel": titel,
                "prijs": round(p), "prijs_twijfel": p < 100 or pt in ("MIN_BID", "SEE_DESCRIPTION", "FAST_BID", "ON_REQUEST"), "prijstype": {"FIXED": "vast", "MIN_BID": "bieden", "FREE": "gratis"}.get(pt, pt.lower()),
                "jaar": int(jaar), "km": int(km), "brandstof": attr(l, "fuel"), "apk": apk_uit_tekst(tekst),
                "plaats": l["location"].get("cityName", ""), "verkoper": l.get("sellerInformation", {}).get("sellerName", ""),
                "type_verkoper": "", "grijs": grijs_kenteken(tekst, url, titel),
                "marge": "marge" in tekst.lower(),
                "export": bool(re.search(r"export|duits kenteken|duitse kenteken|belgisch|belgische", tekst, re.I)),
                "geplaatst": l.get("date", ""), "foto": ("https:" + l["imageUrls"][0]) if l.get("imageUrls") else "",
                "url": url, "tekst": re.sub(r"\s+", " ", l.get("description", ""))[:240],
            })
        # gesorteerd op prijs: stop zodra we boven het maximum zitten
        if ls[-1]["priceInfo"].get("priceCents", 0) / 100 > MAX:
            break
        off += 100
        time.sleep(0.7)
    return uit


def autoscout(model):
    uit = []
    mk, md = model["as"]
    for pag in range(1, 8):
        u = f"https://www.autoscout24.nl/lst/{mk}/{md}?atype=C&cy=NL&priceto={MAX}&sort=price&desc=0&page={pag}"
        h = haal(u)
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
                "prijs": l["price"]["priceRaw"], "prijs_twijfel": l["price"]["priceRaw"] < 100, "prijstype": "vast",
                "jaar": int(jr.group(1)) if jr else 0, "km": int(km or 0), "brandstof": v.get("fuel", ""),
                "apk": "", "plaats": l["location"].get("city", "").title(), "verkoper": "",
                "type_verkoper": "handelaar" if l["seller"]["type"] == "Dealer" else "particulier",
                "grijs": bool(TITEL_VAN.search(f'{v.get("modelVersionInput") or ""}')), "marge": False, "export": False, "geplaatst": "",
                "foto": (l.get("images") or [""])[0], "url": "https://www.autoscout24.nl" + l["url"].split("?")[0],
                "tekst": "",
            })
        if pag >= pp.get("numberOfPages", 1):
            break
        time.sleep(0.7)
    return uit


def verrijk_autoscout(a, oud):
    """Detailpagina: omschrijving (APK-tekst) en carrosserietype. Hergebruik eerdere uitkomst."""
    if a["id"] in oud and oud[a["id"]].get("verrijkt"):
        for k in ("apk", "tekst", "grijs", "marge", "export", "carrosserie"):
            a[k] = oud[a["id"]].get(k, a.get(k))
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
    desc = det.get("description") or ""
    desc = re.sub(r"<br\s*/?>", " ", desc)
    bt = (det.get("vehicle", {}).get("bodyType") or {}).get("formatted", "") if isinstance(det.get("vehicle", {}).get("bodyType"), dict) else ""
    if not bt:
        mm = re.search(r'"bodyType": ?\{"formatted": ?"([^"]+)"', s) or re.search(r'"bodyType": ?"([^"]+)"', s)
        bt = mm.group(1) if mm else ""
    a["carrosserie"] = bt
    a["apk"] = apk_uit_tekst(desc) or apk_uit_tekst(s)
    a["tekst"] = re.sub(r"\s+", " ", desc)[:240]
    a["marge"] = "marge" in desc.lower()
    a["export"] = bool(re.search(r"export|duits kenteken|duitse kenteken|belgisch|belgische", desc, re.I))
    a["grijs"] = bt.lower() in ("bestelwagen", "bedrijfswagen", "bestelbus") or grijs_kenteken(desc, "", a["titel"])
    a["verrijkt"] = True
    time.sleep(0.5)


def main():
    pad = ROOT / "docs" / "data.json"
    oud = {}
    if pad.exists():
        try:
            oud = {a["id"]: a for a in json.load(open(pad))["auto"]}
        except Exception:
            pass
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
    for a in alle.values():
        if a["bron"] == "AutoScout24":
            verrijk_autoscout(a, oud)
        o = oud.get(a["id"])
        a["eerst_gezien"] = o["eerst_gezien"] if o else datetime.date.today().isoformat()
        a["nieuw_prijs"] = bool(o and o["prijs"] != a["prijs"])
        a["vorige_prijs"] = o["prijs"] if o and o["prijs"] != a["prijs"] else None
    # Dezelfde auto op meerdere sites (zelfde model/jaar/km/prijs): één regel met extra link
    uniek = {}
    for a in sorted(alle.values(), key=lambda a: a["bron"] != "Marktplaats"):
        sl = (a["model"], a["jaar"], a["km"], a["prijs"])
        if sl in uniek and a["km"]:
            uniek[sl].setdefault("ook_op", []).append({"bron": a["bron"], "url": a["url"]})
            uniek[sl]["apk"] = uniek[sl]["apk"] or a["apk"]
        else:
            uniek[sl if a["km"] else a["id"]] = a
    lijst = sorted(uniek.values(), key=lambda a: a["prijs"])
    # Als een bron volledig faalde, behoud dan de oude advertenties van die bron niet (die zouden verlopen zijn)
    uit = {"bijgewerkt": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="minutes"),
           "max_prijs": MAX, "bronnen": bronstatus, "auto": lijst}
    pad.write_text(json.dumps(uit, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(lijst)} auto's, bronnen: {bronstatus}")


if __name__ == "__main__":
    main()
