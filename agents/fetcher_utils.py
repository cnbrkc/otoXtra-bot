"""
agents/fetcher_utils.py - Fetcher Yardımcı Fonksiyonları ve URL İşlemleri
Tip dönüşümleri, URL doğrulama, Nitter/Twitter CDN çözümleme ve HTTP istekleri burada.
"""
import os
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional, Tuple, List
import requests
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse, unquote
from core.logger import log

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_PRIORITY_ORDER = {"high": 3, "medium": 2, "low": 1}
_TREND_BONUSES = [(5, 15), (3, 10), (2, 5)]
_TREND_FINGERPRINT_THRESHOLD = 0.70

_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif")
_DISALLOWED_IMAGE_EXTENSIONS = (".svg", ".ico")
_IMAGE_NOISE_HINTS = (
    "logo", "icon", "avatar", "sprite", "pixel", "ads", "banner", "favicon",
    "editor", "author", "profile", "yazar", "cookie", "uygulama-indir",
    "dh-oneriyor", "dh-cookie", "instagram-big", "populer-",
)
_IMAGE_HINT_PATHS = (
    "/wp-content/uploads/", "/uploads/", "/images/", "/image/", "/img/", "/media/",
)
_TRACKING_QUERY_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid"}
_RESIZE_QUERY_KEYS = {"w", "h", "width", "height", "resize", "fit", "crop", "quality", "q"}

_NITTER_PIC_PATTERN = re.compile(
    r"^/pic/(?:orig/)?(?:media%2F|media/)([A-Za-z0-9_\-]+\.[a-zA-Z]{3,4})",
    re.IGNORECASE,
)
_TWITTER_CDN_HOSTS = {"pbs.twimg.com", "ton.twimg.com", "video.twimg.com"}

# ── Tip Dönüşümleri ───────────────────────────────────────────────────────────

def _is_test_mode() -> bool:
    """Test modunun aktif olup olmadığını kontrol eder (ENV veya CLI argümanı ile)."""
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        return True
    return "--test" in sys.argv

def _safe_int(value: Any, default: int) -> int:
    """Değeri güvenli şekilde tam sayıya (int) çevirir, başarısız olursa varsayılan değeri döndürür."""
    try: return int(value)
    except Exception: return default

def _safe_float(value: Any, default: float) -> float:
    """Değeri güvenli şekilde ondalıklı sayıya (float) çevirir, başarısız olursa varsayılan değeri döndürür."""
    try: return float(value)
    except Exception: return default

def _safe_int_min(value: Any, default: int, minimum: int) -> int:
    """Güvenli int çevirme işlemi yapar ve minimum değerin altına düşmesini engeller."""
    parsed = _safe_int(value, default)
    return parsed if parsed >= minimum else minimum

def _safe_float_min(value: Any, default: float, minimum: float = 0.0) -> float:
    """Güvenli float çevirme işlemi yapar ve minimum değerin altına düşmesini engeller."""
    parsed = _safe_float(value, default)
    return parsed if parsed >= minimum else minimum

def _coerce_bool(value: Any, default: bool = False) -> bool:
    """Çeşitli tip ve formatlardaki veriyi boolean (True/False) değerine dönüştürür."""
    if isinstance(value, bool): return value
    if value is None: return default
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "on"}: return True
    if s in {"0", "false", "no", "off"}: return False
    return default

def _read_bool_env(name: str, default: bool) -> bool:
    """Environment variable (ENV) değerini boolean olarak okur."""
    raw = os.environ.get(name)
    if raw is None: return default
    return _coerce_bool(raw, default)

def _read_int_env(name: str, default: int) -> int:
    """Environment variable (ENV) değerini int olarak okur."""
    raw = os.environ.get(name)
    if raw is None: return default
    return _safe_int(raw, default)

def _read_float_env(name: str, default: float) -> float:
    """Environment variable (ENV) değerini float olarak okur."""
    raw = os.environ.get(name)
    if raw is None: return default
    return _safe_float(raw, default)

def _turkish_lower(text: str) -> str:
    """Türkçe karakterleri (I->i vb.) doğru şekilde küçük harfe çevirir."""
    return text.replace("I", "i").lower()

# ── URL & Nitter ──────────────────────────────────────────────────────────────
#
# NITTER INSTANCE HAVUZU — son dogrulama: 2026-10-01
# Kaynaklar: https://status.d420.de (uptime + RSS saglik tablosu) ve
# https://codeberg.org/mv12star/shitter/wiki/Instances (calisan instance listesi).
# Listeye giren her instance'in /<kullanici>/rss ucu bu tarihte canli denendi.
#
# ONEMLI: Public instance'lar "scrape etmeyin, kendiniz host edin" diyor.
# Bu yuzden: (1) havuz genis tutulur, (2) istekler instance'lar arasinda
# DONDURULUR (rotation) boylece yuk tek instance'a yigilmaz, (3) denemeler
# arasina bekleme konur, (4) rate-limit / bot-challenge gorulen instance
# otomatik devre disi birakilir (circuit breaker).

_VERIFIED_NITTER_INSTANCE_HOSTS = (
    "nitter.kareem.one",       # status.d420.de: healthy, RSS ✅, 78 puan (%92 uptime)
    "nitter.meowing.monster",  # status.d420.de: healthy, RSS ✅, 56 puan (%96 uptime)
    "nitter.netbub.com",       # status.d420.de: healthy, RSS ✅, 56 puan (%92 uptime)
    "shitter.thepixora.com",   # status.d420.de: healthy, RSS ✅, 55 puan (%88 uptime)
    "nitter.jaydenha.uk",      # status.d420.de: healthy (ara sira dusuyor), RSS ✅, sadece IPv4
)

_SECONDARY_NITTER_INSTANCE_HOSTS = (
    # shitter wiki'de "calisan" ya da "active but rate limited" olarak gecen,
    # birinci kademe tukenirse denenen instance'lar.
    "nitter.zebes.info",
    "nitter.kabii.moe",
    "nitter.wisq.net",
    "nt.vern.cc",
    "nitter.anoxinon.de",
    "nitter.freedit.eu",
    "x.n0g.xyz",
    "tw.eir-nya.gay",
)

_DEFAULT_NITTER_INSTANCE_HOSTS = _VERIFIED_NITTER_INSTANCE_HOSTS + _SECONDARY_NITTER_INSTANCE_HOSTS

# 2026-10-01 itibariyle olu / kapali / bot engelini asamadigimiz instance'lar.
# Havuza ALINMAZLAR ve sources.json'da gorulseler bile aday olarak denenmezler
# (eski davranis: kaynak host'u her zaman ilk sirada dener -> her calismada
# bosuna DNS/timeout maliyeti ve kaynak 'olu' gorunuyordu).
_RETIRED_NITTER_INSTANCE_HOSTS = frozenset({
    "nitter.cf",                  # instance kapali (sources.json'da yaziliydi)
    "nitter.net",                 # kalici olarak kapandi
    "nitter.poast.org",           # kapali
    "nitter.privacydev.net",      # kapali
    "nitter.space",               # kapali
    "nitter.miningtcup.me",       # kaldirildi
    "xcancel.com",                # "XCancel service is suspended" (hukuki surec)
    "nitter.catsarch.com",        # 503 "Nitter shutdown"
    "nuku.trabun.org",            # 403 Forbidden (openresty)
    "nitter.privacyredirect.com", # privacyredirect.com'a dusuyor, RSS yok
    "shi.meowing.de",             # "Checking your browser" challenge
    "nitter.tiekoetter.com",      # Anubis proof-of-work challenge (JS istiyor, requests gecemez)
    "xcopy.uk",                   # "Instance has no auth tokens, or is fully rate limited"
    "nitter.click",               # status.d420.de: unhealthy
    "nitter.xitter.cc",           # status.d420.de: unhealthy + RSS kapali
    "bitter.st",                  # login duvari
})

# Host adi bu ipuclarindan birini iceriyorsa nitter/shitter ailesinden sayilir.
# (Orn. 'shitter.thepixora.com' ve 'xcancel.com' 'nitter' ile baslamadigi icin
# eski kontrol bunlari tanimiyordu -> gorsel pipeline'i nitter dalina hic girmiyordu.)
_NITTER_HOST_HINTS = ("nitter", "shitter", "xcancel", "twiiit", "nitt.tr", "xcopy")

_NITTER_HEALTH_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "nitter_health.json"
)

_ROTATION_MODES = ("sequential", "random", "sticky")
_rotation_state: dict = {"cursor": -1, "logged_mode": ""}
_nitter_health_cache: Optional[dict] = None

def _normalize_instance_host(raw: Any) -> str:
    """'https://Nitter.Kareem.one/' gibi bir degeri saf lowercase host'a indirger."""
    host = str(raw or "").strip().lower()
    if not host: return ""
    if "://" in host: host = host.split("://", 1)[1]
    host = host.split("/", 1)[0].split("?", 1)[0].split(":", 1)[0]
    if not host or "." not in host: return ""
    return host

_KNOWN_HOSTS_CACHE: dict = {"key": None, "hosts": frozenset()}

def _known_nitter_hosts() -> frozenset:
    """Bilinen tum nitter ailesi host'lari (havuz + varsayilanlar + emekliler).

    Gorsel pipeline'i bu kontrolu aday URL basina cagirdigi icin sonuc
    cache'lenir; ENV/settings degistiginde cache otomatik tazelenir.
    """
    raw_env = os.environ.get("NITTER_INSTANCES", "") or ""
    configured = _nitter_posting_cfg().get("nitter_instances", [])
    cache_key = (raw_env, tuple(configured) if isinstance(configured, list) else str(configured))
    cached = _KNOWN_HOSTS_CACHE.get("hosts")
    if _KNOWN_HOSTS_CACHE.get("key") == cache_key and isinstance(cached, frozenset):
        return cached
    hosts = set(_nitter_instance_hosts()) | set(_DEFAULT_NITTER_INSTANCE_HOSTS) | _RETIRED_NITTER_INSTANCE_HOSTS
    _KNOWN_HOSTS_CACHE["key"] = cache_key
    _KNOWN_HOSTS_CACHE["hosts"] = frozenset(hosts)
    return _KNOWN_HOSTS_CACHE["hosts"]

def _is_known_nitter_host(host: str) -> bool:
    """Host'un nitter/shitter ailesinden olup olmadigini soyler."""
    normalized = _normalize_instance_host(host)
    if not normalized: return False
    if normalized in _known_nitter_hosts(): return True
    return any(hint in normalized for hint in _NITTER_HOST_HINTS)

def _is_nitter_feed(url: str) -> bool:
    """URL'nin bir Nitter feed kaynağına ait olup olmadığını kontrol eder."""
    return _is_known_nitter_host((urlparse(url or "").netloc or "").lower())

def _is_nitter_url(url: str) -> bool:
    """Verilen URL'nin bir Nitter instance'ına ait olup olmadığını kontrol eder.

    Havuzdaki host'lar (orn. shitter.thepixora.com, nt.vern.cc) 'nitter' ile
    baslamadigi icin eskiden taninmiyordu; artik bilinen host listesi + host
    ipuclari birlikte kullaniliyor.
    """
    return _is_known_nitter_host((urlparse(url or "").netloc or "").lower())

# ── Nitter yapilandirma okuyuculari ──────────────────────────────────────────

_POSTING_CFG_CACHE: dict = {"ts": 0.0, "cfg": None}
_POSTING_CFG_TTL_SECONDS = 30.0

def _nitter_posting_cfg() -> dict:
    """settings.json -> posting blogunu guvenli sekilde dondurur (30sn cache).

    Instance saglik kontrolu her aday icin bu ayarlari okudugu icin dosya
    okuma/parse maliyeti cache'lenir (run boyunca ayarlar degismiyor).
    """
    now = time.time()
    cached = _POSTING_CFG_CACHE.get("cfg")
    if isinstance(cached, dict) and (now - float(_POSTING_CFG_CACHE.get("ts") or 0.0)) < _POSTING_CFG_TTL_SECONDS:
        return cached
    try:
        from core.config_loader import load_config
        settings_cfg = load_config("settings")
        posting_cfg = settings_cfg.get("posting", {}) if isinstance(settings_cfg, dict) else {}
        cfg = posting_cfg if isinstance(posting_cfg, dict) else {}
    except Exception:
        cfg = {}
    _POSTING_CFG_CACHE["cfg"] = cfg
    _POSTING_CFG_CACHE["ts"] = now
    return cfg

def _nitter_instance_hosts() -> list:
    """Nitter RSS instance havuzu (yapilandirilmis sirayla, tekrarsiz).

    Sirayla ENV (NITTER_INSTANCES), settings.json (posting.nitter_instances)
    ve varsayilan havuz kullanilir. Bos/yanlis degerler listeye alinmaz;
    'https://host/' seklinde yazilmis degerler saf host'a indirgenir.
    """
    hosts: List[str] = []
    raw_env = (os.environ.get("NITTER_INSTANCES", "") or "").strip()
    if raw_env:
        hosts = [_normalize_instance_host(h) for h in raw_env.split(",")]
    else:
        configured = _nitter_posting_cfg().get("nitter_instances", [])
        if isinstance(configured, list):
            hosts = [_normalize_instance_host(h) for h in configured]
        elif isinstance(configured, str):
            hosts = [_normalize_instance_host(h) for h in configured.split(",")]
    hosts = [h for h in hosts if h]
    if not hosts:
        hosts = list(_DEFAULT_NITTER_INSTANCE_HOSTS)
    unique: List[str] = []
    seen = set()
    for host in hosts:
        if host in seen: continue
        seen.add(host)
        unique.append(host)
    return unique

def _nitter_rotation_mode() -> str:
    """Instance dondurme modu: 'sequential' (round-robin), 'random', 'sticky'."""
    raw = os.environ.get("NITTER_INSTANCE_ROTATION")
    if raw is None:
        raw = _nitter_posting_cfg().get("nitter_instance_rotation", "sequential")
    mode = str(raw or "").strip().lower()
    aliases = {
        "round_robin": "sequential", "roundrobin": "sequential", "rotate": "sequential",
        "rotation": "sequential", "shuffle": "random", "off": "sticky", "none": "sticky",
        "fixed": "sticky", "ordered": "sticky", "": "sequential",
    }
    mode = aliases.get(mode, mode)
    return mode if mode in _ROTATION_MODES else "sequential"

def _nitter_max_instances_per_feed() -> int:
    """Bir feed icin denenecek maksimum instance sayisi (sure/ban korumasi)."""
    default = 5
    value = _read_int_env("NITTER_MAX_INSTANCES_PER_FEED", _safe_int(_nitter_posting_cfg().get("nitter_max_instances_per_feed", default), default))
    return _safe_int_min(value, default, 1)

def _nitter_failure_threshold() -> int:
    """Kac ard arda basarisizlikta instance 'degraded' sayilip sona atilsin."""
    default = 2
    value = _read_int_env("NITTER_INSTANCE_FAILURE_THRESHOLD", _safe_int(_nitter_posting_cfg().get("nitter_instance_failure_threshold", default), default))
    return _safe_int_min(value, default, 1)

def _nitter_block_minutes() -> int:
    """Rate-limit / bot-challenge gorulen instance kac dakika devre disi kalsin."""
    default = 60
    value = _read_int_env("NITTER_INSTANCE_BLOCK_MINUTES", _safe_int(_nitter_posting_cfg().get("nitter_instance_block_minutes", default), default))
    return _safe_int_min(value, default, 1)

def _nitter_health_state_enabled() -> bool:
    """Instance saglik durumu data/nitter_health.json'a yazilsin mi (run'lar arasi hafiza).

    PERSIST_STATE=false olan calismalarda (test/dry-run) diske yazilmaz; durum
    sadece bellek ici tutulur.
    """
    if not _coerce_bool(os.environ.get("PERSIST_STATE", "true"), True): return False
    return _read_bool_env("NITTER_HEALTH_STATE", _coerce_bool(_nitter_posting_cfg().get("nitter_health_state", True), True))

# ── Nitter instance rotasyonu ────────────────────────────────────────────────

def _rotate_nitter_hosts(hosts: list, mode: Optional[str] = None) -> list:
    """Instance listesini secilen moda gore yeniden siralar.

    sequential: her cagride bir sonraki instance'tan baslar (round-robin);
                ilk cagride process-basi rastgele ofset kullanilir, boylece
                farkli calismalar farkli instance'larla baslar ve tek
                instance'a yuk yigilmaz.
    random    : her cagride liste karistirilir.
    sticky    : yapilandirilmis sira korunur (eski davranis).
    """
    hosts = [h for h in (hosts or []) if h]
    if len(hosts) <= 1: return hosts
    rotation = (mode or _nitter_rotation_mode()).strip().lower()
    if rotation == "random":
        shuffled = hosts[:]
        random.shuffle(shuffled)
        return shuffled
    if rotation == "sticky":
        return hosts
    cursor = _rotation_state.get("cursor", -1)
    if not isinstance(cursor, int) or cursor < 0:
        cursor = random.randrange(len(hosts))
    else:
        cursor = (cursor + 1) % len(hosts)
    _rotation_state["cursor"] = cursor
    start = cursor % len(hosts)
    return hosts[start:] + hosts[:start]

# ── Nitter instance saglik takibi (circuit breaker) ──────────────────────────

def _empty_nitter_health() -> dict:
    return {"instances": {}, "updated_at": ""}

def _load_nitter_health() -> dict:
    """Instance saglik durumunu yukler (once bellek, sonra disk)."""
    global _nitter_health_cache
    if isinstance(_nitter_health_cache, dict): return _nitter_health_cache
    data = _empty_nitter_health()
    if _nitter_health_state_enabled():
        try:
            with open(_NITTER_HEALTH_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and isinstance(raw.get("instances"), dict):
                data = {"instances": raw.get("instances", {}), "updated_at": str(raw.get("updated_at", ""))}
        except FileNotFoundError:
            pass
        except Exception as exc:
            log(f"Nitter saglik dosyasi okunamadi ({exc}); bellek ici durumla devam ediliyor", "WARNING")
    _nitter_health_cache = data
    return data

def _save_nitter_health() -> None:
    """Instance saglik durumunu diske yazar (best-effort, hatada sessiz)."""
    if not _nitter_health_state_enabled(): return
    if _nitter_health_cache is None: return
    try:
        from core.config_loader import save_json
        payload = dict(_nitter_health_cache)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save_json(_NITTER_HEALTH_PATH, payload)
    except Exception as exc:
        log(f"Nitter saglik dosyasi yazilamadi: {exc}", "WARNING")

def _nitter_health_entry(host: str) -> dict:
    health = _load_nitter_health()
    instances = health.setdefault("instances", {})
    entry = instances.get(host)
    if not isinstance(entry, dict):
        entry = {"failures": 0, "successes": 0, "state": "ok", "reason": "", "blocked_until": 0.0, "last_change": ""}
        instances[host] = entry
    return entry

def _nitter_instance_state(host: str) -> str:
    """Instance'in anlik durumu: 'ok' | 'degraded' | 'blocked'."""
    normalized = _normalize_instance_host(host)
    if not normalized: return "ok"
    entry = _nitter_health_entry(normalized)
    if str(entry.get("state", "ok")) == "blocked":
        try:
            blocked_until = float(entry.get("blocked_until") or 0.0)
        except (TypeError, ValueError):
            blocked_until = 0.0
        if blocked_until > time.time():
            return "blocked"
        # Suresi doldu: instance'a ikinci sans ver.
        entry.update({"state": "ok", "failures": 0, "reason": "", "blocked_until": 0.0})
    try:
        failures = int(entry.get("failures", 0) or 0)
    except (TypeError, ValueError):
        failures = 0
    if failures >= _nitter_failure_threshold(): return "degraded"
    return "ok"

def _record_nitter_instance_result(host: str, success: bool, reason: str = "", status_code: Optional[int] = None, blocked: bool = False) -> str:
    """Instance icin basari/basarisizlik kaydeder ve yeni durumu dondurur.

    blocked=True (429/403/bot-challenge) -> instance NITTER_INSTANCE_BLOCK_MINUTES
    boyunca hic denenmez; boylece hem sure kaybi hem de ban riski azalir.
    """
    normalized = _normalize_instance_host(host)
    if not normalized: return "ok"
    entry = _nitter_health_entry(normalized)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if success:
        entry["failures"] = 0
        entry["state"] = "ok"
        entry["reason"] = ""
        entry["blocked_until"] = 0.0
        try:
            entry["successes"] = int(entry.get("successes", 0) or 0) + 1
        except (TypeError, ValueError):
            entry["successes"] = 1
    else:
        try:
            entry["failures"] = int(entry.get("failures", 0) or 0) + 1
        except (TypeError, ValueError):
            entry["failures"] = 1
        entry["reason"] = (reason or "error")[:80]
        if status_code:
            entry["last_status"] = int(status_code)
        if blocked:
            entry["state"] = "blocked"
            entry["blocked_until"] = time.time() + (_nitter_block_minutes() * 60)
        elif int(entry["failures"]) >= _nitter_failure_threshold():
            entry["state"] = "degraded"
        else:
            entry["state"] = "ok"
    entry["last_change"] = now_iso
    _save_nitter_health()
    return str(entry.get("state", "ok"))

def _order_nitter_hosts_by_health(hosts: list) -> list:
    """Saglikli instance'lar one, 'degraded' olanlar sona; 'blocked' olanlar elenir.

    Tum liste blokluysa (orn. hepsi rate-limit yediyse) liste oldugu gibi
    dondurulur: hic denememekten iyidir, feed tamamen olu kalmasin.
    """
    ok, degraded = [], []
    for host in hosts or []:
        state = _nitter_instance_state(host)
        if state == "blocked": continue
        if state == "degraded":
            degraded.append(host)
        else:
            ok.append(host)
    if not ok and not degraded:
        if hosts:
            log("Nitter: havuzdaki tum instance'lar bloklu durumda, liste yine de deneniyor", "WARNING")
        return list(hosts or [])
    return ok + degraded

def _nitter_instance_is_available(host: str) -> bool:
    """Instance bu calismada denenebilir mi (blocked degil mi)?"""
    return _nitter_instance_state(host) != "blocked"

def reset_nitter_health_state(clear_disk: bool = False) -> None:
    """Bellek ici (ve istenirse diskteki) instance saglik durumunu sifirlar.

    Testlerin birbirini etkilememesi ve manuel 'sifirdan basla' senaryosu icin.
    """
    global _nitter_health_cache
    _nitter_health_cache = _empty_nitter_health()
    _rotation_state["cursor"] = -1
    _rotation_state["logged_mode"] = ""
    _POSTING_CFG_CACHE["cfg"] = None
    _POSTING_CFG_CACHE["ts"] = 0.0
    _KNOWN_HOSTS_CACHE["key"] = None
    _KNOWN_HOSTS_CACHE["hosts"] = frozenset()
    if clear_disk:
        try:
            if os.path.exists(_NITTER_HEALTH_PATH): os.remove(_NITTER_HEALTH_PATH)
        except OSError:
            pass

def _nitter_health_summary() -> str:
    """Log'lanabilir kisa saglik ozeti (host=durum)."""
    health = _load_nitter_health()
    parts = []
    for host, entry in sorted((health.get("instances") or {}).items()):
        if not isinstance(entry, dict): continue
        state = _nitter_instance_state(host)
        if state == "ok" and not int(entry.get("failures", 0) or 0) and not int(entry.get("successes", 0) or 0):
            continue
        parts.append(f"{host}={state}(f{entry.get('failures', 0)}/s{entry.get('successes', 0)})")
    return ", ".join(parts[:14])

# ── Nitter bot challenge / rate-limit tespiti ────────────────────────────────

_NITTER_CHALLENGE_MARKERS = (
    "making sure you're not a bot",
    "/.within.website/x/cmd/anubis",
    "anubis could not load its javascript",
    "checking your browser",
    "fighting scrapers",
    "just a moment",
    "cf-chl",
    "attention required! | cloudflare",
    "enable javascript and cookies to continue",
    "__goaway_challenge",
    "verify you are a human",
    "access denied",
    "request blocked",
)

_NITTER_RATE_LIMIT_MARKERS = (
    "no auth tokens",
    "fully rate limited",
    "rate limit exceeded",
    "too many requests",
    "twitter rate limit",
    "please try again later",
)

_NITTER_RSS_DISABLED_MARKERS = (
    "rss feed is disabled",
    "rss is disabled",
    "service is suspended",
    "nitter shutdown",
    "nitter is no longer available",
)

def _detect_nitter_block_reason(body_text: str = "", status_code: Optional[int] = None) -> str:
    """Yanitin bot-challenge / rate-limit / RSS-kapali olup olmadigini soyler.

    Donus: "" (sorun yok) | "bot_challenge" | "rate_limited" | "rss_disabled" | "http_<kod>"

    Gecerli bir RSS/XML govdesi icerik olarak bu marker'lari tasiyabilecegi
    icin (orn. tweet metninde 'rate limit' gecmesi) XML govdede marker taramasi
    yapilmaz; sadece HTTP durum koduna bakilir.
    """
    if status_code in (403, 429, 503):
        return f"http_{status_code}"
    if status_code and 400 <= int(status_code) < 500:
        return f"http_{status_code}"
    text = (body_text or "")[:8000]
    if not text.strip(): return ""
    head = text.lstrip()[:300].lower()
    if head.startswith("<?xml") or head.startswith("<rss") or head.startswith("<feed") or "<channel" in head:
        return ""  # RSS/XML govde: challenge sayfasi degil
    lowered = text.lower()
    if any(marker in lowered for marker in _NITTER_CHALLENGE_MARKERS): return "bot_challenge"
    if any(marker in lowered for marker in _NITTER_RSS_DISABLED_MARKERS): return "rss_disabled"
    if any(marker in lowered for marker in _NITTER_RATE_LIMIT_MARKERS): return "rate_limited"
    return ""

def _should_block_nitter_instance(reason: str) -> bool:
    """Bu hata turu instance'i gecici olarak devre disi birakmali mi?"""
    if not reason: return False
    if reason in {"bot_challenge", "rate_limited", "rss_disabled", "http_403", "http_429"}: return True
    return False

# ── Nitter aday URL uretimi ──────────────────────────────────────────────────

def _nitter_candidate_urls(feed_url: str, rotation_mode: Optional[str] = None, max_candidates: Optional[int] = None) -> list:
    """Nitter URL'si icin denenecek instance aday URL'lerini uretir.

    Yol (/kullanici/rss, /kullanici/status/ID) korunur, host rotasyonla degisir.
    - Kaynak URL'si havuz DISI bir instance'a aitse (orn. self-host) ilk aday olur.
    - Kaynak host havuzdaysa veya emekliyse (nitter.cf, nitter.net, xcancel.com...)
      aday olarak EKLENMEZ; dogrudan dondurulmus havuz kullanilir.
    - 'blocked' instance'lar atlanir, 'degraded' olanlar sona atilir.
    """
    parsed = urlparse(feed_url or "")
    if not parsed.scheme or not parsed.netloc or not parsed.path:
        return [feed_url] if feed_url else []
    path = parsed.path
    query = f"?{parsed.query}" if parsed.query else ""
    source_host = _normalize_instance_host(parsed.netloc)

    pool = _nitter_instance_hosts()
    ordered = _order_nitter_hosts_by_health(_rotate_nitter_hosts(pool, rotation_mode))
    limit = max_candidates if (max_candidates and max_candidates > 0) else _nitter_max_instances_per_feed()

    candidates: List[str] = []
    seen = set()
    if source_host and source_host not in set(pool) and source_host not in _RETIRED_NITTER_INSTANCE_HOSTS:
        # Self-host / havuz disi ozel instance: kullanici tercihine saygi duy, once bunu dene.
        candidates.append(feed_url)
        seen.add(source_host)

    instance_attempts = 0
    for host in ordered:
        if instance_attempts >= limit: break
        if host in seen: continue
        seen.add(host)
        candidates.append(f"https://{host}{path}{query}")
        instance_attempts += 1

    if not candidates and feed_url: candidates.append(feed_url)
    return candidates

def _nitter_link_https(url: str) -> str:
    """Nitter linklerini https'e cevirir (bazi instance'lar http:// link uretir)."""
    if not url: return url
    parsed = urlparse(url)
    if parsed.scheme != "http": return url
    if not _is_known_nitter_host(parsed.netloc): return url
    return urlunparse(parsed._replace(scheme="https"))

def _canonicalize_nitter_link(url: str) -> Tuple[str, str]:
    """Nitter linkini (kanonik_link, nitter_sayfa_linki) ciftine cevirir.

    Instance rotasyonu yapildigi icin ayni tweet her calismada FARKLI host'tan
    gelebiliyor (ustelik bazilari http:// link uretiyor). Link kanoniklestirilmezse
    duplike tespiti ve data/posted_news.json kayitlari instance'a bagimli hale
    gelir (ayni haber tekrar paylasilabilir). Bu yuzden /kullanici/status/ID
    linkleri x.com'a cevrilir; nitter sayfasi ayrica dondurulur ki gorsel/metin
    scrape'i hala nitter uzerinden yapilabilsin.
    """
    if not url: return "", ""
    normalized = _nitter_link_https(str(url).strip())
    if not _is_nitter_url(normalized): return normalized, ""
    canonical = normalized
    if _read_bool_env("NITTER_CANONICALIZE_LINKS", _coerce_bool(_nitter_posting_cfg().get("nitter_canonicalize_links", True), True)):
        twitter_url = _nitter_to_twitter_url(normalized)
        if twitter_url: canonical = twitter_url
    return canonical, normalized

def _nitter_to_twitter_url(nitter_url: str) -> str:
    """Nitter tweet URL'sini orijinal Twitter/x.com URL'sine çevirir."""
    if not nitter_url: return ""
    parsed = urlparse(nitter_url)
    path = parsed.path or ""
    m = re.search(r"(/[^/]+/status/\d+)", path)
    if m: return f"https://x.com{m.group(1)}"
    return ""

def _is_profile_image_url(url: str) -> bool:
    """URL'nin bir Twitter/Nitter profil fotosu mu yoksa içerik görseli mi olduğunu kontrol eder."""
    lower = url.lower()
    return "/profile_images/" in lower or "/profile_banners/" in lower

def _resolve_nitter_image_url(raw_url: str, nitter_base: str = "") -> str:
    """Nitter /pic/ formatındaki URL'leri orijinal Twitter CDN (pbs.twimg.com) URL'sine çevirir."""
    if not raw_url: return ""
    parsed_raw = urlparse(raw_url)
    if parsed_raw.netloc in _TWITTER_CDN_HOSTS: return raw_url
    
    path = parsed_raw.path if parsed_raw.scheme else raw_url
    m = _NITTER_PIC_PATTERN.match(path)
    if m:
        filename = m.group(1)
        filename = unquote(filename)
        name_part, _, ext_part = filename.rpartition(".")
        ext_part = ext_part.lower()
        quality = "orig" if "/orig/" in path else "large"
        return f"https://pbs.twimg.com/media/{filename}?format={ext_part}&name={quality}"
        
    if _is_nitter_url(raw_url) and "/pic/" in raw_url: return raw_url
    return ""

# ── HTTP ──────────────────────────────────────────────────────────────────────

def _request_with_retry(url: str, timeout: int = 20, attempts: int = 3, base_wait_seconds: float = 1.5, extra_headers: Optional[dict] = None) -> requests.Response:
    """Geçici ağ hatalarında exponential backoff ile HTTP isteğini tekrar dener.

    4xx istemci hatalari (408 haric) TEKRAR DENENMEZ: nitter instance'lari
    403/429 dondurdugunde ayni instance'a defalarca istek atmak hem sure
    kaybettiriyor hem de ban riskini artiriyordu (public instance'lar scrape
    istemiyor). Bu durumda hata hemen yukari firlatilir, cagiran taraf bir
    sonraki instance'a gecer.
    """
    last_exc: Optional[Exception] = None
    headers = {"User-Agent": _USER_AGENT}
    if extra_headers: headers.update({k: v for k, v in extra_headers.items() if v})
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as exc:
            last_exc = exc
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            log(f"HTTP deneme hatasi ({attempt}/{attempts}) url={url} -> {exc}", "WARNING" if attempt < attempts else "ERROR")
            if status_code and 400 <= status_code < 500 and status_code != 408:
                # Kalici istemci hatasi: tekrar denemek manasiz (rate-limit/ban riski).
                raise
            if attempt < attempts: time.sleep(base_wait_seconds * (2 ** (attempt - 1)))
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_exc = exc
            log(f"HTTP deneme hatasi ({attempt}/{attempts}) url={url} -> {exc}", "WARNING" if attempt < attempts else "ERROR")
            if attempt < attempts: time.sleep(base_wait_seconds * (2 ** (attempt - 1)))
        except Exception as exc:
            last_exc = exc
            log(f"Beklenmeyen istek hatasi ({attempt}/{attempts}) url={url} -> {exc}", "WARNING" if attempt < attempts else "ERROR")
            if attempt < attempts: time.sleep(base_wait_seconds * (2 ** (attempt - 1)))
    if last_exc: raise last_exc
    raise RuntimeError("HTTP request failed without exception detail")

# ── Görsel URL İşlemleri ──────────────────────────────────────────────────────

def _normalize_image_url(raw_url: str, page_url: str = "") -> str:
    """Görsel URL'sini temizler (tracking parametrelerini atar, Nitter'i çevirir, göreli URL'leri tamamlar)."""
    if not raw_url: return ""
    candidate = raw_url.strip()
    if not candidate: return ""
    if "/pic/" in candidate:
        resolved = _resolve_nitter_image_url(candidate, page_url)
        if resolved: return resolved
    if candidate.startswith("//"): candidate = f"https:{candidate}"
    if page_url: candidate = urljoin(page_url, candidate)
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc: return ""
    query_items = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in _TRACKING_QUERY_KEYS]
    cleaned = parsed._replace(query=urlencode(query_items), fragment="")
    return urlunparse(cleaned)

def _normalize_path_for_candidate_key(path: str) -> str:
    """Dosya yolunu normalize ederek candidate key oluşturulmasına uygun hale getirir."""
    if not path: return path
    dir_part, _, filename = path.rpartition("/")
    name, dot, ext = filename.partition(".")
    lower_name = name.lower()
    lower_name = re.sub(r"^src_\d{2,4}x\d{2,4}x", "", lower_name, flags=re.IGNORECASE)
    lower_name = re.sub(r"^\d{2,4}x\d{2,4}", "", lower_name, flags=re.IGNORECASE)
    normalized_filename = f"{lower_name}{dot}{ext}" if dot else lower_name
    return f"{dir_part}/{normalized_filename}" if dir_part else normalized_filename

def _candidate_key(url: str) -> str:
    """URL'yi duplikasyon kontrolü için standart bir anahtara (key) dönüştürür."""
    parsed = urlparse(url)
    path = _normalize_path_for_candidate_key(parsed.path or "")
    path = re.sub(r"-(\d{2,4})x(\d{2,4})(\.(?:jpg|jpeg|png|webp|gif|bmp|avif))$", r"\3", path, flags=re.IGNORECASE)
    filtered_qs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in _RESIZE_QUERY_KEYS]
    return urlunparse(parsed._replace(path=path, query=urlencode(filtered_qs), fragment="")).lower()

def _looks_like_noise_image(url: str) -> bool:
    """URL'nin bir gürültü (logo, ikon, banner, profil fotosu vb.) olup olmadığını kontrol eder."""
    lower_url = url.lower()
    parsed = urlparse(lower_url)
    path = parsed.path or ""
    host = parsed.netloc or ""
    if host in _TWITTER_CDN_HOSTS:
        if "/profile_images/" in path or "/profile_banners/" in path: return True
        return False
    if _is_nitter_url(lower_url) and "/pic/" in lower_url:
        if "profile_images" in lower_url or "profile_banners" in lower_url: return True
        return False
    if any(hint in lower_url for hint in _IMAGE_NOISE_HINTS): return True
    if "/content/img/" in path: return True
    return False

def _is_probable_image_url(url: str) -> bool:
    """URL'nin gerçek bir görsel dosyasına (.jpg, .png vb.) işaret edip etmediğini kontrol eder."""
    lower = url.lower()
    parsed = urlparse(lower)
    host = parsed.netloc or ""
    if host in _TWITTER_CDN_HOSTS: return True
    if _is_nitter_url(lower) and "/pic/" in lower: return True
    if parsed.path.endswith(_DISALLOWED_IMAGE_EXTENSIONS): return False
    if "/images/editor/" in lower or "/images/images/editor/" in lower: return False
    if any(x in lower for x in ("/author/", "/profile/", "/avatar/")): return False
    if any(ext in lower for ext in _IMAGE_EXTENSIONS): return True
    if "image" in lower: return True
    if any(p in lower for p in _IMAGE_HINT_PATHS): return True
    return False

def _donanimhaber_variants(url: str) -> List[str]:
    """Donanimhaber sitesine özel görsel boyut formatlarını varyant olarak üretir."""
    variants = [url]
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path or ""
    if "donanimhaber.com" not in host: return variants
    upgraded_path = re.sub(r"/src_\d{2,4}x\d{2,4}x", "/src/", path, flags=re.IGNORECASE)
    if upgraded_path != path: variants.append(urlunparse(parsed._replace(path=upgraded_path)))
    m_idx = re.search(r"(\d{4,7})_(\d+)(\.(?:jpg|jpeg|png|webp|gif|bmp|avif))$", path, re.IGNORECASE)
    if m_idx:
        base_id = m_idx.group(1); ext = m_idx.group(3); prefix = path[: m_idx.start()]
        for i in range(0, 6):
            p = f"{prefix}{base_id}_{i}{ext}"
            variants.append(urlunparse(parsed._replace(path=p)))
    m_plain = re.search(r"(\d{4,7})(\.(?:jpg|jpeg|png|webp|gif|bmp|avif))$", path, re.IGNORECASE)
    if m_plain:
        base_id = m_plain.group(1); ext = m_plain.group(2); prefix = path[: m_plain.start()]
        for i in range(0, 6):
            p = f"{prefix}{base_id}_{i}{ext}"
            variants.append(urlunparse(parsed._replace(path=p)))
    seen = set(); unique = []
    for item in variants:
        if item and item not in seen:
            seen.add(item); unique.append(item)
    return unique

def _thumbnail_to_original_variants(url: str) -> List[str]:
    """Thumbnail (küçük resim) URL'sinden orijinal büyük resim URL varyantlarını türetir."""
    parsed_check = urlparse(url)
    if parsed_check.netloc in _TWITTER_CDN_HOSTS: return [url]
    variants = [url]
    parsed = urlparse(url)
    path = parsed.path or ""
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    wp_thumb_pattern = re.compile(r"-(\d{2,4})x(\d{2,4})(\.(?:jpg|jpeg|png|webp|gif|bmp|avif))$", re.IGNORECASE)
    if wp_thumb_pattern.search(path):
        original_path = wp_thumb_pattern.sub(r"\3", path)
        variants.append(urlunparse(parsed._replace(path=original_path)))
    if query_items:
        filtered_query = [(k, v) for k, v in query_items if k.lower() not in _RESIZE_QUERY_KEYS]
        if len(filtered_query) != len(query_items):
            variants.append(urlunparse(parsed._replace(query=urlencode(filtered_query))))
    filename_cleaned_path = re.sub(r"(?i)([-_](small|thumb|thumbnail|medium|preview))(?=\.)", "", path)
    if filename_cleaned_path != path:
        variants.append(urlunparse(parsed._replace(path=filename_cleaned_path)))
    for item in list(variants):
        for dv in _donanimhaber_variants(item):
            variants.append(dv)
    unique = []; seen = set()
    for item in variants:
        if item and item not in seen:
            seen.add(item); unique.append(item)
    return unique

def _extract_best_src_from_srcset(srcset: str, page_url: str) -> str:
    """HTML srcset özniteliğinden en yüksek çözünürlüklü görsel URL'sini çıkarır."""
    best_url = ""; best_score = -1.0
    for item in srcset.split(","):
        item = item.strip()
        if not item: continue
        parts = item.split()
        url_part = _normalize_image_url(parts[0], page_url)
        if not url_part: continue
        score = 1.0
        if len(parts) > 1:
            descriptor = parts[1].lower()
            if descriptor.endswith("w"):
                try: score = float(descriptor[:-1])
                except ValueError: score = 1.0
            elif descriptor.endswith("x"):
                try: score = float(descriptor[:-1]) * 1000
                except ValueError: score = 1.0
        if score > best_score:
            best_score = score; best_url = url_part
    return best_url
