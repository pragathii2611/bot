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
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium_stealth import stealth
from openai import OpenAI

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)


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
    stealth(
        driver,
        languages=["en-US", "en"],
        vendor="Google Inc.",
        platform="Win32",
        webgl_vendor="Intel Inc.",
        renderer="Intel Iris OpenGL Engine",
        fix_hairline=True,
    )
    return driver


def human_scroll(driver):
    total_height = int(driver.execute_script("return document.body.scrollHeight"))
    for i in range(1, total_height, random.randint(300, 500)):
        driver.execute_script(f"window.scrollTo(0, {i});")
        time.sleep(random.uniform(0.1, 0.3))
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")


def crop_image(image_path):
    """Crop header/footer noise to focus on the availability grid."""
    try:
        img = Image.open(image_path)
        width, height = img.size
        # Crop top 250px (nav) and bottom 200px (footer)
        top = 250
        bottom = max(top + 100, height - 200)
        img = img.crop((0, top, width, bottom))
        out = "current_scan_cropped.png"
        img.save(out)
        return out
    except Exception:
        return image_path


def encode_image(image_path):
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def analyze_screenshot_with_gpt4o(image_path, facility_name, date_str):
    """
    Use GPT-4o to look at the OnePA availability page screenshot and
    return ONLY genuinely available (bookable) time slots.

    Key fix: very explicit prompt about what BOOKED vs AVAILABLE looks like
    on OnePA so GPT doesn't hallucinate open slots from greyed-out cells.
    """
    try:
        clean_path = crop_image(image_path)
        b64 = encode_image(clean_path)

        prompt = """You are analysing a screenshot of the OnePA badminton court booking page.

The availability grid shows time slots in a table. Each cell can be:
- AVAILABLE (bookable): bright white or light background, no strike-through, no grey fill, no 'X', no 'Booked' label — these are the slots I want
- UNAVAILABLE (booked/closed): grey background, crossed out, contains 'X', labelled 'Booked', 'N/A', 'Closed', or is visibly dimmed/disabled

YOUR TASK:
1. Look carefully at every time slot cell in the grid.
2. Return ONLY the time labels of cells that are TRULY AVAILABLE (white/bright, bookable).
3. If ALL slots are greyed out or booked, return an empty list [].
4. Do NOT include any slot that is greyed, crossed, dimmed, or labelled booked/unavailable.
5. Be strict — when in doubt, do NOT include the slot.

Return ONLY a Python list of time strings, nothing else. Example:
['7:00 PM', '8:00 PM']

If no slots are available, return exactly:
[]"""

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
            max_tokens=400,
        )

        content = response.choices[0].message.content.strip()
        logger.info(f"GPT-4o raw response: {content}")

        # Parse the list safely
        # Handle both ['7:00 PM'] and plain 7:00 PM formats
        times = re.findall(r'\d{1,2}:\d{2}\s*(?:AM|PM)', content, re.IGNORECASE)
        unique_times = sorted(list(set(times)))

        if unique_times:
            logger.info(f"✅ Available slots found: {unique_times}")
        else:
            logger.info(f"❌ No available slots detected for {facility_name} on {date_str}")

        return unique_times

    except Exception as e:
        logger.error(f"GPT-4o error: {e}")
        return []


def send_notification(facility_url, facility_name, date_str, times, recipient_email):
    sender_email    = os.getenv("SENDER_EMAIL")
    sender_password = os.getenv("SENDER_PASSWORD")
    if not sender_email or not sender_password:
        logger.warning("Email credentials not set — skipping notification")
        return

    slot_list = "\n".join(f"  • {t}" for t in times) if times else "  • Slots available (check page)"

    body = f"""Hi,

Good news! Available badminton court slots were found:

📍 Facility : {facility_name}
📅 Date     : {date_str}
🕐 Times    : 
{slot_list}

Book now before they're gone:
{facility_url}

—
OnePA Court Hunter
"""

    msg = MIMEMultipart()
    msg["From"]    = f"Court Hunter <{sender_email}>"
    msg["To"]      = recipient_email
    msg["Subject"] = f"🏸 Slots Open — {facility_name} on {date_str}"
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)
        logger.info(f"📧 Email sent to {recipient_email} for {facility_name} on {date_str}")
    except Exception as e:
        logger.error(f"Email failed: {e}")


def build_date_url(base_url: str, date: datetime) -> tuple[str, str]:
    """Inject a date into the OnePA URL and return (url, date_str)."""
    date_str = date.strftime("%d/%m/%Y")
    parsed   = urlparse(base_url)
    params   = parse_qs(parsed.query)
    params["date"] = [date_str]
    new_query = urlencode(params, doseq=True)
    url = urlunparse(parsed._replace(query=new_query))
    return url, date_str


def check_facility_availability(base_url: str, recipient_email: str) -> int:
    """
    Open the OnePA availability page for a facility for the next 7 days.
    Screenshot each day, ask GPT-4o if any slots are open.
    Returns total number of available slots found (0 if none).
    """
    driver = None
    total_found = 0

    try:
        logger.info(f"🚀 Starting scan for {base_url}")
        driver = setup_driver()
        today  = datetime.now()

        for day_offset in range(7):
            target_date = today + timedelta(days=day_offset)
            target_url, date_str = build_date_url(base_url, target_date)

            logger.info(f"📅 Checking day {day_offset + 1}: {date_str}")
            driver.get(target_url)

            # Wait for page to fully render
            time.sleep(random.uniform(4, 6))
            human_scroll(driver)
            time.sleep(2)

            # Full-page screenshot
            total_height = driver.execute_script("return document.body.scrollHeight")
            driver.set_window_size(1920, total_height + 200)
            screenshot_path = f"scan_{day_offset}.png"
            driver.save_screenshot(screenshot_path)

            # Extract facility name from URL for better logging/email
            fid = base_url.split("facilityId=")[-1].split("&")[0] if "facilityId=" in base_url else "Unknown"
            facility_name = fid.replace("_", " ").replace("cc", " CC").title()

            available_times = analyze_screenshot_with_gpt4o(screenshot_path, facility_name, date_str)

            if available_times:
                logger.info(f"🎉 SLOTS FOUND on {date_str}: {available_times}")
                send_notification(target_url, facility_name, date_str, available_times, recipient_email)
                total_found += len(available_times)
            else:
                logger.info(f"😴 {date_str} — fully booked or unavailable")

            # Human-like delay between days
            time.sleep(random.uniform(3, 5))

        logger.info(f"🏁 Scan complete. Total slots found: {total_found}")
        return total_found

    except Exception as e:
        logger.error(f"Scan error: {e}")
        return 0
    finally:
        if driver:
            driver.quit()
