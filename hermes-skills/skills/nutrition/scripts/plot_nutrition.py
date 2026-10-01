#!/opt/data/home/.venv/bin/python
"""
Построение графиков питания.

Примеры:
  plot_nutrition.py --days 7              # Калории по приёмам пищи за 7 дней
  plot_nutrition.py --days 30 --type time # Распределение калорий по часам дня
  plot_nutrition.py --days 14 --type macro # БЖУ по дням
  plot_nutrition.py --days 9999           # Всё время

Типы графиков:
  meal  — калории по приёмам пищи по дням (stacked bars)
  time  — распределение приёмов пищи по часам дня (scatter)
  macro — БЖУ по дням (stacked area)
"""
import sqlite3, argparse
from datetime import datetime, timedelta
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np

DB_PATH = Path("/opt/data/personal/nutrition/nutrition.db")
OUTPUT_DIR = Path("/tmp")

MEAL_LABELS = {
    "breakfast": "Завтрак",
    "lunch": "Обед",
    "dinner": "Ужин",
    "snack": "Перекус",
}
MEAL_COLORS = {
    "breakfast": "#ff9999",
    "lunch": "#66b3ff",
    "dinner": "#99ff99",
    "snack": "#ffcc99",
}

TS_FORMAT = "%d.%m.%Y %H:%M:%S"


def parse_args():
    parser = argparse.ArgumentParser(description="Графики питания")
    parser.add_argument("--days", type=int, default=7,
                        help="Количество последних дней (по умолчанию: 7, 9999 = всё время).")
    parser.add_argument("--type", type=str, default="meal",
                        choices=["day", "meal", "time", "macro"],
                        help="Тип графика: meal (day — то же самое), time, macro. По умолчанию: meal.")
    parser.add_argument("--output", type=str, default=None,
                        help="Путь для сохранения PNG.")
    return parser.parse_args()


def parse_ts(ts_str):
    return datetime.strptime(ts_str.strip(), TS_FORMAT)


def fetch_nutrition(db_path, days):
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT date, ts, meal, total_kcal, total_protein, total_fat, total_carbs "
        "FROM nutrition WHERE date >= ? ORDER BY ts",
        (cutoff,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def plot_meal(ax, rows):
    daily_meals = {}
    for r in rows:
        d = r["date"]
        m = r["meal"]
        if d not in daily_meals:
            daily_meals[d] = {}
        daily_meals[d][m] = daily_meals[d].get(m, 0) + (r["total_kcal"] or 0)

    if not daily_meals:
        ax.set_title("Нет данных")
        return

    dates = sorted(daily_meals.keys())
    x = [datetime.strptime(d, "%Y-%m-%d") for d in dates]
    meals_order = ["breakfast", "lunch", "dinner", "snack"]

    bottom = np.zeros(len(dates))
    for meal in meals_order:
        values = [daily_meals[d].get(meal, 0) for d in dates]
        if any(v > 0 for v in values):
            ax.bar(x, values, bottom=bottom, label=MEAL_LABELS[meal],
                   color=MEAL_COLORS[meal], alpha=0.85, width=0.8)
            bottom += np.array(values)

    ax.set_ylabel("Калории, ккал", fontsize=9)
    ax.set_title("Калории по приёмам пищи", fontsize=11)
    ax.legend(fontsize=7, loc="upper left")
    ax.grid(True, linestyle=":", alpha=0.4, axis="y")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, fontsize=7)


def plot_time(ax, rows):
    if not rows:
        ax.set_title("Нет данных")
        return

    all_dates = sorted(set(r["date"] for r in rows))
    date_to_y = {d: i for i, d in enumerate(all_dates)}

    for meal in ["breakfast", "lunch", "dinner", "snack"]:
        meal_rows = [r for r in rows if r["meal"] == meal]
        if not meal_rows:
            continue
        hours = [parse_ts(r["ts"]).hour + parse_ts(r["ts"]).minute / 60.0 for r in meal_rows]
        kcal = [r["total_kcal"] or 0 for r in meal_rows]
        y = [date_to_y[r["date"]] for r in meal_rows]
        ax.scatter(hours, y, s=[max(30, k / 3) for k in kcal],
                   label=MEAL_LABELS[meal], color=MEAL_COLORS[meal],
                   alpha=0.7, edgecolors="white", linewidth=0.5)

    ax.set_xlabel("Час дня", fontsize=9)
    ax.set_yticks(range(len(all_dates)))
    ax.set_yticklabels([d[5:].replace("-", ".") for d in all_dates], fontsize=7)
    ax.set_title("Время приёмов пищи (размер = калории)", fontsize=11)
    ax.set_xlim(0, 24)
    ax.set_xticks(range(0, 25, 2))
    ax.legend(fontsize=7)
    ax.grid(True, linestyle=":", alpha=0.3)


def plot_macro(ax, rows):
    daily = {}
    for r in rows:
        d = r["date"]
        if d not in daily:
            daily[d] = {"p": 0, "f": 0, "c": 0}
        daily[d]["p"] += r["total_protein"] or 0
        daily[d]["f"] += r["total_fat"] or 0
        daily[d]["c"] += r["total_carbs"] or 0

    if not daily:
        ax.set_title("Нет данных")
        return

    dates = sorted(daily.keys())
    x = [datetime.strptime(d, "%Y-%m-%d") for d in dates]
    p = [daily[d]["p"] for d in dates]
    f = [daily[d]["f"] for d in dates]
    c = [daily[d]["c"] for d in dates]

    ax.stackplot(x, p, f, c,
                 labels=["Белки", "Жиры", "Углеводы"],
                 colors=["#ff9999", "#ffcc99", "#99ff99"],
                 alpha=0.8)
    ax.set_ylabel("Граммы", fontsize=9)
    ax.set_title("БЖУ по дням", fontsize=11)
    ax.legend(fontsize=7, loc="upper left")
    ax.grid(True, linestyle=":", alpha=0.4, axis="y")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, fontsize=7)


def main():
    args = parse_args()

    if not DB_PATH.exists():
        print(f"❌ БД не найдена: {DB_PATH}")
        return

    rows = fetch_nutrition(DB_PATH, args.days)

    if not rows:
        print("⚠️ Нет данных за указанный период.")
        return

    fig, ax = plt.subplots(1, 1, figsize=(14, 5))

    plot_funcs = {"day": plot_meal, "meal": plot_meal, "time": plot_time, "macro": plot_macro}
    plot_funcs[args.type](ax, rows)

    fig.suptitle(f"Питание (последние {args.days} дн.)", fontsize=13, y=1.02)
    plt.tight_layout()

    default_output = OUTPUT_DIR / f"nutrition_{args.type}.png"
    output = Path(args.output) if args.output else default_output
    plt.savefig(output, dpi=150, bbox_inches="tight")
    print(f"✅ График сохранён: {output}")


if __name__ == "__main__":
    main()
