import os
import logging
import atexit
import time
import random
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from scraper import check_facility_availability 

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# STATE: Stores detailed status for EACH court
monitoring_state = {
    "active": False,
    "email": None,
    "courts": [] 
}

scheduler = BackgroundScheduler()
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

def extract_court_name(url):
    """Extracts a pretty name from the URL."""
    try:
        if "facilityId=" in url:
            name_part = url.split("facilityId=")[1].split("&")[0]
            return name_part.replace("_", " ").replace("-", " ").title()
        return "Unknown Court"
    except:
        return "Court Facility"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start-monitor', methods=['POST'])
def start_monitor():
    global monitoring_state
    
    data = request.json
    email = data.get('email')
    urls = data.get('facility_urls', [])

    if not email or not urls:
        return jsonify({"success": False, "message": "Missing email or URLs"})

    monitoring_state["active"] = True
    monitoring_state["email"] = email
    
    # Initialize table
    monitoring_state["courts"] = []
    for url in urls:
        monitoring_state["courts"].append({
            "url": url,
            "name": extract_court_name(url),
            "status": "In Queue...",
            "last_checked": "-"
        })

    # Clear old jobs & Schedule new ones
    scheduler.remove_all_jobs()
    scheduler.add_job(func=run_check, trigger="cron", hour=21, minute="59-59", second="*/20", id="sniper_warmup")
    scheduler.add_job(func=run_check, trigger="cron", hour=22, minute="0-5", second="*/15", id="sniper_shot")
    scheduler.add_job(func=run_check, trigger="interval", minutes=20, id="casual_patrol")
    scheduler.add_job(func=run_check, id="instant_check")

    return jsonify({"success": True})

@app.route('/stop-monitor', methods=['POST'])
def stop_monitor():
    global monitoring_state
    
    scheduler.remove_all_jobs()
    monitoring_state["active"] = False
    
    for court in monitoring_state["courts"]:
        court["status"] = "Stopped"
        
    return jsonify({"success": True})

@app.route('/status', methods=['GET'])
def get_status():
    return jsonify({
        "active": monitoring_state["active"],
        "courts": monitoring_state["courts"]
    })

def run_check():
    global monitoring_state
    if not monitoring_state["active"]: return

    for i, court in enumerate(monitoring_state["courts"]):
        if not monitoring_state["active"]: break
        
        court["status"] = "Scanning..."
        court["last_checked"] = datetime.now().strftime('%H:%M:%S')
        
        try:
            slots_found = check_facility_availability(court["url"], monitoring_state["email"])
            
            if slots_found > 0:
                court["status"] = f"FOUND {slots_found} SLOTS"
            else:
                court["status"] = "No Slots"
        except Exception as e:
            court["status"] = "Error"
            logger.error(f"Error checking {court['name']}: {e}")
        
        # Human delay between checking different courts
        if i < len(monitoring_state["courts"]) - 1:
            time.sleep(random.uniform(5, 8))

if __name__ == '__main__':
    app.run(debug=True, port=5000, use_reloader=False)