#!/usr/bin/env python3
"""Generates 2-month health review chart with target green channels."""

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from health_monitoring import fmt_range, load_monitoring

CHARTS_DIR = Path("/opt/data/cache/charts")
DEFAULT_CHART_PATH = CHARTS_DIR / "health_weekly_2m.png"
DB_PATH = Path("/opt/data/personal/health/health.db")

def parse_row_datetime(ts_str, date_str):
    if ts_str:
        s = ts_str.strip()
        for fmt in (
            "%d.%m.%Y %H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%d.%m.%Y %H:%M"
        ):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                pass
    if date_str:
        try:
            return datetime.strptime(date_str.strip(), "%Y-%m-%d")
        except ValueError:
            pass
    return None

def generate_weekly_chart(db_path: Path = DB_PATH, output_path: Path = DEFAULT_CHART_PATH, days: int = 60) -> str:
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    if not db_path.exists():
        return ""

    cutoff = datetime.now() - timedelta(days=days)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 1. Glucose
    cur.execute("SELECT ts, value, date FROM metrics WHERE type = 'glucose' AND value > 0")
    gluc_rows = []
    for ts, val, dt in cur.fetchall():
        t = parse_row_datetime(ts, dt)
        if t and t >= cutoff:
            gluc_rows.append((t, val))
    gluc_rows.sort(key=lambda x: x[0])

    # 2. Pressure (in DB type is 'bp' or 'pressure')
    cur.execute("SELECT ts, systolic, diastolic, date FROM metrics WHERE type IN ('bp', 'pressure') AND systolic IS NOT NULL")
    bp_rows = []
    for ts, sys_v, dia_v, dt in cur.fetchall():
        t = parse_row_datetime(ts, dt)
        if t and t >= cutoff:
            bp_rows.append((t, sys_v, dia_v))
    bp_rows.sort(key=lambda x: x[0])

    # 3. Weight
    cur.execute("SELECT ts, value, date FROM metrics WHERE type = 'weight' AND value > 0")
    w_rows = []
    for ts, val, dt in cur.fetchall():
        t = parse_row_datetime(ts, dt)
        if t and t >= cutoff:
            w_rows.append((t, val))
    w_rows.sort(key=lambda x: x[0])

    conn.close()
    if not (gluc_rows or bp_rows or w_rows):
        return ""  # замеров за период нет — пустой график не отправляем
    mon = load_monitoring()

    # Create figure
    fig, axes = plt.subplots(3, 1, figsize=(11, 9.5), sharex=True)
    plt.subplots_adjust(hspace=0.28)

    # Subplot 1: Weight
    ax_w = axes[0]
    if w_rows:
        dates_w = [r[0] for r in w_rows]
        vals_w = [r[1] for r in w_rows]
        ax_w.plot(dates_w, vals_w, color="#8b5cf6", marker="o", markersize=5, linewidth=2, label="Вес (кг)")
        corridor = mon["weight_corridor"]
        if corridor:
            ax_w.axhspan(corridor[0], corridor[1], color="#10b981", alpha=0.18,
                         label=f"Рабочий коридор ({fmt_range(corridor, 'кг', '–')})")
        ax_w.set_ylabel("Вес, кг", fontsize=9, fontweight="bold")
        title = "Динамика веса"
        if mon["weight_start"] and mon["weight_target"]:
            title += f" (с {mon['weight_start']:g} до {mon['weight_target']:g} кг)"
        ax_w.set_title(title, fontsize=11, fontweight="bold")
        ax_w.legend(loc="upper right", fontsize=8)
        ax_w.grid(True, linestyle=":", alpha=0.6)
    else:
        ax_w.set_title("Динамика веса — за период замеров нет", fontsize=10)

    # Subplot 2: Glucose
    ax_g = axes[1]
    if gluc_rows:
        dates_g = [r[0] for r in gluc_rows]
        vals_g = [r[1] for r in gluc_rows]
        ax_g.plot(dates_g, vals_g, color="#ef4444", marker="s", markersize=4, linewidth=1.8, label="Глюкоза (ммоль/л)")
        if mon["glucose_range"]:
            g = mon["glucose_range"]
            ax_g.axhspan(g[0], g[1], color="#10b981", alpha=0.22,
                         label=f"Целевой коридор: {fmt_range(g, 'ммоль/л', '–')}")
        ax_g.set_ylabel("Глюкоза, ммоль/л", fontsize=9, fontweight="bold")
        ax_g.set_title("Сахар крови натощак", fontsize=11, fontweight="bold")
        ax_g.legend(loc="upper right", fontsize=8)
        ax_g.grid(True, linestyle=":", alpha=0.6)
    else:
        ax_g.set_title("Сахар крови — за период замеров нет", fontsize=10)

    # Subplot 3: Pressure
    ax_p = axes[2]
    if bp_rows:
        dates_p = [r[0] for r in bp_rows]
        sys_vals = [r[1] for r in bp_rows]
        dia_vals = [r[2] for r in bp_rows]
        ax_p.plot(dates_p, sys_vals, color="#2563eb", marker="o", markersize=4, label="Систолическое АД")
        ax_p.plot(dates_p, dia_vals, color="#0284c7", marker="o", markersize=4, linestyle="--", label="Диастолическое АД")
        if mon["bp_systolic_range"]:
            r = mon["bp_systolic_range"]
            ax_p.axhspan(r[0], r[1], color="#10b981", alpha=0.15,
                         label=f"Цель систолического ({fmt_range(r, '', '–')})")
        if mon["bp_diastolic_range"]:
            r = mon["bp_diastolic_range"]
            ax_p.axhspan(r[0], r[1], color="#059669", alpha=0.15,
                         label=f"Цель диастолического ({fmt_range(r, '', '–')})")
        ax_p.set_ylabel("АД, мм рт. ст.", fontsize=9, fontweight="bold")
        ax_p.set_title("Артериальное давление", fontsize=11, fontweight="bold")
        ax_p.legend(loc="upper right", fontsize=8)
        ax_p.grid(True, linestyle=":", alpha=0.6)
    else:
        ax_p.set_title("Давление — за период замеров нет", fontsize=10)

    # Format Date Axis
    ax_p.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    ax_p.xaxis.set_major_locator(mdates.DayLocator(interval=5))
    plt.setp(ax_p.xaxis.get_majorticklabels(), rotation=30, fontsize=8)

    fig.suptitle("Мониторинг здоровья: динамика за 2 месяца", fontsize=13, fontweight="bold", y=0.99)
    plt.tight_layout()
    plt.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close()

    return str(output_path)

if __name__ == "__main__":
    out = generate_weekly_chart()
    print("Generated chart:", out)
