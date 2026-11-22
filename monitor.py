import sys
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def make_session(domain):
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"})
    r = s.get(f"https://www.{domain}/", timeout=20)
    return s, r.status_code


def test():
    s, st = make_session("vinted.de")
    print("homepage status:", st, "| cookie names:", sorted(s.cookies.keys()))
    url = ("https://www.vinted.de/api/v2/catalog/items"
           "?search_text=balenciaga%20arena&order=newest_first&per_page=20")
    r = s.get(url, headers={"Accept": "application/json"}, timeout=20)
    print("api status:", r.status_code)
    if r.status_code != 200:
        print("body start:", r.text[:300].replace("\n", " "))
        return 1
    data = r.json()
    print("top-level keys:", list(data.keys()))
    items = data.get("items") or []
    print("items:", len(items))
    if items:
        it = items[0]
        print("first item keys:", list(it.keys()))
        for k, v in it.items():
            if isinstance(v, dict):
                print(f"  {k} -> {list(v.keys())}")
    return 0


if __name__ == "__main__":
    if "--test" in sys.argv:
        sys.exit(test())
    print("Bot mode not implemented yet (step 2). Use --test.")
