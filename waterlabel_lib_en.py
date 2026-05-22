import csv
import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Optional

import requests

CONFIG = {
    "feishu_app_id":     os.environ["FEISHU_APP_ID"],
    "feishu_app_secret": os.environ["FEISHU_APP_SECRET"],
    "bitable_app_token": os.environ["BITABLE_APP_TOKEN"],
    "bitable_table_id":  os.environ["BITABLE_TABLE_ID"],
    "request_delay_seconds": 0.5,
    "request_timeout": (10, 30),
    "request_retries": 4,
    "retry_backoff_seconds": 3,
    "page_size": 10,
    "mark": 840,
    "is_old": 1,
}

PRODUCT_PROFILES = [
    {"product_type": "03", "product_type_label": "洗碗机", "max_pages": 50},
    {"product_type": "96", "product_type_label": "洗碗机2025版", "max_pages": 20},
]

CSV_FIELDS = [
    "Product Type", "Model", "Manufacturer", "Registration No.",
    "Water Efficiency Grade", "Announcement Date", "Standard",
    "WEI", "EEI", "Water Consumption (L)", "Energy Consumption (kWh/cycle)",
    "Drying Index PD", "Cleaning Index PC", "Capacity (place settings)",
    "Noise dB(A)",
]

NUMBER_FIELDS = {
    "Water Efficiency Grade", "WEI", "EEI", "Water Consumption (L)",
    "Energy Consumption (kWh/cycle)", "Drying Index PD", "Cleaning Index PC",
    "Capacity (place settings)", "Noise dB(A)",
}

DATE_FIELDS = {"Announcement Date"}

BASE_URL = "https://www.waterlabel.org.cn/admin-api/gateway/productRegistration"
LIST_URL = f"{BASE_URL}/productRegistrationList"
DETAIL_URL = f"{BASE_URL}/productDetailById"
FEISHU_API = "https://open.feishu.cn/open-apis"

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json;charset=UTF-8",
    "origin": "https://www.waterlabel.org.cn",
    "referer": "https://www.waterlabel.org.cn/productFiling",
    "tenant-id": "1",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    ),
}

_tenant_token: Optional[str] = None
_token_expires = 0.0


def setup_logging(log_name: str = "scraper_en.log") -> logging.Logger:
    logger = logging.getLogger("waterlabel_en")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    for h in (logging.StreamHandler(), logging.FileHandler(log_name, encoding="utf-8")):
        h.setFormatter(fmt)
        logger.addHandler(h)
    return logger


log = setup_logging()


def normalize_product_type(raw: str, default: str) -> str:
    if not raw:
        return default
    compact = raw.replace(" ", "").replace("\u3000", "")
    if "洗碗机" in compact and "2025" in compact:
        return "洗碗机2025版"
    if compact == "洗碗机":
        return "洗碗机"
    return default


def parse_announcement_date(value) -> Optional[date]:
    if not value:
        return None
    text = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _post_json(url: str, payload: dict, label: str) -> dict:
    timeout = CONFIG["request_timeout"]
    retries = CONFIG["request_retries"]
    backoff = CONFIG["retry_backoff_seconds"]
    last_err = None
    for attempt in range(1, retries + 2):
        try:
            resp = requests.post(url, json=payload, headers=HEADERS, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as err:
            last_err = err
            if attempt <= retries:
                wait = backoff * attempt
                log.warning("%s failed (%s/%s): %s; retry in %ss", label, attempt, retries + 1, err, wait)
                time.sleep(wait)
    raise last_err


def fetch_list_page(profile: dict, page: int) -> dict:
    payload = {
        "mark": CONFIG["mark"],
        "productType": profile["product_type"],
        "productModel": "",
        "registrationNumber": "",
        "producerName": "",
        "current": page,
        "isOld": CONFIG["is_old"],
        "pageSize": CONFIG["page_size"],
    }
    data = _post_json(LIST_URL, payload, f"list p{page}")
    if data.get("code") != 200:
        raise RuntimeError(f"list API error: {data}")
    return data["data"]


def fetch_detail(profile: dict, product_id: int) -> dict:
    payload = {
        "productId": product_id,
        "productTypeCode": profile["product_type"],
        "mark": CONFIG["mark"],
        "isOld": CONFIG["is_old"],
    }
    data = _post_json(DETAIL_URL, payload, f"detail id={product_id}")
    if data.get("code") != 200:
        raise RuntimeError(f"detail API error: {data}")
    return data["data"]


def _metrics_dict(detail: dict) -> dict:
    out = {}
    for item in detail.get("list", []):
        name = item.get("name", "")
        value = item.get("value", "")
        out[name] = value
        compact = name.replace(" ", "")
        if compact != name:
            out[compact] = value
    return out


def _metric(metrics: dict, *names: str) -> str:
    for name in names:
        if metrics.get(name) not in (None, ""):
            return metrics[name]
        compact = name.replace(" ", "")
        if metrics.get(compact) not in (None, ""):
            return metrics[compact]
    return ""


def _blank_row(profile: dict, item: dict | None = None) -> dict:
    label = profile["product_type_label"]
    row = {f: "" for f in CSV_FIELDS}
    row["Product Type"] = label
    if item:
        row.update({
            "Product Type": normalize_product_type(item.get("productType", ""), label),
            "Model": item.get("productModel", ""),
            "Manufacturer": item.get("producerName", ""),
            "Registration No.": item.get("registrationNumber", ""),
            "Water Efficiency Grade": item.get("nxLever", ""),
            "Announcement Date": item.get("announcementTime", ""),
        })
    return row


def parse_detail(detail: dict, profile: dict) -> dict:
    metrics = _metrics_dict(detail)
    label = profile["product_type_label"]
    ptype = normalize_product_type(detail.get("productType", ""), label)
    is_2025 = profile["product_type"] == "96"

    row = {
        "Product Type": ptype,
        "Model": detail.get("productModel", ""),
        "Manufacturer": detail.get("producerName", ""),
        "Registration No.": detail.get("registrationNumber", ""),
        "Water Efficiency Grade": detail.get("nxLever", ""),
        "Announcement Date": detail.get("announcementTime", ""),
        "Standard": detail.get("standard", ""),
        "WEI": _metric(metrics, "水效指数WEI", "水效指数 WEI"),
        "EEI": _metric(metrics, "能效指数EEI", "能效指数 EEI"),
        "Drying Index PD": _metric(metrics, "干燥指数PD", "干燥指数 PD"),
        "Cleaning Index PC": _metric(metrics, "清洁指数PC", "清洁指数 PC"),
        "Noise dB(A)": "",
    }
    if is_2025:
        row["Water Consumption (L)"] = _metric(
            metrics, "标准洗涤程序用水量(L)", "标准洗涤程序用水量（L）"
        )
        row["Energy Consumption (kWh/cycle)"] = _metric(
            metrics,
            "标准洗涤程序耗电量(kW·h)",
            "标准洗涤程序耗电量(kw·h)",
            "标准洗涤程序耗电量（kW·h）",
        )
        row["Capacity (place settings)"] = _metric(metrics, "额定容量（套）", "容量（套）")
        row["Noise dB(A)"] = _metric(metrics, "噪声(dB(A)）", "噪声(dB(A))")
    else:
        row["Water Consumption (L)"] = _metric(metrics, "工作周期用水量（L）")
        row["Energy Consumption (kWh/cycle)"] = _metric(
            metrics, "工作周期耗电量（kw·h/cycle）", "工作周期耗电量(kw·h/cycle)"
        )
        row["Capacity (place settings)"] = _metric(metrics, "容量（套）")
    return row


def _items_to_records(profile: dict, items: list[dict]) -> list[dict]:
    records = []
    label = profile["product_type_label"]
    for i, item in enumerate(items, 1):
        try:
            time.sleep(CONFIG["request_delay_seconds"])
            detail = fetch_detail(profile, item["id"])
            records.append(parse_detail(detail, profile))
        except Exception as err:
            log.warning("[%s] detail %s/%s id=%s: %s", label, i, len(items), item.get("id"), err)
            records.append(_blank_row(profile, item))
        if i % 50 == 0 or i == len(items):
            log.info("[%s] details %s/%s", label, i, len(items))
    return records


def collect_list_items(profile: dict) -> list[dict]:
    label = profile["product_type_label"]
    first = fetch_list_page(profile, 1)
    total = first["total"]
    total_pages = min(
        (total + CONFIG["page_size"] - 1) // CONFIG["page_size"],
        profile["max_pages"],
    )
    log.info("[%s] total=%s pages=%s", label, total, total_pages)
    items = list(first["list"])
    for page in range(2, total_pages + 1):
        time.sleep(CONFIG["request_delay_seconds"])
        items.extend(fetch_list_page(profile, page)["list"])
        log.info("[%s] list page %s/%s count=%s", label, page, total_pages, len(items))
    return items


def collect_items_by_date(profile: dict, target: date) -> list[dict]:
    label = profile["product_type_label"]
    matched = []
    page = 1
    while page <= profile["max_pages"]:
        time.sleep(CONFIG["request_delay_seconds"])
        batch = fetch_list_page(profile, page)["list"]
        if not batch:
            break
        dates = []
        for item in batch:
            ad = parse_announcement_date(item.get("announcementTime"))
            if ad:
                dates.append(ad)
            if ad == target:
                matched.append(item)
        if dates and max(dates) < target:
            break
        if dates and min(dates) < target and not any(d == target for d in dates):
            break
        page += 1
    log.info("[%s] %s rows on %s", label, len(matched), target)
    return matched


def scrape_full() -> list[dict]:
    records = []
    for profile in PRODUCT_PROFILES:
        items = collect_list_items(profile)
        records.extend(_items_to_records(profile, items))
    log.info("full scrape done: %s rows", len(records))
    return records


def scrape_daily(target: date | None = None) -> list[dict]:
    target = target or (date.today() - timedelta(days=1))
    log.info("daily scrape for: %s", target)
    records = []
    for profile in PRODUCT_PROFILES:
        items = collect_items_by_date(profile, target)
        if items:
            records.extend(_items_to_records(profile, items))
    return records


def save_csv(records: list[dict], path: str, mode: str = "w") -> None:
    if not records:
        return
    write_header = mode == "w" or not os.path.exists(path)
    with open(path, mode=mode, newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(records)
    log.info("csv %s (%s rows)", path, len(records))


def load_csv(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rec = dict(row)
            if "Noise dB(A)" not in rec:
                rec["Noise dB(A)"] = ""
            rows.append(rec)
    return rows


def _to_number(value) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _to_date_ms(value) -> Optional[int]:
    if value in (None, ""):
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return int(datetime.strptime(str(value).strip()[:19], fmt).timestamp() * 1000)
        except ValueError:
            continue
    return None


def prepare_feishu_row(record: dict) -> dict:
    fields = {}
    for key, value in record.items():
        if key in NUMBER_FIELDS:
            num = _to_number(value)
            if num is not None:
                fields[key] = int(num) if num.is_integer() else num
        elif key in DATE_FIELDS:
            ms = _to_date_ms(value)
            if ms is not None:
                fields[key] = ms
        elif value not in (None, ""):
            fields[key] = value
    return fields


def _feishu_token() -> str:
    global _tenant_token, _token_expires
    if _tenant_token and time.time() < _token_expires:
        return _tenant_token
    resp = requests.post(
        f"{FEISHU_API}/auth/v3/tenant_access_token/internal",
        json={"app_id": CONFIG["feishu_app_id"], "app_secret": CONFIG["feishu_app_secret"]},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"feishu auth failed: {data}")
    _tenant_token = data["tenant_access_token"]
    _token_expires = time.time() + data.get("expire", 7200) - 300
    return _tenant_token


def write_feishu(records: list[dict], batch_size: int = 500) -> int:
    if not records:
        return 0
    app = CONFIG["bitable_app_token"]
    table = CONFIG["bitable_table_id"]
    url = f"{FEISHU_API}/bitable/v1/apps/{app}/tables/{table}/records/batch_create"
    headers = {"Authorization": f"Bearer {_feishu_token()}", "Content-Type": "application/json"}
    written = 0
    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        payload = {"records": [{"fields": prepare_feishu_row(r)} for r in batch]}
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            log.error("feishu batch %s: %s", i // batch_size + 1, data)
        else:
            written += len(data["data"]["records"])
        time.sleep(0.3)
    log.info("feishu written: %s", written)
    return written


def latest_full_csv() -> str | None:
    files = sorted(
        (f for f in os.listdir(".") if f.startswith("waterlabel_full_en_") and f.endswith(".csv")),
        reverse=True,
    )
    return files[0] if files else None
