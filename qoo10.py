"""Qoo10 Japan QAPI 클라이언트.

호출 방식(신형 헤더형 / 경로형)과 API 버전이 아직 확정되지 않아서,
첫 호출 때 조합을 자동으로 시도하고 성공한 조합을 기억한다.
"""
import os, requests

API = "https://api.qoo10.jp/GMKT.INC.Front.QAPIService/ebayjapan.qapi"
VERSIONS = [v.strip() for v in os.getenv("QOO10_VERSIONS", "1.0,1.1,1.2").split(",")]


class Qoo10Error(Exception):
    pass


class Qoo10:
    def __init__(self, key: str):
        self.key = key
        self.combo = {}  # method -> (style, version)

    def _post(self, method, params, version):
        r = requests.post(API, timeout=40,
                          headers={"GiosisCertificationKey": self.key, "QAPIVersion": version},
                          data={"method": method, "returnType": "application/json", **params})
        return self._parse(r)

    def _path(self, method, params, version):
        r = requests.get(f"{API}/{method}", timeout=40,
                         params={"key": self.key, "v": version, "returnType": "application/json", **params})
        return self._parse(r)

    @staticmethod
    def _parse(r):
        try:
            return r.json()
        except ValueError:
            return {"ErrorCode": "NON_JSON", "ErrorMsg": r.text[:200]}

    @staticmethod
    def _ok(res):
        return "ErrorCode" not in res and str(res.get("ResultCode", "0")) == "0"

    def call(self, method, params=None):
        params = params or {}
        styles = {"post": self._post, "path": self._path}
        tried = []
        order = [self.combo[method]] if method in self.combo else \
            [(s, v) for v in VERSIONS for s in ("post", "path")]
        for style, ver in order:
            res = styles[style](method, params, ver)
            if self._ok(res):
                self.combo[method] = (style, ver)
                return res
            tried.append(f"{style}/v{ver}: {res.get('ErrorCode') or res.get('ResultCode')} {res.get('ErrorMsg') or res.get('ResultMsg')}")
        raise Qoo10Error(f"{method} 실패 — " + " | ".join(tried))

    # ── 조회 ─────────────────────────────────
    def products(self):
        items, page = [], 1
        while page <= 50:
            res = self.call("ItemsLookup.GetAllGoodsInfo", {"ItemStatus": "S2", "Page": str(page)})
            rows = _rows(res)
            items += rows
            if len(rows) < 500:
                break
            page += 1
        return items

    def orders(self, start: str, end: str, status: str = ""):
        """start/end: YYYYMMDD. status: 1=배송요청 2=배송준비 3=배송중 4=배송완료 5=구매확정 (빈 값=전체)"""
        p = {"search_Sdate": start, "search_Edate": end, "search_condition": "1"}
        if status:
            p["ShippingStat"] = status
        return _rows(self.call("ShippingBasic.GetShippingInfo_v2", p))


def _rows(res):
    obj = res.get("ResultObject")
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for k in ("Items", "items", "ItemList", "Orders"):
            if isinstance(obj.get(k), list):
                return obj[k]
        return [obj]
    return []
