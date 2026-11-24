"""
Download all single-file document resources from a Moodle course.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional
from urllib.parse import unquote, urlparse

import requests
from bs4 import BeautifulSoup
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

from .utils.utils import get_course_id_from_url, get_logger
from .utils.file_kinds import kind_for

logger = get_logger(__name__)

ICON_SELECTORS = (
    "img[src*='/f/document'], img[src*='/f/text'], img[src*='/f/markup'], "
    "img[src*='/f/pdf'], img[src*='/folder/']"
)

SKIP_SCHEMES = ("#", "mailto:", "javascript:")

USER_AGENT_HEADER = {"User-Agent": "Mozilla/5.0 (compatible; moodle-crawler)"}


def _safe_filename(url: str) -> Optional[str]:
    if url.startswith(SKIP_SCHEMES):
        return None
    name = Path(unquote(urlparse(url).path)).name
    if any(name.endswith(ext) for ext in (".webloc", ".url", ".desktop", ".lnk", ".link")):
        return None
    if "." not in name:
        name += ".bin"
    return name

def _download(sess: requests.Session, url: str, dst: Path) -> bool:
    try:
        with sess.get(url, stream=True, timeout=25, allow_redirects=True) as r:
            r.raise_for_status()
            dst.parent.mkdir(parents=True, exist_ok=True)
            with open(dst, "wb") as fh:
                for chunk in r.iter_content(32_768):
                    fh.write(chunk)
        return True
    except requests.RequestException as exc:
        logger.warning("Download failed for %s - %s", url, exc)
        return False

def _dst_dir(metadata_path: str, suffix: str) -> Path:
    return Path(metadata_path).with_name(f"files_{suffix.lower().lstrip('.') or 'other'}")

def _meta_or_js_redirect(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.find("meta", attrs={"http-equiv": re.compile("refresh", re.I)})
    if meta and (c := meta.get("content")) and "url=" in c.lower():
        return c.split("url=", 1)[1].strip(" '\"")
    m = re.search(r"window\.location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]", html)
    return m.group(1) if m else None

def _scrape_doc_links(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    hrefs = [a["href"] for a in soup.find_all("a", href=True)]
    redir = _meta_or_js_redirect(html)
    if redir:
        hrefs.append(redir)
    return [
        h for h in hrefs
        if h.startswith(("http://", "https://"))
        and kind_for(_safe_filename(h) or "") == "doc"
    ]


def crawl(driver, metadata_path: str) -> List[dict]:
    cid       = get_course_id_from_url(driver.current_url)
    grids     = [g for g in driver.find_elements(By.CSS_SELECTOR, ".activity-grid")
                 if g.find_elements(By.CSS_SELECTOR, ICON_SELECTORS)]
    logger.info("Found %d document grids", len(grids))

    sess = requests.Session()
    for c in driver.get_cookies():
        sess.cookies.set(c["name"], c["value"])
    sess.headers.update(USER_AGENT_HEADER)

    out: List[dict] = []
    seen_view: set[str] = set()

    for idx, grid in enumerate(grids, 1):
        try:
            try:
                a = grid.find_element(
                    By.XPATH,
                    ".//a[contains(@href,'/mod/resource/view.php') or "
                    "contains(@href,'/mod/folder/view.php')  or "
                    "contains(@href,'/mod/url/view.php')]"
                )
                view_url = a.get_attribute("href")
            except NoSuchElementException:
                view_url = None

            title = (a.text if view_url else "").strip() or f"{cid}_{idx:03d}"

            html_pages: list[str] = []
            direct_docs:  list[tuple[str, str]] = []

            def _handle_response(resp, referer):
                ct  = resp.headers.get("Content-Type", "")
                url = resp.url
                if not ct.startswith("text/html") and kind_for(url) == "doc":
                    direct_docs.append((url, referer))
                else:
                    html_pages.append(resp.text)

            if view_url and view_url not in seen_view:
                seen_view.add(view_url)
                r = sess.get(view_url, timeout=20, allow_redirects=True)
                r.raise_for_status()
                _handle_response(r, view_url)

                if "/mod/url/view.php" in view_url and "redirect=1" not in view_url:
                    redir_url = view_url + ("&" if "?" in view_url else "?") + "redirect=1"
                    try:
                        r2 = sess.get(redir_url, timeout=20, allow_redirects=True)
                        r2.raise_for_status()
                        _handle_response(r2, view_url)
                    except requests.RequestException:
                        pass
            else:
                html_pages.append(grid.get_attribute("innerHTML"))

            for page in html_pages:
                for link in _scrape_doc_links(page):
                    direct_docs.append((link, view_url or driver.current_url))

            saved = set()
            for subidx, (dl_url, referer) in enumerate(direct_docs, 1):
                if dl_url in saved:
                    continue
                fname = _safe_filename(dl_url)
                if not fname or kind_for(fname) != "doc":
                    continue
                dst = _dst_dir(metadata_path, Path(fname).suffix) / \
                      f"{cid}_{idx:03d}_{subidx:02d}_document{Path(fname).suffix}"
                if _download(sess, dl_url, dst):
                    logger.info("Saved %s", dst)
                    out.append({
                        "title": title if len(direct_docs) == 1 else Path(fname).name,
                        "moodle_url": referer,
                        "download_url": dl_url,
                        "saved_filename": dst.name,
                        "saved_path": str(dst),
                    })
                    saved.add(dl_url)

            if not direct_docs:
                logger.debug("skip - No docs in grid '%s'", title)

        except requests.RequestException as exc:
            logger.warning("HTTP error - %s", exc)
            continue

    Path(metadata_path).parent.mkdir(parents=True, exist_ok=True)
    with open(metadata_path, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)

    logger.info("Saved document meta to %s", metadata_path)
    return out
