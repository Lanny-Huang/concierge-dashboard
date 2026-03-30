#!/usr/bin/env python3
"""Regenerate the Concierge Call Volume Dashboard from Databricks data."""

import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).resolve().parent
DB_HOST = "intuit-e2-739275435815-exploration-prd.cloud.databricks.com"
DB_PROFILE = "intuit-e2-739275435815-exploration-prd"
DB_HTTP_PATH = "/sql/1.0/warehouses/7b9892a3b3e97fbb"


def get_databricks_token() -> str:
    env_token = os.environ.get("DATABRICKS_TOKEN")
    if env_token:
        return env_token
    result = subprocess.run(
        ["/opt/homebrew/bin/databricks", "auth", "token", "--profile", DB_PROFILE],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)["access_token"]


def query(cursor, sql):
    cursor.execute(sql)
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def main():
    try:
        from databricks import sql as dbsql
    except ImportError:
        sys.exit("pip install databricks-sql-connector")

    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
        import plotly.io as pio
    except ImportError:
        sys.exit("pip install plotly")

    token = get_databricks_token()
    print(f"{datetime.now():%H:%M:%S}  Connecting to Databricks...")

    conn = dbsql.connect(server_hostname=DB_HOST, http_path=DB_HTTP_PATH, access_token=token)
    cur = conn.cursor()

    # ── 1. Summary stats ────────────────────────────────────────────
    print(f"{datetime.now():%H:%M:%S}  Querying summary stats...")
    summary = query(cur, """
        SELECT 
            MIN(DATE(from_utc_timestamp(contact_start_ts, 'US/Pacific'))) as min_date,
            MAX(DATE(from_utc_timestamp(contact_start_ts, 'US/Pacific'))) as max_date,
            COUNT(DISTINCT cc_id) as total_calls,
            DATEDIFF(MAX(DATE(from_utc_timestamp(contact_start_ts, 'US/Pacific'))),
                     MIN(DATE(from_utc_timestamp(contact_start_ts, 'US/Pacific')))) + 1 as total_days
        FROM cgan_ustax_published.ae_concierge_master_lh
        WHERE contact_start_ts IS NOT NULL AND leg_direction = 'INBOUND'
    """)[0]

    min_date = str(summary["min_date"])
    max_date = str(summary["max_date"])
    total_calls = int(summary["total_calls"])
    total_days = int(summary["total_days"])
    avg_daily = round(total_calls / total_days)

    min_dt = datetime.strptime(min_date, "%Y-%m-%d")
    max_dt = datetime.strptime(max_date, "%Y-%m-%d")
    date_range_str = f"{min_dt.strftime('%b %d, %Y')} - {max_dt.strftime('%b %d, %Y')} ({total_days} days)"

    # ── 2. Hourly heatmap (PT) ──────────────────────────────────────
    print(f"{datetime.now():%H:%M:%S}  Querying hourly heatmap (PT)...")
    heatmap_rows = query(cur, """
        SELECT 
            CASE DAYOFWEEK(DATE(from_utc_timestamp(contact_start_ts, 'US/Pacific')))
                WHEN 1 THEN 'Sunday' WHEN 2 THEN 'Monday' WHEN 3 THEN 'Tuesday'
                WHEN 4 THEN 'Wednesday' WHEN 5 THEN 'Thursday' WHEN 6 THEN 'Friday'
                WHEN 7 THEN 'Saturday' END as day_of_week,
            HOUR(from_utc_timestamp(contact_start_ts, 'US/Pacific')) as hour_pt,
            COUNT(DISTINCT cc_id) as total_calls,
            COUNT(DISTINCT DATE(from_utc_timestamp(contact_start_ts, 'US/Pacific'))) as num_days,
            ROUND(COUNT(DISTINCT cc_id) / COUNT(DISTINCT DATE(from_utc_timestamp(contact_start_ts, 'US/Pacific'))), 1) as avg_calls
        FROM cgan_ustax_published.ae_concierge_master_lh
        WHERE contact_start_ts IS NOT NULL AND leg_direction = 'INBOUND'
        GROUP BY 1, 2
        HAVING hour_pt BETWEEN 5 AND 20
        ORDER BY 
            CASE day_of_week WHEN 'Sunday' THEN 1 WHEN 'Monday' THEN 2 WHEN 'Tuesday' THEN 3
                WHEN 'Wednesday' THEN 4 WHEN 'Thursday' THEN 5 WHEN 'Friday' THEN 6 WHEN 'Saturday' THEN 7 END,
            hour_pt
    """)

    # ── 3. Hourly by day-of-week line chart (PT) ────────────────────
    print(f"{datetime.now():%H:%M:%S}  Querying hourly line data (PT)...")
    hourly_line = query(cur, """
        SELECT 
            CASE DAYOFWEEK(DATE(from_utc_timestamp(contact_start_ts, 'US/Pacific')))
                WHEN 1 THEN 'Sunday' WHEN 2 THEN 'Monday' WHEN 3 THEN 'Tuesday'
                WHEN 4 THEN 'Wednesday' WHEN 5 THEN 'Thursday' WHEN 6 THEN 'Friday'
                WHEN 7 THEN 'Saturday' END as day_of_week,
            HOUR(from_utc_timestamp(contact_start_ts, 'US/Pacific')) as hour_pt,
            ROUND(COUNT(DISTINCT cc_id) / COUNT(DISTINCT DATE(from_utc_timestamp(contact_start_ts, 'US/Pacific'))), 0) as avg_calls
        FROM cgan_ustax_published.ae_concierge_master_lh
        WHERE contact_start_ts IS NOT NULL AND leg_direction = 'INBOUND'
        GROUP BY 1, 2
        HAVING hour_pt BETWEEN 5 AND 20
        ORDER BY 1, 2
    """)

    # ── 4. Daily volume timeline ────────────────────────────────────
    print(f"{datetime.now():%H:%M:%S}  Querying daily volume...")
    daily_rows = query(cur, """
        SELECT 
            DATE(from_utc_timestamp(contact_start_ts, 'US/Pacific')) as call_date,
            DAYOFWEEK(DATE(from_utc_timestamp(contact_start_ts, 'US/Pacific'))) as dow,
            COUNT(DISTINCT cc_id) as calls
        FROM cgan_ustax_published.ae_concierge_master_lh
        WHERE contact_start_ts IS NOT NULL AND leg_direction = 'INBOUND'
        GROUP BY 1, 2
        ORDER BY 1
    """)

    # ── 5. By queue type ────────────────────────────────────────────
    print(f"{datetime.now():%H:%M:%S}  Querying queue breakdown...")
    queue_rows = query(cur, """
        SELECT 
            COALESCE(concierge_type, 'Unknown') as queue_type,
            HOUR(from_utc_timestamp(contact_start_ts, 'US/Pacific')) as hour_pt,
            COUNT(DISTINCT cc_id) as total_calls
        FROM cgan_ustax_published.ae_concierge_master_lh
        WHERE contact_start_ts IS NOT NULL AND leg_direction = 'INBOUND'
        GROUP BY 1, 2
        HAVING hour_pt BETWEEN 5 AND 20
        ORDER BY 1, 2
    """)

    # ── 6. Customer timezone distribution ───────────────────────────
    print(f"{datetime.now():%H:%M:%S}  Querying timezone distribution...")
    tz_rows = query(cur, """
        WITH phone_tz AS (
            SELECT cc_id, customer_phone,
                CASE 
                    WHEN SUBSTR(customer_phone, 3, 3) IN ('201','202','203','207','212','215','216','217','219','220','223','224','225','226','228','229','231','234','239','240','248','251','252','253','254','256','260','262','267','269','270','272','276','281','301','302','304','305','308','309','310','312','313','314','315','316','317','318','319','320','321','325','326','327','330','331','332','334','336','337','339','340','341','346','347','351','352','360','361','364','367','380','385','386','401','402','404','405','407','410','412','413','414','415','417','419','423','424','425','430','432','434','435','440','442','443','445','447','448','458','463','464','469','470','475','478','479','480','484','501','502','503','504','505','507','508','509','510','512','513','515','516','517','518','520','530','531','534','539','540','541','551','557','559','561','562','563','564','567','570','571','573','574','575','580','585','586','601','602','603','605','606','607','608','609','610','612','614','615','616','617','618','619','620','623','626','627','628','629','630','631','636','641','646','650','651','657','659','660','661','662','667','669','678','680','681','682','689','701','702','703','704','706','707','708','712','713','714','715','716','717','718','719','720','724','725','726','727','731','732','734','737','740','743','747','754','757','760','762','763','764','765','769','770','771','772','773','774','775','779','781','785','786','787','801','802','803','804','805','806','808','810','812','813','814','815','816','817','818','828','830','831','832','838','843','845','847','848','850','854','856','857','858','859','860','862','863','864','865','870','872','878','901','903','904','906','907','908','909','910','912','913','914','915','916','917','918','919','920','925','928','929','930','931','934','936','937','938','940','941','943','945','947','949','951','952','954','956','959','970','971','972','973','978','979','980','984','985','989')
                    THEN 
                        CASE 
                            WHEN SUBSTR(customer_phone, 3, 3) IN ('907') THEN 'AK'
                            WHEN SUBSTR(customer_phone, 3, 3) IN ('808') THEN 'HT'
                            WHEN SUBSTR(customer_phone, 3, 3) IN ('602','623','480','520','928','303','719','720','970','208','406','505','575','307','385','435','801','702','725','775') THEN 'MT'
                            WHEN SUBSTR(customer_phone, 3, 3) IN ('209','213','310','323','341','408','415','424','442','510','530','559','562','619','626','627','628','650','657','659','661','669','707','714','747','760','805','818','831','858','909','916','925','949','951','971','503','541','971','206','253','360','425','509','564') THEN 'PT'
                            WHEN SUBSTR(customer_phone, 3, 3) IN ('205','251','256','334','479','501','601','662','769','870','318','337','504','225','985','214','254','281','325','346','361','409','430','432','469','512','682','713','737','806','817','830','832','903','915','918','936','940','956','972','979','316','620','785','913','402','531','308','405','580','918','605','701','712','515','563','319','641','612','651','763','952','320','507','218','314','417','573','636','660','816') THEN 'CT'
                            ELSE 'ET'
                        END
                    ELSE 'Unknown'
                END as customer_tz
            FROM cgan_ustax_published.ae_concierge_master_lh
            WHERE contact_start_ts IS NOT NULL AND leg_direction = 'INBOUND'
              AND customer_phone IS NOT NULL
        )
        SELECT customer_tz, COUNT(DISTINCT cc_id) as calls
        FROM phone_tz
        GROUP BY 1
        ORDER BY 2 DESC
    """)

    # ── 7. Peak hour analysis ───────────────────────────────────────
    print(f"{datetime.now():%H:%M:%S}  Computing peak hour...")
    peak_hour_rows = query(cur, """
        SELECT 
            HOUR(from_utc_timestamp(contact_start_ts, 'US/Pacific')) as hour_pt,
            COUNT(DISTINCT cc_id) as total_calls
        FROM cgan_ustax_published.ae_concierge_master_lh
        WHERE contact_start_ts IS NOT NULL AND leg_direction = 'INBOUND'
        GROUP BY 1
        ORDER BY 2 DESC
        LIMIT 1
    """)
    peak_hour_pt = int(peak_hour_rows[0]["hour_pt"])

    peak_dow_rows = query(cur, """
        SELECT 
            CASE DAYOFWEEK(DATE(from_utc_timestamp(contact_start_ts, 'US/Pacific')))
                WHEN 1 THEN 'Sunday' WHEN 2 THEN 'Monday' WHEN 3 THEN 'Tuesday'
                WHEN 4 THEN 'Wednesday' WHEN 5 THEN 'Thursday' WHEN 6 THEN 'Friday'
                WHEN 7 THEN 'Saturday' END as day_of_week,
            COUNT(DISTINCT cc_id) as total_calls
        FROM cgan_ustax_published.ae_concierge_master_lh
        WHERE contact_start_ts IS NOT NULL AND leg_direction = 'INBOUND'
        GROUP BY 1
        ORDER BY 2 DESC
        LIMIT 1
    """)
    peak_dow = peak_dow_rows[0]["day_of_week"]

    # ── 8. Customer local time heatmap ──────────────────────────────
    print(f"{datetime.now():%H:%M:%S}  Querying customer local time heatmap...")
    local_heatmap = query(cur, """
        WITH calls_local AS (
            SELECT cc_id, contact_start_ts,
                DATE(from_utc_timestamp(contact_start_ts, 'US/Pacific')) as call_date_pt,
                CASE 
                    WHEN SUBSTR(customer_phone, 3, 3) IN ('907') THEN -9
                    WHEN SUBSTR(customer_phone, 3, 3) IN ('808') THEN -10
                    WHEN SUBSTR(customer_phone, 3, 3) IN ('602','623','480','520','928','303','719','720','970','208','406','505','575','307','385','435','801','702','725','775') THEN -7
                    WHEN SUBSTR(customer_phone, 3, 3) IN ('209','213','310','323','341','408','415','424','442','510','530','559','562','619','626','627','628','650','657','659','661','669','707','714','747','760','805','818','831','858','909','916','925','949','951','971','503','541','971','206','253','360','425','509','564') THEN -8
                    WHEN SUBSTR(customer_phone, 3, 3) IN ('205','251','256','334','479','501','601','662','769','870','318','337','504','225','985','214','254','281','325','346','361','409','430','432','469','512','682','713','737','806','817','830','832','903','915','918','936','940','956','972','979','316','620','785','913','402','531','308','405','580','918','605','701','712','515','563','319','641','612','651','763','952','320','507','218','314','417','573','636','660','816') THEN -6
                    ELSE -5
                END as utc_offset,
                HOUR(from_utc_timestamp(contact_start_ts, 'US/Pacific')) as hour_pt
            FROM cgan_ustax_published.ae_concierge_master_lh
            WHERE contact_start_ts IS NOT NULL AND leg_direction = 'INBOUND'
              AND customer_phone IS NOT NULL
        )
        SELECT 
            CASE DAYOFWEEK(call_date_pt)
                WHEN 1 THEN 'Sunday' WHEN 2 THEN 'Monday' WHEN 3 THEN 'Tuesday'
                WHEN 4 THEN 'Wednesday' WHEN 5 THEN 'Thursday' WHEN 6 THEN 'Friday'
                WHEN 7 THEN 'Saturday' END as day_of_week,
            CAST(hour_pt + (utc_offset + 8) AS INT) as hour_local,
            COUNT(DISTINCT cc_id) as total_calls,
            COUNT(DISTINCT call_date_pt) as num_days,
            ROUND(COUNT(DISTINCT cc_id) / COUNT(DISTINCT call_date_pt), 1) as avg_calls
        FROM calls_local
        WHERE CAST(hour_pt + (utc_offset + 8) AS INT) BETWEEN 0 AND 23
        GROUP BY 1, 2
        HAVING avg_calls > 0
        ORDER BY 
            CASE day_of_week WHEN 'Sunday' THEN 1 WHEN 'Monday' THEN 2 WHEN 'Tuesday' THEN 3
                WHEN 'Wednesday' THEN 4 WHEN 'Thursday' THEN 5 WHEN 'Friday' THEN 6 WHEN 'Saturday' THEN 7 END,
            hour_local
    """)

    cur.close()
    conn.close()
    print(f"{datetime.now():%H:%M:%S}  All queries complete. Building charts...")

    # ══════════════════════════════════════════════════════════════════
    # BUILD CHARTS
    # ══════════════════════════════════════════════════════════════════

    template = "plotly_white"
    bg_color = "#0f172a"
    paper_color = "#1e293b"
    font_color = "#e2e8f0"
    grid_color = "#334155"

    pio.templates.default = template

    common_layout = dict(
        paper_bgcolor=paper_color, plot_bgcolor=bg_color,
        font=dict(color=font_color, family="Inter, system-ui, sans-serif"),
        margin=dict(l=60, r=30, t=60, b=60),
    )

    def style_axes(fig):
        fig.update_xaxes(gridcolor=grid_color, linecolor=grid_color)
        fig.update_yaxes(gridcolor=grid_color, linecolor=grid_color)
        return fig

    # Chart 1: PT Heatmap
    days_order = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    hours_5_20 = [f"{h}:00" for h in range(5, 21)]
    z_matrix = []
    for h in range(5, 21):
        row = []
        for d in days_order:
            val = next((r["avg_calls"] for r in heatmap_rows if r["day_of_week"] == d and int(r["hour_pt"]) == h), 0)
            row.append(float(val))
        z_matrix.append(row)

    fig_heatmap = go.Figure(data=go.Heatmap(
        z=z_matrix, x=days_order, y=hours_5_20,
        colorscale=[[0, "rgb(255,255,204)"], [0.125, "rgb(255,237,160)"], [0.25, "rgb(254,217,118)"],
                     [0.375, "rgb(254,178,76)"], [0.5, "rgb(253,141,60)"], [0.625, "rgb(252,78,42)"],
                     [0.75, "rgb(227,26,28)"], [0.875, "rgb(189,0,38)"], [1.0, "rgb(128,0,38)"]],
        hovertemplate="%{x}, %{y}<br>Avg Calls: %{z:,.0f}<extra></extra>",
        colorbar=dict(title=dict(text="Avg Calls")),
    ))
    fig_heatmap.update_layout(title="Average Calls by Hour (PT) and Day of Week", height=650, width=900, **common_layout)
    style_axes(fig_heatmap)

    # Chart 2: Hourly line chart (PT)
    day_colors = {"Sunday": "#636efa", "Monday": "#ef553b", "Tuesday": "#00cc96",
                  "Wednesday": "#ab63fa", "Thursday": "#ffa15a", "Friday": "#19d3f3", "Saturday": "#ff6692"}
    fig_hourly_line = go.Figure()
    for day in days_order:
        day_data = [r for r in hourly_line if r["day_of_week"] == day]
        fig_hourly_line.add_trace(go.Scatter(
            x=[int(r["hour_pt"]) for r in day_data],
            y=[float(r["avg_calls"]) for r in day_data],
            mode="lines+markers", name=day,
            line=dict(color=day_colors.get(day, "#94a3b8")), marker=dict(size=4),
            hovertemplate=f"Day={day}<br>Hour of Day (PT)=%{{x}}<br>Avg Calls=%{{y}}<extra></extra>"
        ))
    fig_hourly_line.update_layout(
        title="Average Hourly Call Volume by Day of Week (PT)",
        xaxis_title="Hour of Day (PT)", yaxis_title="Avg Calls",
        height=500, width=1000, **common_layout,
        legend=dict(bgcolor="rgba(0,0,0,0)"),
    )
    style_axes(fig_hourly_line)

    # Chart 3: Daily volume timeline
    weekday_dates = [str(r["call_date"]) for r in daily_rows if int(r["dow"]) not in (1, 7)]
    weekday_calls = [int(r["calls"]) for r in daily_rows if int(r["dow"]) not in (1, 7)]
    weekend_dates = [str(r["call_date"]) for r in daily_rows if int(r["dow"]) in (1, 7)]
    weekend_calls = [int(r["calls"]) for r in daily_rows if int(r["dow"]) in (1, 7)]

    fig_daily = go.Figure()
    fig_daily.add_trace(go.Bar(x=weekday_dates, y=weekday_calls, name="Weekday", marker_color="#4f46e5"))
    fig_daily.add_trace(go.Bar(x=weekend_dates, y=weekend_calls, name="Weekend", marker_color="#f59e0b"))
    fig_daily.update_layout(
        title="Daily Inbound Call Volume", xaxis_title="Date", yaxis_title="Distinct Calls",
        height=450, width=1100, barmode="overlay", **common_layout,
        legend=dict(bgcolor="rgba(0,0,0,0)"),
    )
    style_axes(fig_daily)

    # Chart 4: Queue type breakdown
    queue_types = sorted(set(r["queue_type"] for r in queue_rows))
    queue_colors = ["rgb(102,194,165)", "rgb(252,141,98)", "rgb(141,160,203)", "rgb(231,138,195)",
                    "rgb(166,216,84)", "rgb(255,217,47)", "rgb(229,196,148)"]
    fig_queue = go.Figure()
    for i, qt in enumerate(queue_types):
        qt_data = [r for r in queue_rows if r["queue_type"] == qt]
        fig_queue.add_trace(go.Bar(
            x=[int(r["hour_pt"]) for r in qt_data],
            y=[int(r["total_calls"]) for r in qt_data],
            name=qt, marker_color=queue_colors[i % len(queue_colors)],
            hovertemplate=f"Type={qt}<br>Hour of Day (PT)=%{{x}}<br>Total Calls=%{{y}}<extra></extra>"
        ))
    fig_queue.update_layout(
        title="Total Calls by Queue Type and Hour (PT)", barmode="stack",
        xaxis_title="Hour of Day (PT)", yaxis_title="Total Calls",
        height=500, width=1000, **common_layout,
        legend=dict(bgcolor="rgba(0,0,0,0)"),
    )
    style_axes(fig_queue)

    # Chart 5: Timezone distribution bar
    tz_labels = [r["customer_tz"] for r in tz_rows]
    tz_values = [int(r["calls"]) for r in tz_rows]
    tz_pcts = [f"{v/total_calls*100:.1f}%" for v in tz_values]
    tz_text = [f"{v:,} ({p})" for v, p in zip(tz_values, tz_pcts)]
    tz_colors = ["#4f46e5", "#10b981", "#ef4444", "#f59e0b", "#9ca3af", "#06b6d4", "#8b5cf6"]

    fig_tz = go.Figure(go.Bar(
        x=tz_labels, y=tz_values, text=tz_text, textposition="outside",
        marker_color=tz_colors[:len(tz_labels)],
    ))
    fig_tz.update_layout(
        title="Call Volume by Customer Timezone", height=400, width=700, **common_layout,
    )
    style_axes(fig_tz)

    # Chart 6: Customer local time heatmap
    hours_0_23 = [f"{h}:00" for h in range(24)]
    z_local = []
    for h in range(24):
        row = []
        for d in days_order:
            val = next((r["avg_calls"] for r in local_heatmap if r["day_of_week"] == d and int(r["hour_local"]) == h), 0)
            row.append(float(val))
        z_local.append(row)

    fig_local_heatmap = go.Figure(data=go.Heatmap(
        z=z_local, x=days_order, y=hours_0_23,
        colorscale=[[0, "rgb(255,255,204)"], [0.125, "rgb(255,237,160)"], [0.25, "rgb(254,217,118)"],
                     [0.375, "rgb(254,178,76)"], [0.5, "rgb(253,141,60)"], [0.625, "rgb(252,78,42)"],
                     [0.75, "rgb(227,26,28)"], [0.875, "rgb(189,0,38)"], [1.0, "rgb(128,0,38)"]],
        hovertemplate="%{x}, %{y}<br>Avg Calls: %{z:,.0f}<extra></extra>",
        colorbar=dict(title=dict(text="Avg Calls")),
    ))
    fig_local_heatmap.update_layout(title="Average Calls by Customer Local Hour and Day of Week", height=650, width=900, **common_layout)
    style_axes(fig_local_heatmap)

    # ══════════════════════════════════════════════════════════════════
    # RENDER HTML
    # ══════════════════════════════════════════════════════════════════
    print(f"{datetime.now():%H:%M:%S}  Rendering HTML...")

    charts_html = ""
    for fig in [fig_heatmap, fig_hourly_line, fig_daily, fig_queue]:
        charts_html += f'<div class="chart-card">{pio.to_html(fig, full_html=False, include_plotlyjs=False)}</div>\n'

    local_charts_html = ""
    for fig in [fig_tz, fig_local_heatmap]:
        local_charts_html += f'<div class="chart-card">{pio.to_html(fig, full_html=False, include_plotlyjs=False)}</div>\n'

    plotly_js = pio.to_html(go.Figure(), full_html=False, include_plotlyjs="cdn").split("</script>")[0] + "</script>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Concierge Call Volume Dashboard</title>
{plotly_js}
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  :root {{
    --bg: #0f172a; --card: #1e293b; --border: #334155;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #6366f1;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'Inter', system-ui, sans-serif; padding: 32px; max-width: 1200px; margin: 0 auto; }}
  h1 {{ font-size: 1.75rem; margin-bottom: 4px; }}
  .subtitle {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 24px; }}
  .kpi-row {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 32px; }}
  .kpi {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px 24px; min-width: 180px; flex: 1; }}
  .kpi-label {{ color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }}
  .kpi-value {{ font-size: 1.8rem; font-weight: 700; }}
  .kpi-sub {{ color: var(--muted); font-size: 0.7rem; margin-top: 2px; }}
  .section {{ margin-bottom: 40px; }}
  .section h2 {{ font-size: 1.1rem; color: var(--accent); margin-bottom: 8px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }}
  .section-note {{ color: var(--muted); font-size: 0.8rem; margin-bottom: 16px; }}
  .chart-grid {{ display: flex; flex-wrap: wrap; gap: 24px; }}
  .chart-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px; }}
  .footer {{ margin-top: 48px; padding-top: 16px; border-top: 1px solid var(--border); color: var(--muted); font-size: 0.75rem; }}
  .methodology {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-top: 16px; font-size: 0.8rem; color: var(--muted); }}
</style>
</head>
<body>

<h1>Concierge Inbound Call Volume Dashboard</h1>
<p class="subtitle">{date_range_str} &nbsp;|&nbsp; Inbound calls on FS concierge queues &nbsp;|&nbsp; Metric: Distinct cc_id</p>

<div class="kpi-row">
  <div class="kpi">
    <div class="kpi-label">Total Calls</div>
    <div class="kpi-value">{total_calls:,}</div>
    <div class="kpi-sub">Distinct cc_id</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Avg Daily Volume</div>
    <div class="kpi-value">{avg_daily:,}</div>
    <div class="kpi-sub">{date_range_str}</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Peak Hour (PT)</div>
    <div class="kpi-value">{peak_hour_pt}:00</div>
    <div class="kpi-sub">Pacific Time</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Peak Day</div>
    <div class="kpi-value">{peak_dow}</div>
    <div class="kpi-sub">Highest total volume</div>
  </div>
</div>

<div class="section">
  <h2>Pacific Time Analysis (Source Timezone)</h2>
  <p class="section-note">All times reflect the source data timezone (PT). Shows when calls arrive at the contact center.</p>
  <div class="chart-grid">
    {charts_html}
  </div>
</div>

<div class="section">
  <h2>Customer Local Time Analysis</h2>
  <p class="section-note">Customer phone area codes are mapped to US timezones to show when customers call in <strong>their own local time</strong> — useful for planning office hours of operation.</p>
  <div class="chart-grid">
    {local_charts_html}
  </div>
  <div class="methodology">
    <strong>Methodology note:</strong> Phone area codes are mapped to US timezones (ET/CT/MT/PT). Coverage: ~99% of calls. Caveat: ~10-15% of cell users may have ported numbers from a different region. Results are approximate but reliable at aggregate level for operational planning.
  </div>
</div>

<div class="footer">
  Generated from cgan_ustax_published.ae_concierge_master_lh &nbsp;|&nbsp; {date_range_str}
</div>

</body>
</html>"""

    out_path = SCRIPT_DIR / "index.html"
    out_path.write_text(html)
    print(f"{datetime.now():%H:%M:%S}  Wrote {out_path} ({len(html):,} bytes)")

    # Push to GitHub
    subprocess.run(["git", "add", "-A"], cwd=SCRIPT_DIR, check=True)
    subprocess.run(
        ["git", "commit", "-m", f"Refresh dashboard {max_date}"],
        cwd=SCRIPT_DIR, check=True,
    )
    subprocess.run(["git", "push", "origin", "main"], cwd=SCRIPT_DIR, check=True)
    print(f"{datetime.now():%H:%M:%S}  Pushed to GitHub Pages ({max_date}).")
    print(f"{datetime.now():%H:%M:%S}  Done.")


if __name__ == "__main__":
    main()
