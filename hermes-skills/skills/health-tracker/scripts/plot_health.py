#!/opt/data/home/.venv/bin/python
"""
Построение графиков метрик здоровья.

Примеры:
  plot_health.py --metrics sugar --days 14        # Только сахар, 14 дней
  plot_health.py --metrics pressure,pulse         # Давление и пульс, 7 дней (по умолчанию)
  plot_health.py --metrics weight,sugar --days 30 # Вес и сахар, 30 дней
  plot_health.py --metrics pressure,pulse,sugar,weight --days 9999  # Все метрики за всё время

Допустимые значения: pressure, pulse, sugar, weight
"""
import sqlite3, argparse, os
from datetime import datetime, timedelta
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

DB_PATH = Path("/opt/data/personal/health/health.db")
OUTPUT_DIR = Path("/tmp")

METRIC_LABELS = {
    "pressure": "Давление, мм рт.ст.",
    "pulse": "Пульс, уд/мин",
    "sugar": "Глюкоза, ммоль/л",
    "weight": "Вес, кг",
}
METRIC_COLORS = {
    "pressure": ("#1f77b4", "#ff7f0e"),
    "pulse": ("#2ca02c",),
    "sugar": ("#d62728",),
    "weight": ("#9467bd",),
}
VALID_METRICS = ("pressure", "pulse", "sugar", "weight")


def parse_args():
    parser = argparse.ArgumentParser(description="Графики метрик здоровья")
    parser.add_argument(
        "--metrics",
        type=str,
        default="pressure,pulse,sugar,weight",
        help="Метрики через запятую: pressure, pulse, sugar, weight. По умолчанию все.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Количество последних дней (по умолчанию: 7, 9999 = всё время).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Путь для сохранения PNG (по умолчанию: /tmp/health_metrics.png).",
    )
    return parser.parse_args()


def parse_ts(ts_str):
    return datetime.strptime(ts_str.strip(), "%d.%m.%Y %H:%M:%S")


def fetch_metric(db_path, metric_type, days):
    """Возвращает (timestamps, values) для одной метрики."""
    queries = {
        "pressure": "SELECT ts, systolic, diastolic FROM metrics WHERE type='bp'",
        "pulse":    "SELECT ts, value FROM metrics WHERE type='pulse'",
        "sugar":    "SELECT ts, value FROM metrics WHERE type='glucose'",
        "weight":   "SELECT ts, value FROM metrics WHERE type='weight'",
    }
    if metric_type not in queries:
        return None

    cutoff = datetime.now() - timedelta(days=days)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(queries[metric_type])
    rows = cur.fetchall()
    conn.close()

    result = []
    for row in rows:
        try:
            ts = parse_ts(row[0])
            if ts >= cutoff:
                result.append(row)
        except (ValueError, IndexError):
            continue

    result.sort(key=lambda r: parse_ts(r[0]))
    return result


def plot_metric(ax, data, metric_type):
    """Рисует одну метрику на заданном axes."""
    if not data:
        ax.set_title(f"{METRIC_LABELS[metric_type]} — нет данных")
        return

    color = METRIC_COLORS[metric_type]

    if metric_type == "pressure":
        ts_list = [parse_ts(r[0]) for r in data]
        systolic = [r[1] for r in data]
        diastolic = [r[2] for r in data]
        ax.plot(ts_list, systolic,  label="Систолическое", color=color[0], marker="o", markersize=4)
        ax.plot(ts_list, diastolic, label="Диастолическое", color=color[1], marker="o", markersize=4, linestyle="--")
        ax.legend(fontsize=8)
    else:
        ts_list = [parse_ts(r[0]) for r in data]
        values = [r[1] for r in data]
        ax.plot(ts_list, values, color=color[0], marker="o", markersize=4, label=METRIC_LABELS[metric_type])
        ax.legend(fontsize=8)

    ax.set_ylabel(METRIC_LABELS[metric_type], fontsize=9)
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, fontsize=7)


def main():
    args = parse_args()

    # Валидация метрик
    requested = [m.strip() for m in args.metrics.split(",")]
    metrics = []
    for m in requested:
        if m in VALID_METRICS:
            metrics.append(m)
        else:
            print(f"⚠️  Неизвестная метрика '{m}', пропускаю. Допустимые: {', '.join(VALID_METRICS)}")

    if not metrics:
        print(f"❌ Нет валидных метрик. Допустимые: {', '.join(VALID_METRICS)}")
        return

    if not DB_PATH.exists():
        print(f"❌ БД не найдена: {DB_PATH}")
        return

    # Загрузка данных
    data = {}
    for m in metrics:
        data[m] = fetch_metric(DB_PATH, m, args.days)

    # Определяем количество subplots: давление занимает 1, остальные по 1
    n = len(metrics)
    fig, axes = plt.subplots(n, 1, figsize=(14, 4 * n), squeeze=False)
    period_label = "за всё время" if args.days >= 9999 else f"последние {args.days} дн."
    fig.suptitle(f"Метрики здоровья ({period_label})", fontsize=14, y=1.01)

    for i, m in enumerate(metrics):
        plot_metric(axes[i, 0], data.get(m), m)

    # На нижнем subplot подписываем ось X
    axes[-1, 0].set_xlabel("Дата", fontsize=9)

    plt.tight_layout()

    output = Path(args.output) if args.output else OUTPUT_DIR / "health_metrics.png"
    plt.savefig(output, dpi=150, bbox_inches="tight")
    print(f"✅ График сохранён: {output}")


if __name__ == "__main__":
    main()
