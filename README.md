# 큐텐 운영 에이전트 (Qoo10 Japan 대시보드)

통합 대시보드 · 상품별 분석 · 배송지연 관리 · 주문 조회 · 동기화 히스토리.
큐텐 QAPI에서 상품·주문을 자동으로 가져와(기본 60분마다) SQLite에 저장하고 화면에 보여줍니다.
`QOO10_KEY`가 없으면 데모 데이터로 뜹니다.

## Railway 배포 (원가 분석 에이전트와 같은 방식)
1. 이 폴더를 GitHub 새 저장소에 올린다 (예: `wbl-qoo10-dash`)
2. Railway → New Project → Deploy from GitHub repo → 저장소 선택
3. Variables 탭에 환경변수 추가
   | 이름 | 값 |
   |---|---|
   | QOO10_KEY | 큐텐 셀러 인증키 |
   | DASH_PASSWORD | 접속 비밀번호 (브라우저 로그인 창에서 아이디는 아무거나) |
   | DELAY_DAYS | 배송지연 기준일 (기본 3) |
   | SYNC_MINUTES | 동기화 주기 분 (기본 60) |
   | ANTHROPIC_API_KEY | (선택) 상품 등록 화면의 AI 일본어 초안용 |
   | TZ | Asia/Seoul |
   | DB_PATH | `/data/qoo10.db` (아래 볼륨 사용 시) |
4. Settings → Volumes → `/data` 마운트 (재배포해도 데이터 유지)
5. Settings → Networking → Generate Domain → 접속

## 로컬 실행
```
pip install -r requirements.txt
set QOO10_KEY=...   (Mac: export QOO10_KEY=...)
uvicorn app:app --reload
```
→ http://localhost:8000

## 큐텐 API 주의
- 호출 방식·버전이 계정마다 달라 `qoo10.py`가 (신형 POST / 경로형 GET) × (v1.1, 1.0, 1.2)를 자동 시도하고 성공 조합을 기억합니다.
- 전부 실패하면 '동기화 히스토리'에 조합별 에러가 남습니다. `-90001 存在しないAPIです`가 모두 뜨면
  키가 셀러 인증키가 아닐 가능성 → QSM에서 셀러 인증키 확인/재발급.
- 사용 메서드: `ItemsLookup.GetAllGoodsInfo`(상품), `ShippingBasic.GetShippingInfo_v2`(주문).
  응답 필드명은 여러 후보로 매핑했지만 실제 응답으로 검증 전입니다. 첫 동기화 후 값이 비는 칸이 있으면 DB의 raw 컬럼을 보고 `app.py`의 `pick(...)` 키를 맞추세요.

## 상품 등록 화면
- 사이드바 '상품 등록': ① 기존 상품 복제(카테고리·배송·연락처) → ② 한국어 정보로 AI 일본어 초안 → ③ 가격·재고·옵션 → ④ 이미지 업로드 → ⑤ 미리보기·최종 확인 후 등록
- 같은 셀러코드는 두 번 등록되지 않게 막음. 등록 기록은 '등록 이력'
- 업로드한 이미지는 서버의 `/img/...` 주소로 공개되어 큐텐이 가져감 → **Volume(/data) + DB_PATH=/data/qoo10.db 설정 필수** (없으면 재배포 때 이미지가 사라져 상세페이지 이미지가 깨질 수 있음)
- 등록은 ItemsBasic.SetNewGoods, 신형 POST v1.1→1.0 순서 (존재하지 않는 API일 때만 다음 버전 시도 → 중복 등록 없음)
