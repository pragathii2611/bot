import os
import logging
import atexit
import time
import random
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from scraper import check_facility_availability
from pytz import timezone

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── Known CCs with their OnePA facility URLs ──────────────────────────────────
KNOWN_FACILITIES = [
    {"id": "bedokcc_badmintoncourts",       "name": "Bedok CC",         "url": "https://www.onepa.gov.sg/facilities/availability?facilityId=bedokcc_badmintoncourts"},
    {"id": "tampinescc_badmintoncourts",     "name": "Tampines CC",      "url": "https://www.onepa.gov.sg/facilities/availability?facilityId=tampinescc_badmintoncourts"},
    {"id": "toaPayohcc_badmintoncourts",     "name": "Toa Payoh CC",     "url": "https://www.onepa.gov.sg/facilities/availability?facilityId=toaPayohcc_badmintoncourts"},
    {"id": "woodlandscc_badmintoncourts",    "name": "Woodlands CC",     "url": "https://www.onepa.gov.sg/facilities/availability?facilityId=woodlandscc_badmintoncourts"},
    {"id": "amkcc_badmintoncourts",          "name": "Ang Mo Kio CC",    "url": "https://www.onepa.gov.sg/facilities/availability?facilityId=amkcc_badmintoncourts"},
    {"id": "jurongwestcc_badmintoncourts",   "name": "Jurong West CC",   "url": "https://www.onepa.gov.sg/facilities/availability?facilityId=jurongwestcc_badmintoncourts"},
    {"id": "clementicc_badmintoncourts",     "name": "Clementi CC",      "url": "https://www.onepa.gov.sg/facilities/availability?facilityId=clementicc_badmintoncourts"},
    {"id": "yishuncc_badmintoncourts",       "name": "Yishun CC",        "url": "https://www.onepa.gov.sg/facilities/availability?facilityId=yishuncc_badmintoncourts"},
    {"id": "pasirriscc_badmintoncourts",     "name": "Pasir Ris CC",     "url": "https://www.onepa.gov.sg/facilities/availability?facilityId=pasirriscc_badmintoncourts"},
    {"id": "serangooncc_badmintoncourts",    "name": "Serangoon CC",     "url": "https://www.onepa.gov.sg/facilities/availability?facilityId=serangooncc_badmintoncourts"},
    {"id": "bishan_badmintoncourts",         "name": "Bishan CC",        "url": "https://www.onepa.gov.sg/facilities/availability?facilityId=bishan_badmintoncourts"},
    {"id": "choaphuacc_badmintoncourts",     "name": "Choa Chu Kang CC", "url": "https://www.onepa.gov.sg/facilities/availability?facilityId=choaphuacc_badmintoncourts"},
]

monitoring_state = {
    "active": False,
    "email": "",
    "courts": []
}

scheduler = BackgroundScheduler()
scheduler.start()
atexit.register(lambda: scheduler.shutdown())


@app.route('/')
def index():
    return render_template('index.html', facilities=KNOWN_FACILITIES)


@app.route('/api/facilities')
def get_facilities():
    return jsonify(KNOWN_FACILITIES)


@app.route('/start-monitor', methods=['POST'])
def start_monitor():
    global monitoring_state

    data = request.json
    email = data.get('email', '').strip()
    selected_ids = data.get('facility_ids', [])

    if not email:
        return jsonify({"success": False, "message": "Email is required"})
    if not selected_ids:
        return jsonify({"success": False, "message": "Select at least one CC"})

    # Build courts list from selected IDs
    selected = [f for f in KNOWN_FACILITIES if f["id"] in selected_ids]

    monitoring_state["active"] = True
    monitoring_state["email"] = email
    monitoring_state["courts"] = [{
        "id":           f["id"],
        "url":          f["url"],
        "name":         f["name"],
        "status":       "In Queue…",
        "last_checked": "-",
        "slots_found":  0,
    } for f in selected]

    scheduler.remove_all_jobs()
    # Aggressive checks around 10 PM when slots drop
    scheduler.add_job(func=run_check, trigger="cron", hour=21, minute="59", second="*/20", id="warmup")
    scheduler.add_job(func=run_check, trigger="cron", hour=22, minute="0-5", second="*/15", id="sniper")
    # Regular patrol every 15 min
    scheduler.add_job(func=run_check, trigger="interval", minutes=15, id="patrol")
    # Immediate first check
    scheduler.add_job(func=run_check, id="instant")

    return jsonify({"success": True})


@app.route('/stop-monitor', methods=['POST'])
def stop_monitor():
    global monitoring_state
    scheduler.remove_all_jobs()
    monitoring_state["active"] = False
    for court in monitoring_state["courts"]:
        court["status"] = "Stopped"
    return jsonify({"success": True})


@app.route('/status')
def get_status():
    return jsonify({
        "active":  monitoring_state["active"],
        "courts":  monitoring_state["courts"],
        "email":   monitoring_state["email"],
    })


def run_check():
    global monitoring_state
    if not monitoring_state["active"]:
        return

    for i, court in enumerate(monitoring_state["courts"]):
        if not monitoring_state["active"]:
            break

        court["status"] = "Scanning…"
        sg_time = datetime.now(timezone('Asia/Singapore'))
        court["last_checked"] = sg_time.strftime('%H:%M:%S')

        try:
            slots_found = check_facility_availability(court["url"], monitoring_state["email"])
            if slots_found > 0:
                court["status"] = f"🎉 {slots_found} SLOT(S) FOUND"
                court["slots_found"] = slots_found
            else:
                court["status"] = "No slots"
                court["slots_found"] = 0
        except Exception as e:
            court["status"] = "Error"
            logger.error(f"Error checking {court['name']}: {e}")

        if i < len(monitoring_state["courts"]) - 1:
            time.sleep(random.uniform(4, 7))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
