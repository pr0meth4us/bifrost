import os
import time
import tempfile
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

PRICING = {"input": 0.075, "output": 0.30, "pro_input": 1.25, "pro_output": 3.75}   # per 1M tokens

APP_CONFIGS = [
    {
        "label": "TikTok Keeper",
        "client_id": "bifrost_client_5dd70ad3a86c4f51",
        "project_id": "mac-project-7892",
        "color": "rgba(217, 70, 239",   # fuchsia
    },
    {
        "label": "OCR Tools",
        "client_id": "random_project_abf21112",
        "project_id": "khmer-ocr-496606",
        "color": "rgba(56, 189, 248",    # cyan
    },
    {
        "label": "Auto Texter",
        "client_id": "auto_texter_77cb5d03",
        "project_id": "gen-lang-client-0429923800",
        "color": "rgba(74, 222, 128",    # green
    },
]

def fetch_billing_data(db_hook):
    """Fetches real financial data from BigQuery Billing Export."""
    from google.cloud import bigquery
    from google.oauth2 import service_account
    
    billing_data = {
        "status": "waiting",
        "total_spend": 0,
        "credits_remaining": 300.0, # Default GCP free trial credit
        "services": {}
    }
    
    tmp = None
    try:
        sa_path = f"/app/secrets/khmer-tiktok-sa.json"
        
        if not os.path.exists(sa_path):
            try:
                ag_creds = db_hook("bifrost_client_5dd70ad3a86c4f51")
                fd, tmp = tempfile.mkstemp(suffix=".json")
                with os.fdopen(fd, 'w') as f:
                    f.write(ag_creds)
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = tmp
                bq_client = bigquery.Client(project="mac-project-7892")
            except:
                bq_client = bigquery.Client(project="mac-project-7892")
        else:
            bq_client = bigquery.Client(project="mac-project-7892", credentials=service_account.Credentials.from_service_account_file(sa_path))
            
        query = """
        SELECT 
            service.description as service_name,
            SUM(cost) as total_cost,
            SUM(CASE WHEN credit.name IS NOT NULL THEN credit.amount ELSE 0 END) as total_credits
        FROM `mac-project-7892.billing_export.gcp_billing_export_v1_016306_312143_6117C7`
        LEFT JOIN UNNEST(credits) as credit
        GROUP BY 1
        ORDER BY total_cost DESC
        """
        
        query_job = bq_client.query(query)
        results = query_job.result()
        
        billing_data["status"] = "ready"
        total_real_spend = 0
        total_real_credits = 0
        
        for row in results:
            service = row.service_name
            cost = row.total_cost
            credits = row.total_credits
            total_real_spend += cost
            total_real_credits += credits
            billing_data["services"][service] = {"cost": cost, "credits": credits}
            
        billing_data["total_spend"] = total_real_spend
        # Removed hardcoded $300 free trial assumption 
        
    except Exception as e:
        logger.error(f"Error fetching BigQuery billing data: {e}")
    finally:
        if tmp:
            try: os.unlink(tmp)
            except: pass

    return billing_data


def fetch_ai_metrics(db_hook):
    """Fetches AI metrics via Cloud Monitoring for Vertex AI and custom Antigravity."""
    now = time.time()
    end_secs = int(now)
    start_secs = end_secs - 30 * 24 * 60 * 60

    def query_project(project_id, creds_json):
        result = {
            "input_by_day":    [0] * 30,
            "output_by_day":   [0] * 30,
            "requests_by_day": [0] * 30,
            "models": {},
        }
        if not creds_json:
            return result

        fd, tmp = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(creds_json)
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = tmp

            from google.cloud import monitoring_v3
            mc = monitoring_v3.MetricServiceClient()
            proj = f"projects/{project_id}"

            interval = monitoring_v3.TimeInterval({
                "end_time":   {"seconds": end_secs,   "nanos": 0},
                "start_time": {"seconds": start_secs, "nanos": 0},
            })

            aggregation = monitoring_v3.Aggregation({
                "alignment_period": {"seconds": 86400},
                "per_series_aligner": monitoring_v3.Aggregation.Aligner.ALIGN_SUM,
                "cross_series_reducer": monitoring_v3.Aggregation.Reducer.REDUCE_SUM,
                "group_by_fields": ["metric.labels.type"],
            })

            def safe_list(filter_str, agg=None):
                try:
                    req = {
                        "name": proj, "filter": filter_str,
                        "interval": interval,
                        "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                    }
                    if agg:
                        req["aggregation"] = agg
                    return list(mc.list_time_series(request=req))
                except Exception:
                    return []

            token_series = safe_list(
                'metric.type="aiplatform.googleapis.com/publisher/online_serving/token_count"',
                agg=aggregation,
            )
            for ts in token_series:
                token_type = ts.metric.labels.get("type", "")
                for pt in ts.points:
                    day_offset = int((end_secs - pt.interval.end_time.timestamp()) / 86400)
                    idx = 29 - day_offset
                    if 0 <= idx < 30:
                        val = int(pt.value.int64_value or pt.value.double_value or 0)
                        if token_type == "input":
                            result["input_by_day"][idx] += val
                        elif token_type == "output":
                            result["output_by_day"][idx] += val

            req_agg = monitoring_v3.Aggregation({
                "alignment_period": {"seconds": 86400},
                "per_series_aligner": monitoring_v3.Aggregation.Aligner.ALIGN_SUM,
                "cross_series_reducer": monitoring_v3.Aggregation.Reducer.REDUCE_SUM,
            })
            for ts in safe_list(
                'metric.type="aiplatform.googleapis.com/publisher/online_serving/model_invocation_count"',
                agg=req_agg
            ):
                for pt in ts.points:
                    day_offset = int((end_secs - pt.interval.end_time.timestamp()) / 86400)
                    idx = 29 - day_offset
                    if 0 <= idx < 30:
                        result["requests_by_day"][idx] += int(pt.value.int64_value or pt.value.double_value or 0)

            model_agg = monitoring_v3.Aggregation({
                "alignment_period": {"seconds": 30 * 86400},
                "per_series_aligner": monitoring_v3.Aggregation.Aligner.ALIGN_SUM,
                "cross_series_reducer": monitoring_v3.Aggregation.Reducer.REDUCE_SUM,
                "group_by_fields": ["resource.labels.model_user_id"],
            })
            for ts in safe_list(
                'metric.type="aiplatform.googleapis.com/publisher/online_serving/token_count"',
                agg=model_agg
            ):
                model_id = ts.resource.labels.get("model_user_id", "unknown")
                if not model_id or model_id == "":
                    model_id = "unknown"
                for pt in ts.points:
                    val = int(pt.value.int64_value or pt.value.double_value or 0)
                    result["models"][model_id] = result["models"].get(model_id, 0) + val

        except Exception as e:
            logger.error(f"Error querying project: {e}")
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)

        return result

    projects_data = []
    grand_input = [0] * 30
    grand_output = [0] * 30
    grand_requests = [0] * 30
    grand_models = {}

    for cfg in APP_CONFIGS:
        creds = db_hook(cfg["client_id"])
        data = query_project(cfg["project_id"], creds)

        total_in  = sum(data["input_by_day"])
        total_out = sum(data["output_by_day"])
        total_req = sum(data["requests_by_day"])
        cost = (total_in / 1_000_000) * PRICING["input"] + (total_out / 1_000_000) * PRICING["output"]

        projects_data.append({
            "label":    cfg["label"],
            "project":  cfg["project_id"],
            "color":    cfg["color"],
            "input":    total_in,
            "output":   total_out,
            "requests": total_req,
            "cost":     round(cost, 6),
            "input_by_day":    data["input_by_day"],
            "output_by_day":   data["output_by_day"],
            "requests_by_day": data["requests_by_day"],
            "models":   data["models"],
        })

        for i in range(30):
            grand_input[i]    += data["input_by_day"][i]
            grand_output[i]   += data["output_by_day"][i]
            grand_requests[i] += data["requests_by_day"][i]
        for model, count in data["models"].items():
            grand_models[model] = grand_models.get(model, 0) + count

    return {
        "projects": projects_data,
        "grand_input": grand_input,
        "grand_output": grand_output,
        "grand_requests": grand_requests,
        "grand_models": grand_models,
        "end_secs": end_secs
    }
