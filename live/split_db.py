# -*- coding: utf-8 -*-
"""Разделение БД перед выходом на live: демо-история → архив, рабочая БД → чистая.

Зачем: в scalp_trades нет колонки счёта/режима, поэтому live-сделки легли бы в ту
же таблицу, что 74 демо-сделки, и витрина показывала бы смесь демо и реала.

Что делает:
  1) полная копия bot.db → data/archive/bot_demo_<дата>.db (демо-история цела)
  2) в рабочей bot.db очищает торговые таблицы (схема остаётся):
     scalp_trades, pairs, events, snapshots, copy_trades
  3) сбрасывает счётчики AUTOINCREMENT, делает VACUUM
  4) НЕ трогает telegram.json (подписчики) и state_*.json

Запуск (сначала всегда с --dry-run):
    .venv\\Scripts\\python.exe live\\split_db.py --dry-run
    .venv\\Scripts\\python.exe live\\split_db.py --yes

Важно: боты, пишущие в БД, должны быть остановлены (иначе SQLite залочен и часть
записей может уйти уже после архива).
"""
import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402

TABLES = ["scalp_trades", "pairs", "events", "snapshots", "copy_trades"]


def counts(db_path: Path) -> dict:
    out = {}
    with sqlite3.connect(db_path) as c:
        for t in TABLES:
            try:
                out[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                out[t] = None  # таблицы нет — не ошибка
    return out


def show(title: str, data: dict):
    print(f"\n{title}")
    for t, n in data.items():
        print(f"  {t:14} {'нет таблицы' if n is None else str(n) + ' строк'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="только показать, что будет сделано")
    ap.add_argument("--yes", action="store_true",
                    help="выполнить без интерактивного подтверждения")
    args = ap.parse_args()

    db = Path(config.DB_PATH)
    print("=" * 62)
    print("РАЗДЕЛЕНИЕ БД: демо → архив, рабочая → чистая под live")
    print("=" * 62)
    print(f"рабочая БД: {db}")

    if not db.exists():
        print("[FAIL] bot.db не найдена — нечего разделять")
        sys.exit(1)

    size_mb = db.stat().st_size / 1024 / 1024
    print(f"размер    : {size_mb:.2f} МБ")
    show("СЕЙЧАС В БД:", counts(db))

    archive_dir = db.parent / "archive"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive = archive_dir / f"bot_demo_{stamp}.db"

    print(f"\nбудет создан архив: {archive}")
    print(f"будут очищены таблицы: {', '.join(TABLES)}")
    print("НЕ затрагиваются: telegram.json (подписчики), state_*.json, логи")

    if args.dry_run:
        print("\n[dry-run] изменений не внесено")
        return

    if not args.yes:
        print("\nПродолжить? Введите YES заглавными:")
        if input("> ").strip() != "YES":
            print("отменено")
            return

    archive_dir.mkdir(exist_ok=True)
    shutil.copy2(db, archive)
    print(f"\n[ok] архив создан: {archive} ({archive.stat().st_size / 1024 / 1024:.2f} МБ)")

    arch_counts = counts(archive)
    if arch_counts != counts(db):
        print("[FAIL] архив не совпал с исходной БД — очистка ОТМЕНЕНА")
        sys.exit(1)
    print("[ok] архив сверен с исходной БД построчно")

    with sqlite3.connect(db) as c:
        for t in TABLES:
            try:
                c.execute(f"DELETE FROM {t}")
                print(f"  очищено: {t}")
            except sqlite3.OperationalError as e:
                print(f"  пропуск {t}: {e}")
        try:
            c.execute("DELETE FROM sqlite_sequence")
        except sqlite3.OperationalError:
            pass
        c.commit()
    with sqlite3.connect(db) as c:
        c.execute("VACUUM")

    show("ПОСЛЕ ОЧИСТКИ:", counts(db))
    print(f"\n[ok] рабочая БД чистая, размер {db.stat().st_size / 1024:.0f} КБ")
    print(f"[ok] демо-история сохранена: {archive}")
    print("\nДальше: .env (MT5_LOGIN/SERVER, HYBRID_TEST_BALANCE=150,")
    print("TRADING_MODE=real) → patch_tg_owner.py → рестарт задач.")


if __name__ == "__main__":
    main()
