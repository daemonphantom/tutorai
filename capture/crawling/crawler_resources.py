"""
Downloads source-code single files and archive files from a Moodle course.
"""
from __future__ import annotations

import json
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
    "img[src*='/f/archive'], img[src*='/f/sourcecode'], img[src*='/folder/']"
)
SUB_SINGLE = "files_single"
SUB_ZIP    = "files_zip"


def _safe_name(url: str) -> Optional[str]:
    name = Path(unquote(urlparse(url).path)).name
    if any(name.endswith(ext) for ext in (".webloc", ".url", ".desktop", ".lnk", ".link")):
        return None
    return name or None


def _download(sess: requests.Session, url: str, dst: Path) -> bool:
    try:
        with sess.get(url, stream=True, timeout=25, allow_redirects=True) as resp:
            resp.raise_for_status()
            dst.parent.mkdir(parents=True, exist_ok=True)
            with open(dst, "wb") as fh:
                for chunk in resp.iter_content(32_768):
                    fh.write(chunk)
        return True
    except requests.RequestException as exc:
        logger.warning("Download failed for %s - %s", url, exc)
        return False


def _nearest_folder(anchor) -> str:
    span = anchor.find_previous(
        lambda t: t.name == "span" and "fp-filename" in t.get("class", []) and not t.find("a")
    )
    return span.get_text(strip=True) if span else ""


def crawl(driver, metadata_path: str) -> List[dict]:
    cid = get_course_id_from_url(driver.current_url)

    grids = [g for g in driver.find_elements(By.CSS_SELECTOR, ".activity-grid")
             if g.find_elements(By.CSS_SELECTOR, ICON_SELECTORS)]
    logger.info("Found %d archive/source grids", len(grids))

    sess = requests.Session()
    for c in driver.get_cookies():
        sess.cookies.set(c["name"], c["value"])
    sess.headers.update({"User-Agent": driver.execute_script("return navigator.userAgent;")})

    out: List[dict] = []
    seen: set[str] = set()

    for idx, grid in enumerate(grids, 1):
        try:
            try:
                a = grid.find_element(By.XPATH,
                    ".//a[contains(@href,'/mod/resource/view.php') or contains(@href,'/mod/folder/view.php')]"
                )
                view_url = a.get_attribute("href")
            except NoSuchElementException:
                view_url = None

            if view_url and view_url not in seen:
                seen.add(view_url)
                res = sess.get(view_url, stream=True, timeout=20, allow_redirects=True)
                res.raise_for_status()

                # direct binary (HTTP 302 to pluginfile)
                if "pluginfile.php" in res.url and not res.headers.get("Content-Type", "").startswith("text/html"):
                    fname = _safe_name(res.url)
                    if not fname:
                        continue
                    kind = kind_for(fname)
                    if kind == "doc":
                        continue
                    subfolder = SUB_ZIP if kind == "archive" else SUB_SINGLE
                    dst = Path(metadata_path).with_name(subfolder) / f"{cid}_{idx:03d}_{fname}"
                    if _download(sess, res.url, dst):
                        logger.info("Saved %s", dst)
                        out.append({
                            "title": fname,
                            "folder": "",
                            "moodle_url": view_url,
                            "download_url": res.url,
                            "saved_filename": dst.name,
                            "saved_path": str(dst),
                        })
                    continue  # done with this grid

                html = res.text  # folder view or stub page
            else:
                html = grid.get_attribute("innerHTML")  # grid itself contains links
                view_url = driver.current_url

            soup = BeautifulSoup(html, "html.parser")
            anchors = soup.select("a[href*='pluginfile.php']")
            for subidx, link in enumerate(anchors, 1):
                dl_url = link["href"]
                fname = _safe_name(dl_url)
                if not fname:
                    continue
                kind = kind_for(fname)
                if kind == "doc":
                    continue
                subfolder = SUB_ZIP if kind == "archive" else SUB_SINGLE
                dst = Path(metadata_path).with_name(subfolder) / f"{cid}_{idx:03d}_{subidx:02d}_{fname}"
                if _download(sess, dl_url, dst):
                    logger.info("Saved %s", dst)
                    out.append({
                        "title": link.get_text(strip=True) or fname,
                        "folder": _nearest_folder(link),
                        "moodle_url": view_url,
                        "download_url": dl_url,
                        "saved_filename": dst.name,
                        "saved_path": str(dst),
                    })

        except requests.RequestException as exc:
            logger.warning("HTTP error %s", exc)
            continue

    # write metadata
    Path(metadata_path).parent.mkdir(parents=True, exist_ok=True)
    with open(metadata_path, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)

    return out
