"""큐텐 운영 대시보드 — FastAPI 서버.

환경변수
  QOO10_KEY       셀러 인증키 (없으면 데모 데이터로 동작)
  DASH_PASSWORD   접속 비밀번호 (아이디는 아무거나). 비우면 잠금 없음
  SYNC_MINUTES    자동 동기화 주기(분), 기본 60
  DELAY_DAYS      결제 후 이 일수가 지나도 미발송이면 '배송지연', 기본 3
  DB_PATH         SQLite 경로, 기본 ./qoo10.db (Railway는 볼륨 경로 권장)
"""
import os, json, sqlite3, random, secrets, threading, time, traceback
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from qoo10 import Qoo10

KEY = os.getenv("QOO10_KEY", "").strip()
if not KEY:  # PC 실행용: 같은 폴더나 상위 폴더의 qoo10_key.txt
    for f in (Path(__file__).with_name("qoo10_key.txt"), Path(__file__).parent.parent / "qoo10_key.txt"):
        if f.exists():
            KEY = f.read_text(encoding="utf-8").strip(); break
PASSWORD = os.getenv("DASH_PASSWORD", "")
SYNC_MIN = int(os.getenv("SYNC_MINUTES", "60"))
DELAY_DAYS = int(os.getenv("DELAY_DAYS", "3"))
DB = os.getenv("DB_PATH", str(Path(__file__).with_name("qoo10.db")))
DEMO = not KEY

app = FastAPI(title="큐텐 운영 대시보드")
security = HTTPBasic(auto_error=bool(PASSWORD))


def auth(cred: HTTPBasicCredentials = Depends(security)):
    if PASSWORD and not (cred and secrets.compare_digest(cred.password, PASSWORD)):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})


# ── DB ───────────────────────────────────────────────
def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


with db() as c:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS products(item_code TEXT PRIMARY KEY, seller_code TEXT, title TEXT,
        price REAL, retail_price REAL, qty INTEGER, status TEXT, raw TEXT, updated TEXT);
    CREATE TABLE IF NOT EXISTS orders(order_no TEXT PRIMARY KEY, pack_no TEXT, item_code TEXT, seller_code TEXT,
        title TEXT, option TEXT, qty INTEGER, amount REAL, order_date TEXT, pay_date TEXT, ship_date TEXT,
        status TEXT, carrier TEXT, tracking TEXT, buyer TEXT, raw TEXT, updated TEXT);
    CREATE TABLE IF NOT EXISTS sync_log(id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT, finished TEXT,
        ok INTEGER, products INTEGER, orders INTEGER, message TEXT);
    """)


def pick(d, *keys, default=""):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return default


def num(v):
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return 0.0


def dt(v):
    """큐텐 날짜 문자열 → 'YYYY-MM-DD HH:MM:SS'"""
    if not v:
        return ""
    s = str(v).replace("T", " ").replace("/", "-")[:19]
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s[:len(datetime.now().strftime(f))], f).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return s


STATUS = {"1": "배송요청", "2": "배송준비", "3": "배송중", "4": "배송완료", "5": "구매확정",
          "Paid": "배송요청", "On request": "배송요청", "Checking order": "배송준비",
          "On delivery": "배송중", "Delivered": "배송완료"}


def norm_status(v):
    v = str(v or "")
    for k, s in STATUS.items():
        if k.lower() in v.lower():
            return s
    return v or "미확인"


# ── 동기화 ────────────────────────────────────────────
_lock = threading.Lock()


def sync():
    if not _lock.acquire(blocking=False):
        return {"ok": False, "message": "이미 동기화 중"}
    started = datetime.now().isoformat(timespec="seconds")
    np = no = 0
    try:
        if DEMO:
            np, no = seed_demo()
            msg = "데모 데이터 생성 (QOO10_KEY 미설정)"
        else:
            q = Qoo10(KEY)
            now = datetime.now().isoformat(timespec="seconds")
            prods = q.products()
            with db() as c:
                for p in prods:
                    c.execute("REPLACE INTO products VALUES(?,?,?,?,?,?,?,?,?)", (
                        str(pick(p, "ItemCode", "GdNo", "ItemNo")), pick(p, "SellerCode"),
                        pick(p, "ItemTitle", "ItemName"), num(pick(p, "ItemPrice", "SellPrice", default=0)),
                        num(pick(p, "RetailPrice", default=0)), int(num(pick(p, "ItemQty", "Qty", default=0))),
                        pick(p, "ItemStatus", default="S2"), json.dumps(p, ensure_ascii=False), now))
            end = datetime.now()
            start = end - timedelta(days=int(os.getenv("ORDER_DAYS", "90")))
            ords, cur = [], start
            while cur < end:  # 큐텐 조회기간 제한 대비 30일 단위
                nxt = min(cur + timedelta(days=30), end)
                ords += q.orders(cur.strftime("%Y%m%d"), nxt.strftime("%Y%m%d"))
                cur = nxt + timedelta(days=1)
            with db() as c:
                for o in ords:
                    c.execute("REPLACE INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                        str(pick(o, "orderNo", "OrderNo")), str(pick(o, "packNo", "PackNo")),
                        str(pick(o, "itemCode", "ItemCode", "ItemNo")), pick(o, "sellerItemCode", "SellerItemCode", "SellerCode"),
                        pick(o, "itemTitle", "ItemTitle"), pick(o, "option", "Option", "OptionInfo"),
                        int(num(pick(o, "orderQty", "OrderQty", default=1))),
                        num(pick(o, "total", "Total", "orderPrice", "OrderPrice", default=0)),
                        dt(pick(o, "orderDate", "OrderDate")), dt(pick(o, "PaymentDate", "paymentDate", "orderDate")),
                        dt(pick(o, "ShippingDate", "shippingDate")), norm_status(pick(o, "shippingStatus", "ShippingStatus", "ShippingStat")),
                        pick(o, "DeliveryCompany", "deliveryCompany"), pick(o, "TrackingNo", "trackingNo"),
                        pick(o, "buyer", "Buyer", "receiver"), json.dumps(o, ensure_ascii=False), now))
            np, no = len(prods), len(ords)
            msg = f"API 조합: {q.combo}"
        ok = 1
    except Exception as e:
        ok, msg = 0, f"{e}"[:1500]
        traceback.print_exc()
    finally:
        _lock.release()
    with db() as c:
        c.execute("INSERT INTO sync_log(started,finished,ok,products,orders,message) VALUES(?,?,?,?,?,?)",
                  (started, datetime.now().isoformat(timespec="seconds"), ok, np, no, msg))
    return {"ok": bool(ok), "products": np, "orders": no, "message": msg}


def seed_demo():
    random.seed(7)
    names = [("BESTコレクション 耳つぼジュエリー", 2325, 3000), ("小顔 耳つぼシール 24粒", 1650, 2200),
             ("スワロフスキー 耳つぼジュエリー ピュアホワイト", 2980, 3800), ("五行 お守りブレスレット", 3480, 4500),
             ("耳つぼジュエリー お試しセット", 990, 1500), ("ゴールド 耳つぼ 48粒", 2750, 3500),
             ("ローズクォーツ 耳つぼジュエリー", 2480, 3200), ("耳つぼマップ付き スターターキット", 3980, 4980)]
    now = datetime.now()
    with db() as c:
        c.execute("DELETE FROM products"); c.execute("DELETE FROM orders")
        for i, (t, p, r) in enumerate(names):
            c.execute("REPLACE INTO products VALUES(?,?,?,?,?,?,?,?,?)",
                      (f"10{i+1:07d}", f"WBL-{i+1:03d}", t, p, r, random.choice([3, 8, 25, 60, 140]), "S2", "{}", now.isoformat()))
        n = 0
        for d in range(90, -1, -1):
            day = now - timedelta(days=d)
            mega = 1 if d in range(30, 36) else 0
            for _ in range(random.randint(0, 2) + mega * random.randint(8, 16)):
                i = random.choices(range(8), weights=[8, 3, 3, 2, 2, 1, 1, 1])[0]
                t, p, _r = names[i]
                q = random.choice([1, 1, 1, 2])
                pay = day.replace(hour=random.randint(0, 23), minute=random.randint(0, 59))
                age = (now - pay).days
                if age <= 1: st = "배송요청"
                elif age <= 3: st = random.choice(["배송요청", "배송준비", "배송중"])
                elif age <= 7: st = random.choice(["배송요청", "배송중", "배송완료", "배송완료"])
                else: st = random.choices(["배송완료", "구매확정", "배송요청"], weights=[3, 6, 0.3])[0]
                ship = "" if st in ("배송요청", "배송준비") else (pay + timedelta(days=random.choice([1, 2, 2, 3, 5]))).strftime("%Y-%m-%d %H:%M:%S")
                n += 1
                c.execute("REPLACE INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (f"9{n:08d}", f"P{n:07d}", f"10{i+1:07d}", f"WBL-{i+1:03d}", t, "", q, p * q,
                           pay.strftime("%Y-%m-%d %H:%M:%S"), pay.strftime("%Y-%m-%d %H:%M:%S"), ship, st,
                           "ヤマト運輸" if ship else "", f"{random.randint(10**11, 10**12)}" if ship else "",
                           f"購入者{n}", "{}", now.isoformat()))
    return len(names), n


def scheduler():
    time.sleep(3)
    while True:
        sync()
        time.sleep(SYNC_MIN * 60)


threading.Thread(target=scheduler, daemon=True).start()


# ── 집계 ─────────────────────────────────────────────
def since(days):
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S") if days else "0000"


def delay_info(o):
    pay = datetime.strptime(o["pay_date"], "%Y-%m-%d %H:%M:%S") if o["pay_date"] else None
    if not pay:
        return None, False
    if o["ship_date"]:
        took = (datetime.strptime(o["ship_date"], "%Y-%m-%d %H:%M:%S") - pay).days
        return took, took > DELAY_DAYS
    waited = (datetime.now() - pay).days
    return waited, waited > DELAY_DAYS and o["status"] in ("배송요청", "배송준비")


@app.get("/api/summary", dependencies=[Depends(auth)])
def summary(days: int = 30):
    with db() as c:
        s = c.execute("SELECT COUNT(*) n, COALESCE(SUM(amount),0) rev, COUNT(DISTINCT buyer) buyers FROM orders WHERE pay_date>=?", (since(days),)).fetchone()
        prev = c.execute("SELECT COALESCE(SUM(amount),0) rev FROM orders WHERE pay_date>=? AND pay_date<?", (since(days * 2), since(days))).fetchone() if days else None
        prods = c.execute("SELECT COUNT(*) n, SUM(qty<10) low FROM products").fetchone()
        open_orders = [dict(r) for r in c.execute("SELECT * FROM orders WHERE status IN ('배송요청','배송준비')")]
        last = c.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
    delayed = sum(1 for o in open_orders if delay_info(o)[1])
    return {"demo": DEMO, "delay_days": DELAY_DAYS, "revenue": s["rev"], "orders": s["n"],
            "aov": s["rev"] / s["n"] if s["n"] else 0, "buyers": s["buyers"],
            "revenue_prev": prev["rev"] if prev else None, "products": prods["n"], "low_stock": prods["low"] or 0,
            "open_orders": len(open_orders), "delayed": delayed, "last_sync": dict(last) if last else None}


@app.get("/api/daily", dependencies=[Depends(auth)])
def daily(days: int = 30):
    with db() as c:
        rows = c.execute("SELECT substr(pay_date,1,10) d, SUM(amount) rev, COUNT(*) n FROM orders WHERE pay_date>=? GROUP BY d ORDER BY d",
                         (since(days or 3650),)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/products", dependencies=[Depends(auth)])
def products(days: int = 30, q: str = "", flag: str = ""):
    with db() as c:
        rows = c.execute("""SELECT p.*, COALESCE(SUM(o.qty),0) sold, COALESCE(SUM(o.amount),0) rev, COUNT(o.order_no) n
            FROM products p LEFT JOIN orders o ON o.item_code=p.item_code AND o.pay_date>=?
            GROUP BY p.item_code ORDER BY rev DESC""", (since(days),)).fetchall()
    out = []
    for r in rows:
        d = dict(r); d.pop("raw", None)
        d["discount"] = (1 - d["price"] / d["retail_price"]) * 100 if d["retail_price"] else 0
        d["daily_sold"] = d["sold"] / (days or 90)
        d["days_left"] = d["qty"] / d["daily_sold"] if d["daily_sold"] else None
        if q and q.lower() not in f"{d['title']} {d['seller_code']} {d['item_code']}".lower():
            continue
        if flag == "low" and d["qty"] >= 10: continue
        if flag == "nosale" and d["n"] > 0: continue
        if flag == "top" and d["rev"] == 0: continue
        out.append(d)
    return out


@app.get("/api/orders", dependencies=[Depends(auth)])
def orders(days: int = 30, status: str = "", delayed: bool = False, q: str = ""):
    with db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM orders WHERE pay_date>=? ORDER BY pay_date DESC", (since(days),))]
    out = []
    for o in rows:
        o.pop("raw", None)
        o["elapsed"], o["is_delayed"] = delay_info(o)
        if status and o["status"] != status: continue
        if delayed and not (o["is_delayed"] and not o["ship_date"]): continue
        if q and q.lower() not in f"{o['order_no']} {o['title']} {o['buyer']} {o['tracking']}".lower(): continue
        out.append(o)
    return out[:2000]


@app.post("/api/sync", dependencies=[Depends(auth)])
def sync_now():
    return sync()


@app.get("/api/sync-log", dependencies=[Depends(auth)])
def sync_log():
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 50")]


@app.get("/", dependencies=[Depends(auth)])
def index():
    return FileResponse(Path(__file__).with_name("static") / "index.html")
