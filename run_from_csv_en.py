import sys

import waterlabel_lib_en as wl

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else wl.latest_full_csv()
    if not path:
        print("Usage: python run_from_csv_en.py [csv_path]")
        raise SystemExit(1)
    records = wl.load_csv(path)
    wl.write_feishu(records)
