import sys
from datetime import date, datetime, timedelta

import waterlabel_lib_en as wl

if __name__ == "__main__":
    target = date.today() - timedelta(days=1)
    if len(sys.argv) > 1:
        target = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()

    wl.log.info("=== daily run (EN) for %s ===", target)
    records = wl.scrape_daily(target)
    if records:
        wl.save_csv(records, f"waterlabel_daily_en_{target:%Y%m%d}.csv", mode="w")
        wl.write_feishu(records)
    wl.log.info("=== done ===")
