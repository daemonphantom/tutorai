import os
import re
import json
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from .utils.utils import get_logger
from time import sleep

logger = get_logger(__name__)

BASE_URL = "https://isis.tu-berlin.de"

def normalize_user_role(raw_role: str) -> str:
    if not raw_role:
        return "Teilnehmer"
    
    role_lower = raw_role.lower()
    
    if any(keyword in role_lower for keyword in ['trainer', 'tutor', 'editor']):
        return "Trainer"
    
    return "Teilnehmer"

def crawl(driver, users_folder, course_metadata):
    os.makedirs(users_folder, exist_ok=True)
    
    course_url = driver.current_url
    course_id = parse_qs(urlparse(course_url).query).get("id", ["unknown"])[0]
    
    if course_id in ["unknown", "1"]:
        logger.error(f"Invalid course ID detected {course_id}. Aborting user crawl.")
        return {}

    logger.info(f"Crawling users for course {course_id}...")
    
    # Navigate to the users page
    users_url = f"{BASE_URL}/user/index.php?id={course_id}"
    driver.get(users_url)
    
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.table"))
        )
    except Exception as e:
        logger.error(f"Users table not found for course {course_id}: {e}")
        return {}
    
    try:
        show_all_link = driver.find_element(By.CSS_SELECTOR, "a[data-action='showcount']")
        if show_all_link:
            logger.info("Clicking Show all participants link...")
            driver.execute_script("arguments[0].click();", show_all_link)
            
            sleep(6)
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "table.table"))
            )
            logger.info("All participants loaded")
    except Exception:
        logger.info("No 'Show all participants' link found or already showing all")
    
    soup = BeautifulSoup(driver.page_source, "html.parser")
    users_data = {}
    
    participants_table = soup.find("table", class_="table")
    if not participants_table:
        logger.error("Participants table not found")
        return {}
    
    tbody = participants_table.find("tbody")
    if not tbody:
        logger.error("Table body not found")
        return {}
    
    rows = tbody.find_all("tr")
    logger.info(f"Found {len(rows)} user rows to process")
    
    for i, row in enumerate(rows, 1):
        try:
            name_cell = row.find("th", class_="cell c1")
            if not name_cell: 
                continue
                
            user_link = name_cell.find("a")
            if not user_link:
                continue
                
            user_name = user_link.get_text(strip=True)
            if len(user_name) > 2 and user_name[:2].isupper():
                user_name = user_name[2:]
            
            role_cell = row.find("td", class_="cell c2")
            if not role_cell:
                continue
                
            raw_role = role_cell.get_text(strip=True)
            user_role = normalize_user_role(raw_role)
            
            user_href = user_link.get("href", "")
            user_id_match = re.search(r"id=(\d+)", user_href)
            user_id = user_id_match.group(1) if user_id_match else "unknown"
            
            users_data[user_name] = {
                "name": user_name,
                "role": user_role,
                "raw_role": raw_role,
                "user_id": user_id,
                "href": user_href
            }
            
            if i % 100 == 0:
                logger.info(f"Processed {i}/{len(rows)} users...")
                
        except Exception as e:
            logger.warning(f"Error parsing user row {i}: {e}")
            continue
    
    logger.info(f"Successfully extracted {len(users_data)} users")
    
    output_file = os.path.join(users_folder, f"users_{course_id}.json")
    
    users_list = []
    for data in users_data.values():
        users_list.append({
            "name": data["name"],
            "role": data["role"],
            "raw_role": data["raw_role"],
            "user_id": data["user_id"],
            "href": data["href"],
            "course_id": course_id,
            "crawl_datetime": datetime.now().isoformat()
        })
    
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(users_list, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved users data to {output_file}")
    except Exception as e:
        logger.error(f"Failed to save users data: {e}")
    
    return users_data


def load_users_data(course_id, users_folder=None):
    """
    Load previously crawled users data for a course.
    Returns a dictionary mapping user names to their roles.
    """
    if users_folder is None:
        users_folder = os.path.join("data", f"course_{course_id}", "users")
    
    users_file = os.path.join(users_folder, f"users_{course_id}.json")
    
    if not os.path.exists(users_file):
        logger.warning(f"Users file not found: {users_file}")
        return {}
    
    try:
        with open(users_file, "r", encoding="utf-8") as f:
            users_list = json.load(f)
        
        users_data = {}
        for user in users_list:
            users_data[user["name"]] = {
                "role": user["role"],
                "user_id": user["user_id"],
                "href": user["href"]
            }
        
        logger.info(f"Loaded {len(users_data)} users from {users_file}")
        return users_data
        
    except Exception as e:
        logger.error(f"Failed to load users data: {e}")
        return {}


def get_user_info(author_name, users_data):
    if not users_data or not author_name:
        return {"role": "Teilnehmer", "user_id": "unknown"}
    
    if author_name in users_data:
        user_data = users_data[author_name]
        return {
            "role": user_data["role"],
            "user_id": user_data["user_id"]
        }
    
    author_lower = author_name.lower()
    for user_name, user_data in users_data.items():
        if user_name.lower() == author_lower:
            return {
                "role": user_data["role"],
                "user_id": user_data["user_id"]
            }
    
    return {"role": "Teilnehmer", "user_id": "unknown"}
