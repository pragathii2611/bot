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

# --- CONFIGURATION ---
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# 🔑 OPENAI SETUP
# PASTE YOUR KEY INSIDE THE QUOTES BELOW 👇
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") 
client = OpenAI(api_key=OPENAI_API_KEY)
def setup_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--window-size=1920,1080")
    
    # --- CLOUD STABILITY SETTINGS ---
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    
    # 🥷 STEALTH SETTINGS
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")

    # Standard Install (No custom binary paths needed for Render)
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

    stealth(driver,
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
    try:
        img = Image.open(image_path)
        width, height = img.size
        if height > 600:
            img = img.crop((0, 300, width, height - 300))
        img.save("current_scan_cropped.png")
        return "current_scan_cropped.png"
    except:
        return image_path

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def analyze_screenshot_with_gpt4o(image_path):
    try:
        clean_path = crop_image(image_path)
        base64_img = encode_image(clean_path)
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a vision assistant extracting booking times."},
                {"role": "user", "content": [
                    {"type": "text", "text": "List the exact TIME LABELS for all AVAILABLE (White/Green) slots. Ignore Grey/X slots. Return a Python list of strings. Example: ['9:00 AM']"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_img}"}}
                ]}
            ],
            max_tokens=300
        )
        content = response.choices[0].message.content.strip()
        
        found_times = re.findall(r'\d{1,2}:\d{2}\s*(?:AM|PM)?', content, re.IGNORECASE)
        unique_times = sorted(list(set(found_times)))
        
        if unique_times:
            logger.info(f"✅ AI Found: {unique_times}")
        
        return unique_times
        
    except Exception as e:
        logger.error(f"❌ AI Error: {e}")
        return []

# --- UPDATED EMAIL FUNCTION ---
def send_notification(facility_url, date_str, times, recipient_email):
    sender_email = os.getenv('SENDER_EMAIL')
    sender_password = os.getenv('SENDER_PASSWORD')
    if not sender_email: return

    msg = MIMEMultipart()
    msg['From'] = f"Court Hunter <{sender_email}>"
    msg['To'] = recipient_email
    
    # Simple Subject
    msg['Subject'] = f"Slots Available: {date_str}"

    # Professional, Simple Body
    body = f"""
    Good news,

    We have detected available booking slots for the following date:

    📅 Date: {date_str}
    
    📍 Secure your booking here:
    {facility_url}

    Best regards,
    Court Hunter
    """
    msg.attach(MIMEText(body, 'plain'))

    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)
        logger.info(f"📧 Notification sent for {date_str}")
    except Exception as e:
        logger.error(f"Email failed: {e}")

def update_url_date(original_url, days_to_add):
    try:
        parsed = urlparse(original_url)
        params = parse_qs(parsed.query)
        current_date_str = params.get('date', [datetime.now().strftime('%d/%m/%Y')])[0]
        current_date = datetime.strptime(current_date_str, '%d/%m/%Y')
        new_date = current_date + timedelta(days=days_to_add)
        new_date_str = new_date.strftime('%d/%m/%Y')
        params['date'] = [new_date_str]
        new_query = urlencode(params, doseq=True)
        return urlunparse(parsed._replace(query=new_query)), new_date_str
    except Exception as e:
        logger.error(f"Date math failed: {e}")
        return original_url, "Unknown Date"

def check_facility_availability(base_url, recipient_email):
    driver = None
    try:
        logger.info(f"🚀 Starting Stealth Calendar Scan...")
        driver = setup_driver()
        
        for day_offset in range(7):
            target_url, date_str = update_url_date(base_url, day_offset)
            logger.info(f"📅 Checking Day {day_offset + 1}: {date_str}")
            
            driver.get(target_url)
            
            # 🕒 Stealth Delays
            time.sleep(random.uniform(4, 7)) 
            human_scroll(driver)
            time.sleep(2)
            
            total_height = driver.execute_script("return document.body.scrollHeight")
            driver.set_window_size(1920, total_height + 500)
            driver.save_screenshot("current_scan.png")

            available_times = analyze_screenshot_with_gpt4o("current_scan.png")
            
            if available_times:
                logger.info(f"🎉 JACKPOT! Found slots on {date_str}")
                send_notification(target_url, date_str, available_times, recipient_email)
                return len(available_times)
            
            logger.info(f"❌ {date_str} is full. Resting...")
            time.sleep(random.uniform(3, 6))

        logger.info("🏁 Checked 7 days. No luck.")
        return 0

    except Exception as e:
        logger.error(f"Error: {e}")
        return 0
    finally:
        if driver:
            driver.quit()