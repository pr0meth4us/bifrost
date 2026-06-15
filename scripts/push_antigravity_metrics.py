#!/usr/bin/env python3
import os
import json
import glob
from datetime import datetime, timezone, timedelta
from google.cloud import monitoring_v3

# Path to local Antigravity logs
BRAIN_DIR = os.path.expanduser("~/.gemini/antigravity-ide/brain")
STATE_FILE = os.path.expanduser("~/.gemini/antigravity-ide/last_pushed_metrics.txt")
PROJECT_ID = "mac-project-7892"

def get_last_pushed():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            try:
                return datetime.fromisoformat(f.read().strip())
            except:
                pass
    return datetime.now(timezone.utc) - timedelta(hours=24)

def set_last_pushed(dt):
    with open(STATE_FILE, "w") as f:
        f.write(dt.isoformat())

def push_metrics():
    # Make sure we use the right SA
    sa_path = os.path.expanduser("~/code/gcp_sa_keys/khmer-tiktok-sa.json")
    if os.path.exists(sa_path):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_path
    
    last_pushed = get_last_pushed()
    max_timestamp = last_pushed
    
    new_points = []
    
    # 1. Gather all points
    for path in glob.glob(os.path.join(BRAIN_DIR, "*", ".system_generated", "logs", "transcript.jsonl")):
        try:
            with open(path, "r") as f:
                for line in f:
                    if not line.strip() or line.startswith("--"):
                        continue
                    try:
                        step = json.loads(line)
                        if step.get("source") == "MODEL" and step.get("type") == "PLANNER_RESPONSE":
                            created_at_str = step.get("created_at")
                            if not created_at_str:
                                continue
                                
                            dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                            if dt > last_pushed and dt > datetime.now(timezone.utc) - timedelta(hours=24):
                                new_points.append(dt)
                                if dt > max_timestamp:
                                    max_timestamp = dt
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            print(f"Error reading {path}: {e}")
            
    if not new_points:
        print("No new Antigravity requests to push.")
        return

    print(f"Found {len(new_points)} new Antigravity requests. Pushing to Cloud Monitoring...")
    
    # 2. Push to Google Cloud Monitoring
    client = monitoring_v3.MetricServiceClient()
    project_name = f"projects/{PROJECT_ID}"
    
    # We push a value of 1 for every request to a custom metric
    series = monitoring_v3.TimeSeries()
    series.metric.type = "custom.googleapis.com/antigravity/request_count"
    series.resource.type = "global"
    series.metric.labels["type"] = "ai_request"
    
    # We must push points individually if they belong to the same TimeSeries?
    # Actually, you can only write one point per TimeSeries per CreateTimeSeriesRequest.
    # So we loop over points.
    success_count = 0
    for dt in sorted(new_points):
        pt = monitoring_v3.Point()
        pt.value.int64_value = 1
        pt.interval.end_time = {"seconds": int(dt.timestamp()), "nanos": dt.microsecond * 1000}
        series.points = [pt]
        
        try:
            client.create_time_series(name=project_name, time_series=[series])
            success_count += 1
        except Exception as e:
            print(f"Failed to push point at {dt}: {e}")
            
    print(f"Successfully pushed {success_count} points.")
    set_last_pushed(max_timestamp)

if __name__ == "__main__":
    push_metrics()
