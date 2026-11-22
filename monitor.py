import html
import json
import os
import random
import re
import sys
import time

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
MARKER = '"items":{"items":'
STATE_FILE = "state.json"
CONFIG_FILE = "config.json"
MAX_SEEN = 2000      # IDs kept per search
MAX_SEND = 10        # max alerts per search per run (rest marked seen)
FAIL_WARN_AT = 3     # consecutive failed runs before one Telegram warning


class FetchError(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


def describe(ex):
    # Never str() a requests exception: it can contain URLs (Telegram URL holds the token).
    return str(ex) if isinstance(ex, FetchError) else type(ex).__name__


def load_json(path, default):
    try:
        with open(path, encoding="utf-8-sig") as f:  # tolerate BOM (Windows editors)
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def make_session(domain):
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"})
    r = s.get(f"https://www.{domain}/", timeout=20)
    if r.status_code != 200:
        raise FetchError(f"homepage HTTP {r.status_code}", r.status_code)
    return s


def extract_items(page):
    """Vinted's JSON API returns 404; the catalog page embeds items in its Next.js stream."""
    for m in PUSH_RE.finditer(page):
        chunk = json.loads(m.group(1))
        i = chunk.find(MARKER)
        if i >= 0:
            arr, _ = json.JSONDecoder().raw_decode(chunk, i + len(MARKER))
            return arr
    return None


def fetch(session, search, domain):
    params = [("search_text", search["search_text"]), ("order", "newest_first")]
    if search.get("price_to") is not None:
        params.append(("price_to", search["price_to"]))
    for sid in search.get("size_ids") or []:
        params.append(("size_ids[]", sid))
    r = session.get(f"https://www.{domain}/catalog", params=params, timeout=20)
    if r.status_code != 200:
        raise FetchError(f"catalog HTTP {r.status_code}", r.status_code)
    items = extract_items(r.text)
    if items is None:
        raise FetchError("no items found in page (layout changed?)")
    return [x for x in items if isinstance(x, dict) and x.get("id")]


def price_ok(item, price_to):
    if price_to is None:
        return True
    try:
        return float(item["productItem"]["price"]["amount"]) <= float(price_to)
    except (KeyError, TypeError, ValueError):
        return True


def tg(method, data):
    url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{method}"
    try:
        r = requests.post(url, data=data, timeout=20)
        if r.status_code == 429:
            wait = r.json().get("parameters", {}).get("retry_after", 5)
            time.sleep(min(int(wait), 30))
            r = requests.post(url, data=data, timeout=20)
        if not r.ok:
            print(f"  telegram {method} failed: HTTP {r.status_code}")
        return r.ok
    except (requests.RequestException, ValueError) as ex:
        print(f"  telegram {method} error: {describe(ex)}")
        return False


def send_item(item, domain, search_name):
    p = item.get("productItem") or {}
    box = p.get("itemBox") or {}
    price = p.get("price") or {}
    e = lambda v: html.escape(str(v or "").strip())
    link = f"https://www.{domain}{p.get('url') or '/items/' + str(item['id'])}"
    lines = [
        f"<b>{e(p.get('title'))[:300]}</b>",
        f"💶 {e(price.get('amount'))} {e(price.get('currencyCode'))}",
        f"📏 {e(box.get('secondLine')) or '—'}",
        f"🏷 {e(box.get('firstLine')) or '—'}",
        f"🔎 {e(search_name)}",
        f'<a href="{html.escape(link, quote=True)}">Open on Vinted</a>',
    ]
    caption = "\n".join(lines)
    chat = os.environ["TELEGRAM_CHAT_ID"]
    photos = p.get("photos") or []
    photo = (photos[0].get("url") if photos else None) or p.get("thumbnailUrl")
    if photo and tg("sendPhoto", {"chat_id": chat, "photo": photo,
                                  "caption": caption, "parse_mode": "HTML"}):
        return
    tg("sendMessage", {"chat_id": chat, "text": caption, "parse_mode": "HTML"})


def run():
    if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"):
        print("Missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID secrets.")
        return 1
    cfg = load_json(CONFIG_FILE, None)
    if not cfg or not cfg.get("searches"):
        print("config.json missing or has no searches.")
        return 1

    state = load_json(STATE_FILE, {})
    seen = state.setdefault("seen", {})
    failed = False
    sessions = {}

    for n, search in enumerate(cfg["searches"]):
        name = search["name"]
        domain = search.get("domain") or "vinted.de"
        if n:
            time.sleep(random.uniform(1, 2))
        try:
            if domain not in sessions:
                sessions[domain] = make_session(domain)
            items = fetch(sessions[domain], search, domain)
        except (requests.RequestException, FetchError, ValueError) as ex:
            failed = True
            print(f"[{name}] fetch failed: {describe(ex)}")
            if getattr(ex, "status", None) in (403, 429):
                print("Blocked/rate-limited; skipping remaining searches this run.")
                break
            continue

        ids = [str(x["id"]) for x in items]
        if name not in seen:
            seen[name] = ids[:MAX_SEEN]
            print(f"[{name}] first run: seeded {len(ids)} IDs, no alerts")
            continue

        known = set(seen[name])
        new = [x for x in items if str(x["id"]) not in known]
        to_send = [x for x in new if price_ok(x, search.get("price_to"))][:MAX_SEND]
        print(f"[{name}] {len(items)} fetched, {len(new)} new, {len(to_send)} alerts")
        for item in reversed(to_send):  # oldest first, newest ends up at the bottom
            send_item(item, domain, name)
            time.sleep(1)
        seen[name] = ([str(x["id"]) for x in new] + seen[name])[:MAX_SEEN]

    streak = state.get("fail_streak", 0) + 1 if failed else 0
    state["fail_streak"] = streak
    if streak == FAIL_WARN_AT:
        tg("sendMessage", {"chat_id": os.environ["TELEGRAM_CHAT_ID"],
                           "text": f"⚠️ Vinted monitor: {streak} runs in a row failed. Check the Actions logs."})
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)
    return 0


def test():
    s = make_session("vinted.de")
    print("homepage ok")
    r = s.get("https://www.vinted.de/catalog?search_text=balenciaga%20arena&order=newest_first",
              timeout=20)
    print("catalog status:", r.status_code)
    items = extract_items(r.text) if r.status_code == 200 else None
    if not items:
        print("no embedded items found")
        return 1
    print("items:", len(items), "| first ids:", [x.get("id") for x in items[:5]])
    print("productItem keys:", list(items[0].get("productItem", {}).keys()))
    return 0


if __name__ == "__main__":
    sys.exit(test() if "--test" in sys.argv else run())
