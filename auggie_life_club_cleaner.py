"""
roster_manager.py

1. opens a browser window w/ ur saved profile, u do SSO + Duo if asked, it continues by itself
2. reads the Auggie Life (Campus Labs Engage) roster pages in the browser
3. copies the browser's logged-in cookiez into a plain requests session, closes the browser
4. 1 exact-match directory POST per member (first + last), straight to the server
5. prints + saves whoever has no match
6. after you type yes, ends Auggie Life membership for each of them (same buttons you'd click), then re-checks the roster

setup (1 time):
    pip install --upgrade playwright beautifulsoup4 requests
    python -m playwright install chromium
"""

import csv
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

ROSTER_BASE = "https://augsburg.campuslabs.com/engage/actioncenter/organization/acc/roster"
ROSTER_URL = ROSTER_BASE + "?Direction=Ascending&Page=1"
DIRECTORY_URL = "https://augnet.augsburg.edu/protected/directory/search.pl"
NO_RESULTS_TEXT = "No entries matching your criteria were found"

PROFILE_DIR = Path.home() / ".roster_manager_profile"  # saved login, treat like a password
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_CSV = SCRIPT_DIR / "unmatched_members.csv"
ERROR_HTML = SCRIPT_DIR / "directory_error.html"  # only written if the session dies mid-run

# delay settingz (>_<)
LOGIN_TIMEOUT_SECONDS = 300  # how long to wait for u to finish SSO + Duo
MAX_WORKERS = 2              # parallel directory requestz. low on purpose
DELAY_SECONDS = 0.25         # pause per request, per worker


class SessionExpired(Exception):
    pass


# ---------------------------------------------------------------------------
# BROWSER + LOGIN  
# ---------------------------------------------------------------------------

def safe_goto(page, url):
    #page.goto that doesnt crash when SSO / Duo redirects interrupt it.
    try:
        page.goto(url, wait_until="domcontentloaded")
    except PlaywrightError as e:
        if "interrupted by another navigation" not in str(e):
            raise
        try:
            page.wait_for_load_state("domcontentloaded")
        except PlaywrightError:
            pass


def page_ready(page, selector):
    try:
        return page.locator(selector).count() > 0
    except PlaywrightError:
        return False  # page was mid-navigation


def ensure_logged_in(page, url, selector, label):
    #Go to url. If SSO showz up, wait while u finish SSO + Duo, then continue.
    safe_goto(page, url)
    if page_ready(page, selector):
        print(f"[{label}] already logged in (˶˃ ᵕ ˂˶)")
        return

    print(f"[{label}] finish SSO + Duo in the browser window, the script continues by itself...")
    target_host = urlparse(url).netloc
    deadline = time.time() + LOGIN_TIMEOUT_SECONDS
    stable_polls = 0

    while time.time() < deadline:
        time.sleep(1)
        if page_ready(page, selector):
            print(f"[{label}] logged in (˶˃ ᵕ ˂˶)")
            return

        current = urlparse(page.url)
        in_redirect = any(w in current.path.lower() for w in ("redirect", "login", "sso", "saml"))
        # landed on the right site but the wrong page 4 a few secondz -> nudge the tab.
        # never navigate while SSO / Duo is mid-flow
        if current.netloc == target_host and not in_redirect:
            stable_polls += 1
            if stable_polls >= 3:
                safe_goto(page, url)
                stable_polls = 0
        else:
            stable_polls = 0

    raise RuntimeError(f"[{label}] login timed out, run the script again")


# ---------------------------------------------------------------------------
# ROSTER  
# ---------------------------------------------------------------------------

def parse_roster_page(html):
    #Return (list of member dicts, URL of next page or None)
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="svgGrid")
    if table is None:
        raise RuntimeError("No roster table found, Engage login probably expired")

    members = []
    for row in table.select("tbody tr"):
        first_cell = row.find("td", attrs={"data-label": "First Name"})
        last_cell = row.find("td", attrs={"data-label": "Last Name"})
        if first_cell is None or last_cell is None:
            continue
        positions = [s.get_text(strip=True) for s in row.find_all("span", class_="className")]
        members.append({
            "first": first_cell.get_text(strip=True),
            "last": last_cell.get_text(strip=True),
            "positions": "; ".join(positions),
        })

    next_url = None
    pagination = soup.find("span", class_="paginationRight")
    if pagination is not None:
        for link in pagination.find_all("a"):
            if link.get_text(strip=True).lower() == "next":
                next_url = link["href"]
                if next_url.startswith("/"):
                    next_url = "https://augsburg.campuslabs.com" + next_url
                break

    return members, next_url


def load_roster(page):
    members = []
    seen_urls = set()
    url = ROSTER_URL
    while url and url not in seen_urls:
        seen_urls.add(url)
        safe_goto(page, url)
        page.locator("table.svgGrid").wait_for(state="attached", timeout=30000)
        page_members, url = parse_roster_page(page.content())
        members.extend(page_members)
        print(f"  roster page {len(seen_urls)}: {len(page_members)} members")
    return members


# ---------------------------------------------------------------------------
# DIRECTORY  (direct requests, no browser)  
# ---------------------------------------------------------------------------

_thread_local = threading.local()


def get_session(jar, user_agent):
    #1 requests session per worker thread
    if not hasattr(_thread_local, "session"):
        s = requests.Session()
        s.cookies.update(jar)
        s.headers["User-Agent"] = user_agent
        s.headers["Referer"] = DIRECTORY_URL
        _thread_local.session = s
    return _thread_local.session


def check_member(member, jar, user_agent):
    #1 exact-match POST w/ first + last on the same entry. Any result = match.
    session = get_session(jar, user_agent)
    form = {
        "first": member["first"], "last": member["last"], "dept": "", "user": "",
        "room": "", "title": "", "phone": "", "box": "",
        "action": "search", "exact": "yes",
    }
    r = session.post(DIRECTORY_URL, data=form, timeout=30)

    # an expired session redirects 2 SSO / external_redirect.html, check before status code
    final = urlparse(r.url)
    if final.netloc != "augnet.augsburg.edu" or "/protected/directory/" not in final.path:
        ERROR_HTML.write_text(r.text, encoding="utf-8")
        raise SessionExpired(f"got redirected 2 {r.url}")

    r.raise_for_status()
    time.sleep(DELAY_SECONDS)
    return member, NO_RESULTS_TEXT not in r.text


# ---------------------------------------------------------------------------
# END MEMBERSHIPS  (same buttons as the roster page)  
# ---------------------------------------------------------------------------

def find_row(page, first, last):
    #The roster row whose First Name + Last Name cellz equal first + last exactly.
    rows = page.locator("table.svgGrid tbody tr")
    for i in range(rows.count()):
        row = rows.nth(i)
        row_first = row.locator('td[data-label="First Name"]').inner_text().strip()
        row_last = row.locator('td[data-label="Last Name"]').inner_text().strip()
        if row_first == first and row_last == last:
            return row
    return None


def end_membership(page, member):
    #Search the roster 4 the member, tick their box, End Membership, confirm. True if submitted.
    safe_goto(page, f"{ROSTER_BASE}?SearchValue={quote(member['last'])}")
    try:
        page.locator("table.svgGrid").wait_for(state="attached", timeout=15000)
    except PlaywrightError:
        return False  # no resultz table at all

    row = find_row(page, member["first"], member["last"])
    if row is None:
        return False

    row.locator("input.endMemberships-checkbox").check()
    page.locator("#endMemberships").click()
    submit = page.locator("#endMemberships-submit")
    submit.wait_for(state="visible", timeout=10000)
    with page.expect_response(
        lambda r: r.url.lower().endswith("/roster/endmembership") and r.request.method == "POST",
        timeout=30000,
    ):
        submit.click()
    page.wait_for_load_state("domcontentloaded")
    return True


def end_memberships(page, to_end, me):
    to_end = [m for m in to_end if (m["first"].lower(), m["last"].lower()) != me]
    if not to_end:
        print("\nNobody to remove !")
        return

    print(f"\nAbout to END Auggie Life membership for these !!! {len(to_end)}:")
    for m in to_end:
        extra = f"  ({m['positions']})" if m["positions"] else ""
        print(f"  {m['first']} {m['last']}{extra}")
    if input("Type yes to continue: ").strip().lower() != "yes":
        print("Cancelled, nobody removed")
        return

    for m in to_end:
        if end_membership(page, m):
            print(f"  ended: {m['first']} {m['last']}")
        else:
            print(f"  couldnt find row 4: {m['first']} {m['last']} (skipped)")

    # verify: reload the whole roster and make sure they're actually gone
    print("\nRe-checking roster...")
    remaining = {(m["first"], m["last"]) for m in load_roster(page)}
    still_there = [m for m in to_end if (m["first"], m["last"]) in remaining]
    if still_there:
        print(f"{len(still_there)} still on the roster, end these by hand:")
        for m in still_there:
            print(f"  {m['first']} {m['last']}")
    else:
        print(f"All {len(to_end)} removed, roster now has {len(remaining)} members :D")


# ---------------------------------------------------------------------------
# MAIN  
# ---------------------------------------------------------------------------

def main():
    start = time.time()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE_DIR), headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            ensure_logged_in(page, ROSTER_URL, "table.svgGrid", "Auggie Life")
            ensure_logged_in(page, DIRECTORY_URL, 'form[name="baseform"]', "AugNet directory")

            print("\nReading roster...")
            members = load_roster(page)
            print(f"Loaded {len(members)} roster memberz\n")

            # whoever is logged in (u), so the script can never remove u
            me = page.evaluate(
                "typeof currentUser !== 'undefined' && currentUser"
                " ? [currentUser.firstName, currentUser.lastName] : null"
            )
            me = (me[0].lower(), me[1].lower()) if me else None

            user_agent = page.evaluate("navigator.userAgent")
            jar = requests.cookies.RequestsCookieJar()
            for c in context.cookies():
                jar.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))

            print(f"Checking directory ({MAX_WORKERS} at a time)...")
            unmatched = []
            try:
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                    results = pool.map(lambda m: check_member(m, jar, user_agent), members)
                    for i, (m, matched) in enumerate(results, start=1):
                        print(f"[{i}/{len(members)}] {m['first']} {m['last']}: {'match' if matched else 'NO MATCH'}")
                        if not matched:
                            unmatched.append(m)
            except SessionExpired as e:
                print(f"\nAugNet session expired mid-run ({e}). Run it again. Response saved to {ERROR_HTML}")
                return

            print(f"\n{len(unmatched)} of {len(members)} roster members had no directory match")
            with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["first", "last", "positions"])
                writer.writeheader()
                writer.writerows(unmatched)
            print(f"Saved 2 {OUTPUT_CSV}")

            end_memberships(page, unmatched, me)
        finally:
            context.close()

    print(f"\nDone in {time.time() - start:.1f} sec :P")


if __name__ == "__main__":
    main()
