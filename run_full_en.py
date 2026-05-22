from datetime import date

import waterlabel_lib_en as wl

if __name__ == "__main__":
    wl.log.info("=== full run (EN) ===")
    records = wl.scrape_full()
    path = f"waterlabel_full_en_{date.today():%Y%m%d}.csv"
    wl.save_csv(records, path, mode="w")
    wl.write_feishu(records)
    wl.log.info("=== done ===")
