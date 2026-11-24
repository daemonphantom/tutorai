from seleniumwire import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv
import os
from .utils.utils import get_logger

logger = get_logger(__name__)

load_dotenv()

username = os.getenv("TUB_USERNAME")
password = os.getenv("TUB_PASSWORD")


def login(driver, username, password, timeout=10):
    logger.info("Opening ISIS login page...")
    driver.get("https://isis.tu-berlin.de/login/index.php")

    try:
        tu_login_btn = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.ID, "shibbolethbutton"))
        )
        tu_login_btn.click()
        logger.info("TU Login button clicked.")


        WebDriverWait(driver, timeout).until(
            EC.visibility_of_element_located((By.ID, "username"))
        )

        WebDriverWait(driver, timeout).until(
            EC.visibility_of_element_located((By.ID, "password"))
        )

        driver.find_element(By.ID, "username").send_keys(username)
        driver.find_element(By.ID, "password").send_keys(password)

        driver.find_element(By.ID, "login-button").click()
        logger.info("Submitted login form.")

        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.ID, "page-header"))
        )

        logger.info("Logged in successfully!")

    except Exception as e:
        logger.error(f"Login failed: {e}")
        raise e

if __name__ == "__main__":
    options = webdriver.ChromeOptions() # Selenium WebDriver with Chrome
    options.add_argument("--start-maximized")
    
    # remove hashtag below for headless- if you dont want to see the browser
    # options.add_argument("--headless")
    
    driver = webdriver.Chrome(options=options)

    try:
        login(driver, username, password)
        logger.info("Logged in successfully!")
    except Exception as e:
        logger.error(f"Login failed: {e}")
    finally:
        driver.quit()
