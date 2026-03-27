"""
🔧 utils.py — Yardımcı Fonksiyonlar
Log, config okuma, tarih işlemleri, kayıt kontrolü.
"""

import json
import os
from datetime import datetime, timezone, timedelta


# Türkiye saat dilimi
TR_TIMEZONE = timezone(timedelta(hours=3))


def log(message):
    """Zaman damgalı log yazdırır."""
    now = datetime.now(TR_TIMEZONE).strftime("%H:%M:%S")
    print(f"[{now}] {message}")


def load_config(name):
    """Config dosyasını yükler. name: 'sources', 'settings', vs."""
    path = f"config/{name}.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        log(f"⚠️ Config dosyası bulunamadı: {path}")
        return {}
    except json.JSONDecodeError as e:
        log(f"⚠️ Config parse hatası ({path}): {e}")
        return {}


def load_posted_data():
    """Paylaşılmış haber verilerini yükler."""
    path = "data/posted_news.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"posts": [], "daily_counts": {}}


def save_posted_data(data):
    """Paylaşılmış haber verilerini kaydeder."""
    path = "data/posted_news.json"
    os.makedirs("data", exist_ok=True)

    # Eski kayıtları temizle (500+ kayıt birikirse)
    if len(data.get("posts", [])) > 500:
        data["posts"] = data["posts"][-300:]
        log("🧹 Eski kayıtlar temizlendi (500+ → 300)")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_already_posted(link):
    """Bu link daha önce paylaşılmış mı?"""
    if not link:
        return False
    data = load_posted_data()
    posted_links = [p.get("link", "") for p in data.get("posts", [])]
    return link in posted_links


def record_post(news_item, score):
    """Paylaşılan haberi kaydet."""
    data = load_posted_data()
    today = datetime.now(TR_TIMEZONE).strftime("%Y-%m-%d")

    data["posts"].append({
        "title": news_item.get("title", "")[:100],
        "link": news_item.get("link", ""),
        "score": score,
        "source": news_item.get("source", ""),
        "posted_at": datetime.now(TR_TIMEZONE).isoformat()
    })

    daily = data.get("daily_counts", {})
    daily[today] = daily.get(today, 0) + 1
    data["daily_counts"] = daily

    save_posted_data(data)
    log(f"📝 Kayıt eklendi (bugün toplam: {daily[today]} post)")


def get_today_post_count():
    """Bugün kaç post yapılmış?"""
    data = load_posted_data()
    today = datetime.now(TR_TIMEZONE).strftime("%Y-%m-%d")
    return data.get("daily_counts", {}).get(today, 0)


def can_post_now():
    """Şu an post yapılabilir mi? (Sadece günlük limit kontrolü)"""
    settings = load_config("settings")
    max_daily = settings.get("max_daily_posts", 7)

    today_count = get_today_post_count()
    if today_count >= max_daily:
        log(f"⛔ Günlük limit doldu ({today_count}/{max_daily})")
        return False

    log(f"✅ Post yapılabilir (bugün {today_count}/{max_daily})")
    return True
