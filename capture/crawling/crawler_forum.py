import os, json
import requests
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs, unquote
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from .utils.utils import get_logger, slugify
from . import crawler_users
from time import sleep

logger = get_logger(__name__)

BASE_URL = "https://isis.tu-berlin.de"


def crawl(driver, forum_folder, course_metadata):
    summary_path = None
    existing_summary = {}
    summary_files = [f for f in os.listdir(forum_folder) if f.startswith("forum_summary__") and f.endswith(".json")]
    if summary_files:
        summary_files.sort(reverse=True)
        summary_path = os.path.join(forum_folder, summary_files[0])
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                for item in json.load(f):
                    existing_summary[item["forum_name"]] = {
                        "thread_count": item.get("thread_count"),
                        "reply_count": item.get("reply_count")
                    }
        except Exception as e:
            logger.warning(f"Failed to load previous forum summary: {e}")

    os.makedirs(forum_folder, exist_ok=True)
    course_url = driver.current_url
    course_id = parse_qs(urlparse(course_url).query).get("id", ["unknown"])[0]
    if course_id in ["unknown", "1"]:
        logger.error(f"Invalid course ID detected: {course_id}. Aborting crawl.")
        return []

    # Load user data for role mapping
    users_folder = os.path.join(os.path.dirname(forum_folder), "users")
    users_data = crawler_users.load_users_data(course_id, users_folder)
    if not users_data:
        logger.warning(f"No user data found for course {course_id}. User roles will be 'Unknown'.")

    forums = get_forums(driver, course_id)
    summary = []

    for i, forum in enumerate(forums, start=1):
        logger.info(f"Crawling forum: {forum['forum_name']}")

        threads = get_thread_links(driver, forum["forum_url"], forum["thread_count"])
        current_reply_count = sum(thread.get("reply_count", 0) for thread in threads)

        previous = existing_summary.get(forum["forum_name"])
        if previous:
            if (previous["thread_count"] == len(threads)) and (previous["reply_count"] == current_reply_count):
                logger.info(f"Skipping unchanged forum: {forum['forum_name']}")
                continue
        forum_data = []

        for thread in threads:
            logger.info(f"Thread: {thread['title']}")
            posts = parse_thread(driver, thread["url"], forum_folder, course_metadata, users_data)
            forum_data.append({
                "thread_title": thread["title"],
                "thread_url": thread["url"],
                "posts": posts
            })
    
        safe_name = slugify(forum["forum_name"])
        crawl_timestamp = datetime.now(timezone.utc).isoformat(timespec='minutes').replace(":", "-")
        forum_path = os.path.join(forum_folder, f"{course_id}_forum_{i:02d}_{safe_name}_{forum['forum_id']}__{crawl_timestamp}.json")
        with open(forum_path, "w", encoding="utf-8") as f:
            json.dump(forum_data, f, ensure_ascii=False, indent=2)

        summary.append({
            "forum_name": forum["forum_name"],
            "forum_id": forum["forum_id"],
            "thread_count": len(threads),
            "reply_count": sum(thread.get("reply_count", 0) for thread in threads),
            "forum_url": forum["forum_url"],
            "saved_to": forum_path
        })
    if summary:
        summary_ts = datetime.now(timezone.utc).isoformat(timespec='minutes').replace(":", "-")
        summary_path = os.path.join(forum_folder, f"forum_summary__{summary_ts}.json")
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        logger.info(f"Forum summary saved to {summary_path}")
    else:
        logger.info("No forums were updated. Skipped saving summary.")

    return summary


def get_forums(driver, course_id):
    forum_index_url = f"{BASE_URL}/mod/forum/index.php?id={course_id}"
    driver.get(forum_index_url)
    
    try:
        WebDriverWait(driver, 4).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.generaltable"))
        )
    except Exception as e:
        logger.warning("Forum table not found: %s", e)
        return []
    
    soup = BeautifulSoup(driver.page_source, "html.parser")

    forum_list = []
    table = soup.find("table", class_="generaltable")
    if not table:
        logger.warning("Forum table not found.")
        return []

    for row in table.select("tbody tr"):
        try:
            cells = row.find_all("td")
            link = cells[0].find("a")
            forum_url = urljoin(forum_index_url, link.get("href"))
            forum_id = parse_qs(urlparse(forum_url).query).get("f", [""])[0]
            thread_count = int(cells[2].text.strip())

            forum_list.append({
                "forum_name": link.text.strip(),
                "forum_url": forum_url,
                "forum_id": forum_id,
                "thread_count": thread_count
            })
        except Exception as e:
            logger.warning(f"Skipping forum row: {e}")
            continue
    return forum_list


def get_thread_links(driver, forum_url, thread_count):
    threads = {}
    total_pages = (thread_count - 1) // 100 + 1  # page counter

    for p in range(total_pages):
        paged_url = f"{forum_url}&p={p}"
        driver.get(paged_url)

        try:
            WebDriverWait(driver, 4).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='discuss.php?d=']"))
            )
        except Exception:
            logger.warning(f"forum page {p}")
            continue

        soup = BeautifulSoup(driver.page_source, "html.parser")
        for row in soup.select("tr.discussion"):
            try:
                discussion_id = row.get("data-discussionid")
                if not discussion_id:
                    continue

                link = row.select_one("a[href*='discuss.php?d=']")
                if not link:
                    continue

                title = link.get("title") or link.text.strip()
                thread_url = urljoin(BASE_URL, link.get("href"))

                # Find reply count
                reply_td = row.select_one("td.p-0.text-center.align-middle.fit-content.px-2 span")
                reply_count = int(reply_td.text.strip()) if reply_td and reply_td.text.strip().isdigit() else 0

                threads[discussion_id] = {
                    "title": title,
                    "url": thread_url,
                    "reply_count": reply_count
                }

            except Exception as e:
                logger.warning(f"Failed to extract thread data: {e}")
                continue


    return list(threads.values())


def parse_thread(driver, discussion_url, forum_folder, course_metadata, users_data=None):
    driver.get(discussion_url)

    try:
        WebDriverWait(driver, 4).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.forumpost"))
        )
    except Exception:
        logger.warning("No posts found in the discussion.")
        return []
    
    soup = BeautifulSoup(driver.page_source, "html.parser")
    posts = soup.find_all("div", class_="forumpost")
    post_data = []
    for post in posts:
        try:
            post_id = post.get("data-post-id", "unknown")
            thread_id = parse_qs(urlparse(discussion_url).query).get("d", ["unknown"])[0]
            header = post.find("header")
            subject = header.find("h3").text.strip() if header and header.find("h3") else "No subject"
            author = header.find("a").text.strip() if header and header.find("a") else "Unknown"
            
            # Get both role and user_id for the author
            user_info = crawler_users.get_user_info(author, users_data) if users_data else {"role": "Unknown", "user_id": "unknown"}
            author_role = user_info["role"]
            author_user_id = user_info["user_id"]
            
            post_datetime = header.find("time")["datetime"] if header and header.find("time") else "Unknown"
            
            content_div = post.find("div", class_="post-content-container")
            links = []
            for a in content_div.find_all("a"):
                text = a.get_text(strip=True)
                href = a.get("href")
                if href:
                    href = unquote(href)
                    links.append({
                        "text": text,
                        "url": href
                    })

            content = content_div.get_text(" ", strip=True)

            response_anchor = post.find("a", title=lambda t: t and "Ursprungsbeitrag" in t)
            if response_anchor:
                response_to = response_anchor["href"].split("#")[-1].removeprefix("p") 
                is_reply = True
                is_thread_root = False
            else:
                response_to = None
                is_reply = False
                is_thread_root = True

            # Find image attachments
            attachment_imgs = post.find_all("img")
            attachment_urls = []

            for img in attachment_imgs:
                src = img.get("src")
                if (
                    src
                    and "pluginfile.php" in src
                    and "user/icon" not in src  
                    and src not in attachment_urls
                ):
                    attachment_urls.append(src)

            has_attachments = bool(attachment_urls)

            local_attachments = download_attachments(
                attachment_urls,
                save_dir=os.path.join(forum_folder, "attachments"),
                post_id=post_id,
                driver=driver
            )

            post_data.append({
                "chunk_type": "forum_post",
                "content": content,
                "metadata": {
                    "course": course_metadata,
                    "post_id": post_id,
                    "thread_id": thread_id,
                    "subject": subject,
                    "author": author,
                    "author_role": author_role,
                    "author_user_id": author_user_id,
                    "post_datetime": post_datetime,
                    "permalink": f"{discussion_url}#p{post_id}",
                    "response_to": response_to,
                    "is_reply": is_reply,
                    "is_thread_root": is_thread_root,
                    "has_attachments": has_attachments,
                    "attachments": attachment_urls,
                    "local_attachments": local_attachments,
                    "links": links,
                    "crawl_datetime": datetime.now(timezone.utc).isoformat(timespec='minutes')
                }
            })

        except Exception as e:
            logger.warning(f"Error parsing post: {e}")
            continue
    return post_data

def download_attachments(attachment_urls, save_dir, post_id, driver=None):
    os.makedirs(save_dir, exist_ok=True)
    local_paths = []

    session = requests.Session()
    if driver:
        cookies = {cookie['name']: cookie['value'] for cookie in driver.get_cookies()}
        session.cookies.update(cookies)

    for i, url in enumerate(attachment_urls, start=1):
        parsed_url = urlparse(url)
        ext = os.path.splitext(parsed_url.path)[1]
        if ext.lower() not in ['.png', '.jpg', '.jpeg', '.gif']:
            ext = ''

        filename = f"attachment_{post_id}_{i}{ext or '.bin'}"
        filepath = os.path.join(save_dir, filename)

        if os.path.exists(filepath):
            local_paths.append(filepath)
            continue

        for attempt in range(3):
            try:
                response = session.get(url, stream=True, timeout=10)
                content_type = response.headers.get("Content-Type", "")

                if response.status_code == 200 and content_type.startswith("image/"):
                    if not ext:
                        if "png" in content_type:
                            ext = ".png"
                        elif "jpeg" in content_type or "jpg" in content_type:
                            ext = ".jpg"
                        elif "gif" in content_type:
                            ext = ".gif"
                        else:
                            ext = ".bin"
                        filename = f"attachment_{post_id}_{i}{ext}"
                        filepath = os.path.join(save_dir, filename)

                    with open(filepath, "wb") as f:
                        for chunk in response.iter_content(1024):
                            f.write(chunk)

                    local_paths.append(filepath)
                    logger.info(f"Downloaded image: {url} -> {filepath}")
                    break
                else:
                    logger.warning(f"Skipped non-image or failed download: {url} ({content_type})")
                    break
            except requests.exceptions.RequestException as e:
                if attempt == 2:
                    logger.warning(f"Failed after retries: {url} - {e}")
                else:
                    sleep(1)

    return local_paths


