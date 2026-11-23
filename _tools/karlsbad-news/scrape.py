#!/usr/bin/env python3
"""Holt alle Artikel des Karlsbader Mitteilungsblatts und schreibt karlsbad-news/articles.json.

Quelle:
  - https://www.karlsbad.de/news?action=view_everything        (alle Teaser, nach Rubrik gruppiert)
  - https://www.karlsbad.de/website/de/gemeinde-buerger/aktuelles/mitteilungsblatt  (Rubrikenbaum)
Bestehende Artikel werden per ID zusammengeführt (Archiv). Thumbnails werden lokal gespeichert.
Nur Python-Standardbibliothek.
"""
import html, json, os, re, sys, time, urllib.request
from html.parser import HTMLParser
from datetime import date

BASE = "https://www.karlsbad.de"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_DIR = os.path.join(ROOT, "karlsbad-news")
OUT = os.path.join(OUT_DIR, "articles.json")
IMG_DIR = os.path.join(OUT_DIR, "img")
UA = "kelsaka-news-index/1.0 (+https://kelsaka.github.io; 1 Abruf pro Tag)"

BEREICH = {
    "Redaktionelle Berichte": "rathaus", "Notdienste und wichtige Telefonnummern": "rathaus",
    "Polizeiinformationen": "rathaus", "Landkreisinformationen": "rathaus", "Weitere Informationen": "rathaus",
    "Schaufenster Startseite": "rathaus",
    "Jugendbeteiligung": "buerger", "Seniorenbeirat Karlsbad": "buerger", "Gemeindebücherei": "buerger",
    "Sprechstunden Jugend Familie Soziales": "buerger", "Miteinander Zukunft gestalten - Karlsbad 2030": "buerger",
    "Agendaarbeit": "buerger", "Bürgerinitiativen": "buerger", "Volkshochschule": "buerger",
    "Wirtschafts News": "wirtschaft",
    "Freiwillige Feuerwehr Karlsbad": "feuerwehr", "Rettungswesen": "feuerwehr",
    "Parteien und Wählervereinigungen": "politik", "Aus den Fraktionen und Gruppierungen des Gemeinderates": "politik",
    "Schulen": "bildung", "Kindergärten": "bildung",
    "Kirchen und religiöse Vereinigungen": "kirchen",
}
ORTE = ["Auerbach", "Ittersbach", "Langensteinbach", "Mutschelbach", "Spielberg"]


def get(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


class TreeParser(HTMLParser):
    """Liest den Rubrikenbaum (verschachtelte <ul>) -> Liste von {name, id, path}."""
    def __init__(self):
        super().__init__()
        self.stack, self.items, self.cat, self.last, self.depth = [], [], None, None, 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "ul":
            self.depth += 1
            if self.depth > 1:
                self.stack.append(self.last)
        elif tag == "a" and "category_id=" in (a.get("href") or ""):
            self.cat = int(re.search(r"category_id=(\d+)", a["href"]).group(1))

    def handle_endtag(self, tag):
        if tag == "ul":
            if self.depth > 1 and self.stack:
                self.stack.pop()
            self.depth -= 1
            if self.depth == 0:
                raise StopIteration

    def handle_data(self, d):
        d = d.strip()
        if not d or self.depth == 0:
            return
        self.last = d
        self.items.append({"name": d, "id": self.cat, "path": [p for p in self.stack if p]})
        self.cat = None


def parse_tree(page):
    i = page.find("Bitte w")
    if i < 0:
        raise RuntimeError("Rubrikenbaum nicht gefunden")
    rest = page[i:]
    j = rest.find("<ul")
    p = TreeParser()
    try:
        p.feed(rest[j:])
    except StopIteration:
        pass
    return p.items


def parse_articles(page, tree):
    linked = [x for x in tree if x["id"]]
    blocks = page.split('<div class="news-cat-container">')[1:]
    arts, bi = [], 0
    for b in blocks:
        m = re.search(r'news-cat-header">(.*?)</h1>', b, re.S)
        if not m:
            continue
        head = html.unescape(m.group(1)).strip()
        k = bi
        while k < len(linked) and html.unescape(linked[k]["name"]).strip() != head:
            k += 1
        if k < len(linked):
            node, bi = linked[k], k + 1
        else:  # nicht im Baum (z. B. Portal-Boxen)
            node = {"id": None, "path": [], "name": head}
        path = [html.unescape(p) for p in node["path"]]
        group = path[0] if path else head
        if group.startswith("Portal-Boxen"):
            continue
        for a in re.finditer(
            r'article_id=(\d+)".*?\| ([\d.]+)</p><h2>(.*?)</h2>(?:<img[^>]*src="([^"]+)")?.*?<p class="content">(.*?)</p>',
            b, re.S):
            aid, d, t, img, c = a.groups()
            bereich = BEREICH.get(group) or ("sport" if len(path) > 1 and path[1] == "Sport" else "vereine")
            if bereich == "sport":
                sub = path[2] if len(path) > 2 else head
            else:
                sub = path[1] if len(path) > 1 else head
            title = html.unescape(re.sub("<[^>]+>", "", t)).strip()
            teaser = html.unescape(re.sub("<[^>]+>", "", c)).strip()
            txt = " ".join(path + [head, title, teaser])
            arts.append({
                "id": int(aid), "d": f"{d[6:]}-{d[3:5]}-{d[:2]}", "t": title, "x": teaser,
                "src": html.unescape(img) if img else None, "c": head, "cid": node["id"], "p": path,
                "b": bereich, "s": sub, "o": [o for o in ORTE if o in txt],
            })
    return arts


def fetch_image(src):
    name = os.path.basename(src)
    if not re.fullmatch(r"[\w.\-]+\.(jpe?g|png|gif|webp)", name, re.I):
        return None
    dest = os.path.join(IMG_DIR, name)
    if not os.path.exists(dest):
        try:
            data = get(BASE + src, binary=True)
        except Exception as e:  # Bild fehlt: Platzhalter im Frontend
            print("Bild fehlgeschlagen:", src, e, file=sys.stderr)
            return None
        with open(dest, "wb") as f:
            f.write(data)
        time.sleep(0.3)
    return "img/" + name


def main():
    os.makedirs(IMG_DIR, exist_ok=True)
    tree = parse_tree(get(BASE + "/website/de/gemeinde-buerger/aktuelles/mitteilungsblatt"))
    fresh = parse_articles(get(BASE + "/news?action=view_everything"), tree)

    old = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            old = {a["id"]: a for a in json.load(f)}
    if old and len(fresh) < 0.5 * sum(1 for a in old.values() if a.get("live")):
        sys.exit(f"Nur {len(fresh)} Artikel gefunden – Markup geändert? Daten bleiben unverändert.")

    today = date.today().isoformat()
    for a in old.values():
        a["live"] = False
    for a in fresh:
        prev = old.get(a["id"], {})
        src = a.pop("src")
        a["i"] = fetch_image(src) if src else None
        a["first"] = prev.get("first", today)
        a["live"] = True
        old[a["id"]] = a

    items = sorted(old.values(), key=lambda r: (r["d"], r["id"]), reverse=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("[\n" + ",\n".join(json.dumps(r, ensure_ascii=False) for r in items) + "\n]\n")
    print(f"{len(fresh)} aktuell, {len(items)} gesamt, Bilder: {len(os.listdir(IMG_DIR))}")


if __name__ == "__main__":
    main()
