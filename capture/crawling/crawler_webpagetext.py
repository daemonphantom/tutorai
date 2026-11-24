import os
import json
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from .utils.utils import clean_course_text, download_image, extract_colors_from_soup, extract_table, get_logger, slugify, transform_course_data

logger = get_logger(__name__)
BASE_URL = "https://isis.tu-berlin.de/"


def crawl_mainpage_sections(driver, output_dir):
    logger.info("Crawling course sections from mainpage")
    
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.CLASS_NAME, "course-section"))
    )

    sections = {}

    for section in driver.find_elements(By.CSS_SELECTOR, "li.section.course-section.main.clearfix"):
        try:
            section_name = section.get_attribute("data-sectionname") or "Untitled"

            content_div = section.find_element(By.CSS_SELECTOR, "div.content.course-content-item-content")
            soup = BeautifulSoup(content_div.get_attribute("outerHTML"), "html.parser")

            for activity in soup.select("li.activity"):
                modtype = activity.get("class", [])
                if any(cls.startswith("modtype_") and cls not in ["modtype_label"] for cls in modtype):
                    activity.decompose()

            extracted_links = []
            table_data = []

            colors = extract_colors_from_soup(soup)

            for table in soup.find_all("table"):
                parsed = extract_table(table)
                if parsed:
                    table_data = parsed
                table.decompose()

            for i, img in enumerate(soup.find_all("img"), 1):
                src = img.get("src")
                if src and "pluginfile.php" in src:
                    image_dir = os.path.join(output_dir, "images", section_name.replace(" ", "_"))
                    slug_name = slugify(section_name)
                    downloaded = download_image(src, image_dir, f"{slug_name}_{i}", driver)
                    if downloaded:
                        img.replace_with(f"(image: {os.path.basename(downloaded)})")

            for a in soup.find_all("a", href=True):
                text = a.get_text(separator=" ", strip=True)
                href = unquote(a["href"])
                if not text:
                    text = href
                extracted_links.append({"text": text, "url": href})
                a.replace_with(f"[{text}]({href})")

            content = clean_course_text(soup.get_text(separator=" ", strip=True))

            sections[section_name] = {
                "text": content.strip(),
                "links": extracted_links,
                "table": table_data,
                "colors": colors,
                "metadata": {
                    "incomplete": False,
                    "source": driver.current_url,
                    "page_type": "mainpage_section",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            }

        except Exception as e:
            logger.warning(f"Failed to process section: {e}")

    return sections


def crawl_subpages(driver, output_dir):
    logger.info("Crawling subpages")
    
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.CLASS_NAME, "course-section"))
    )

    subpages_data = {}

    # Filter only activit grids that include /page/
    activity_grids = driver.find_elements(
        By.CSS_SELECTOR, ".activity-grid:has(img[src*='/page/'])"
    )

    subpage_links = []
    for grid in activity_grids:
        try:
            a_tag = grid.find_element(By.CSS_SELECTOR, ".activityname a")
            href = a_tag.get_attribute("href")
            title = a_tag.get_attribute("innerText").replace("Textseite", "").strip()
            if href and title:
                subpage_links.append((href, title))
        except Exception as e:
            logger.debug(f"Could not extract link from grid: {e}")

    logger.info(f"Found {len(subpage_links)} subpage links.")

    for href, title in subpage_links:
        try:
            logger.debug(f"Opening subpage: {title} ({href})")
            driver.get(href)

            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "box"))
            )

            soup = BeautifulSoup(driver.page_source, "html.parser")
            box = soup.find("div", class_="box py-3 generalbox center clearfix")
            if not box:
                continue

            # Extract tables first
            table_data = []
            for table in box.find_all("table"):
                parsed = extract_table(table)
                if parsed:
                    table_data = parsed
                table.decompose()

            # Annotate font colors + collect
            colors = extract_colors_from_soup(soup)

            # Replace image tags
            for i, img in enumerate(box.find_all("img"), 1):
                src = img.get("src")
                if src and "pluginfile.php" in src:
                    image_dir = os.path.join(output_dir, "images", title.replace(" ", "_"))
                    img_filename = os.path.basename(urlparse(src).path)
                    img_name = f"{title}_{i}_{img_filename.split('.')[0]}"
                    downloaded = download_image(src, image_dir, img_name, driver)
                    if downloaded:
                        img.replace_with(f"(image: {os.path.basename(downloaded)})")

            # Replace links
            extracted_links = []
            for a in box.find_all("a", href=True):
                text = a.get_text(separator=" ", strip=True)
                href_link = a["href"]

                if not text:
                    text = href_link

                extracted_links.append({"text": text, "url": href_link})
                a.replace_with(f"{text} ({href_link})")

            content = clean_course_text(box.get_text(separator=" ", strip=True))

            subpages_data[title] = {
                "text": content.strip(),
                "table": table_data,
                "links": extracted_links,
                "colors": colors,
                "metadata": {
                    "incomplete": False,
                    "source": href,
                    "page_type": "subpage",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            }

        except Exception as e:
            logger.warning(f"Failed to crawl {href}: {e}")

    return subpages_data


def crawl(driver, output_dir):
    logger.info("Starting unified webpage text crawl")
    
    # Ensure output_dir is a directory, not a file path
    if isinstance(output_dir, str):
        output_dir = os.path.dirname(output_dir) if output_dir.endswith('.json') else output_dir
    else:  # Path object
        output_dir = str(output_dir.parent) if output_dir.suffix == '.json' else str(output_dir)
    
    os.makedirs(output_dir, exist_ok=True)
    
    # Extract course_id from URL
    course_url = driver.current_url
    from urllib.parse import parse_qs, urlparse
    course_id = parse_qs(urlparse(course_url).query).get("id", ["unknown"])[0]
    
    # Crawl mainpage sections
    mainpage_data = crawl_mainpage_sections(driver, output_dir)
    
    # Crawl subpages
    subpage_data = crawl_subpages(driver, output_dir)
    
    # Combine both datasets
    all_pages = {**mainpage_data, **subpage_data}
    
    if all_pages:
        structured = transform_course_data(all_pages)
        
        # Generate timestamped filename
        crawl_timestamp = datetime.now(timezone.utc).isoformat(timespec='minutes').replace(":", "-")
        output_filename = f"{course_id}_webpagetext__{crawl_timestamp}.json"
        output_path = os.path.join(output_dir, output_filename)
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(structured, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Saved {len(mainpage_data)} mainpage sections and {len(subpage_data)} subpages to {output_path}")        
        return structured
    else:
        logger.warning("No webpage content found.")
        return None
