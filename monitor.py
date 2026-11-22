import json
import re
import sys
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
MARKER = '"items":{"items":'


def make_session(domain):
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"})
    r = s.get(f"https://www.{domain}/", timeout=20)
    return s, r.status_code


def extract_items(html):
    """Vinted's JSON API now returns 404; the catalog page embeds items in its Next.js stream."""
    for m in PUSH_RE.finditer(html):
        chunk = json.loads(m.group(1))
        i = chunk.find(MARKER)
        if i >= 0:
            arr, _ = json.JSONDecoder().raw_decode(chunk, i + len(MARKER))
            return arr
    return None


def test():
    s, st = make_session("vinted.de")
    print("homepage status:", st)
    r = s.get("https://www.vinted.de/catalog?search_text=balenciaga%20arena&order=newest_first",
              timeout=20)
    print("catalog status:", r.status_code)
    if r.status_code != 200:
        print("body start:", r.text[:300].replace("\n", " "))
        return 1
    items = extract_items(r.text)
    if not items:
        print("no embedded items found")
        return 1
    print("items:", len(items), "| first ids:", [x.get("id") for x in items[:5]])
    print("item keys:", list(items[0].keys()))
    p = items[0].get("productItem", {})
    print("productItem keys:", list(p.keys()))
    for k, v in p.items():
        if not isinstance(v, (dict, list)):
            print(f"  {k} = {str(v)[:80]}")
        elif isinstance(v, dict):
            print(f"  {k} -> {json.dumps(v, ensure_ascii=False)[:120]}")
    return 0


if __name__ == "__main__":
    if "--test" in sys.argv:
        sys.exit(test())
    print("Bot mode not implemented yet (step 2). Use --test.")
