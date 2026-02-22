import time
import os
import smtplib
import logging
import base64
import re
import random
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from PIL import Image

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium_stealth import stealth
from openai import OpenAI

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

# ── Anti-spam state ───────────────────────────────────────────────────────────
notified_slots: set = set()       # "facility_url|date_str|time"
last_notified: dict = {}          # facility_url → datetime (1hr cooldown)


def setup_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=options
    )
    stealth(driver, languages=["en-US", "en"], vendor="Google Inc.",
            platform="Win32", webgl_vendor="Intel Inc.",
            renderer="Intel Iris OpenGL Engine", fix_hairline=True)
    return driver


def human_scroll(driver):
    total_height = int(driver.execute_script("return document.body.scrollHeight"))
    for i in range(1, total_height, random.randint(300, 500)):
        driver.execute_script(f"window.scrollTo(0, {i});")
        time.sleep(random.uniform(0.1, 0.3))
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")


def crop_image(image_path):
    try:
        img = Image.open(image_path)
        w, h = img.size
        img = img.crop((0, 250, w, max(350, h - 200)))
        out = "current_scan_cropped.png"
        img.save(out)
        return out
    except Exception:
        return image_path


def encode_image(image_path):
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def analyze_screenshot_with_gpt4o(image_path, facility_name, date_str):
    try:
        b64 = encode_image(crop_image(image_path))
        prompt = """You are analysing a screenshot of the OnePA badminton court booking page.

The availability grid shows time slots. Each cell is either:
- AVAILABLE: bright white/light background, no X, no grey fill, no 'Booked'/'N/A'/'Closed' label
- UNAVAILABLE: grey background, crossed out, has X, labelled Booked/N/A/Closed, or visibly dimmed

YOUR TASK:
1. Look at every time slot cell.
2. Return ONLY times of TRULY AVAILABLE (white/bright, bookable) cells.
3. If ALL slots are booked/grey, return [].
4. Be strict — when in doubt, do NOT include the slot.

Return ONLY a Python list of time strings. Example: ['7:00 PM', '8:00 PM']
If nothing available, return exactly: []"""

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
            ]}],
            max_tokens=400,
        )
        content = response.choices[0].message.content.strip()
        logger.info(f"GPT-4o: {content}")

        times = re.findall(r'\d{1,2}:\d{2}\s*(?:AM|PM)', content, re.IGNORECASE)
        unique = sorted(set(times))
        if unique:
            logger.info(f"Slots detected: {unique}")
        else:
            logger.info(f"No slots for {facility_name} on {date_str}")
        return unique

    except Exception as e:
        logger.error(f"GPT-4o error: {e}")
        return []


def send_notification(facility_url, facility_name, date_str, recipient_email):
    """Simple email — just tells them slots are open + booking link. No times, no counts."""
    sender_email    = os.getenv("SENDER_EMAIL")
    sender_password = os.getenv("SENDER_PASSWORD")
    if not sender_email or not sender_password:
        logger.warning("Email credentials missing — skipping")
        return

    body = f"""Hi,

Badminton court slots are available at {facility_name} on {date_str}.

Book here before they're gone:
{facility_url}

— Court Hunter
"""
    msg = MIMEMultipart()
    msg["From"]    = f"Court Hunter <{sender_email}>"
    msg["To"]      = recipient_email
    msg["Subject"] = f"🏸 Slots open at {facility_name} — {date_str}"
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)
        logger.info(f"📧 Email sent → {recipient_email}")
    except Exception as e:
        logger.error(f"Email failed: {e}")


def build_date_url(base_url: str, date: datetime) -> tuple[str, str]:
    date_str  = date.strftime("%d/%m/%Y")
    parsed    = urlparse(base_url)
    params    = parse_qs(parsed.query)
    params["date"] = [date_str]
    url = urlunparse(parsed._replace(query=urlencode(params, doseq=True)))
    return url, date_str


def check_facility_availability(base_url: str, recipient_email: str) -> int:
    driver      = None
    total_found = 0

    try:
        fid = base_url.split("facilityId=")[-1].split("&")[0] if "facilityId=" in base_url else "Unknown"
        facility_name = fid.replace("_", " ").replace("cc", " CC").title()

        logger.info(f" Scanning {facility_name}…")
        driver = setup_driver()
        today  = datetime.now()

        for day_offset in range(7):
            target_date = today + timedelta(days=day_offset)
            target_url, date_str = build_date_url(base_url, target_date)

            logger.info(f"Day {day_offset + 1}: {date_str}")
            driver.get(target_url)
            time.sleep(random.uniform(4, 6))
            human_scroll(driver)
            time.sleep(2)

            total_height = driver.execute_script("return document.body.scrollHeight")
            driver.set_window_size(1920, total_height + 200)
            screenshot_path = f"scan_{day_offset}.png"
            driver.save_screenshot(screenshot_path)

            available_times = analyze_screenshot_with_gpt4o(screenshot_path, facility_name, date_str)

            if available_times:
                # Only keep genuinely new slots we haven't notified about
                new_times = []
                for t in available_times:
                    key = f"{base_url}|{date_str}|{t}"
                    if key not in notified_slots:
                        new_times.append(t)
                        notified_slots.add(key)

                if new_times:
                    # Enforce 1hr cooldown per facility
                    now  = datetime.now()
                    last = last_notified.get(base_url)
                    if last and (now - last).seconds < 3600:
                        logger.info(f" Cooldown active for {facility_name}, skipping email")
                    else:
                        send_notification(target_url, facility_name, date_str, recipient_email)
                        last_notified[base_url] = now
                    total_found += len(new_times)
                else:
                    logger.info(f"⏭ Already notified about {date_str} slots")
            else:
                logger.info(f"{date_str} fully booked")

            time.sleep(random.uniform(3, 5))

        logger.info(f" Scan complete. New slots: {total_found}")
        return total_found

    except Exception as e:
        logger.error(f"Scan error: {e}")
        return 0
    finally:
        if driver:
            driver.quit()