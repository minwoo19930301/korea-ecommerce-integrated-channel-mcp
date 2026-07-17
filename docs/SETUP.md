# 설치와 연결

## 1. 설치

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
pytest -q
```

## 2. 판매처 자격증명

`.env`에 사용할 판매처의 값만 채웁니다. 이 파일은 Git에 올리지 않습니다.

```dotenv
KEIC_NAVER_CLIENT_ID=
KEIC_NAVER_CLIENT_SECRET=
KEIC_NAVER_ACCOUNT_ID=

KEIC_COUPANG_VENDOR_ID=
KEIC_COUPANG_ACCESS_KEY=
KEIC_COUPANG_SECRET_KEY=

KEIC_ELEVENST_API_KEY=

KEIC_ESM_MASTER_ID=
KEIC_ESM_SECRET_KEY=
KEIC_ESM_GMARKET_SELLER_ID=
KEIC_ESM_AUCTION_SELLER_ID=
KEIC_ESM_ISSUER=
```

실제 반영을 켤 때는 데이터베이스와 프로필 폴더를 절대경로로 지정해야 합니다.

```dotenv
KEIC_ALLOW_MUTATIONS=true
KEIC_DATABASE_PATH=/absolute/path/korea_ecommerce_integrated_channel.sqlite3
KEIC_PROFILE_DIRECTORY=/absolute/path/profiles
```

## 3. 판매처별 등록 양식

`profiles/example.json`은 어떤 모양으로 값을 넣는지 보여주는 예시입니다. 실제 사용 전에는 판매자 계정에서 쓰는 카테고리, 출고지, 반품지, 배송 정책, 상품고시, 옵션 항목을 채운 별도 프로필을 만들어야 합니다.

프로필에는 `{{product.name}}`, `{{product.price}}`, `{{product.stock}}` 같은 자리표시자를 쓸 수 있습니다. 서버는 실행 전에 기준 상품 정보로 이 값을 채웁니다.

## 4. MCP 클라이언트 연결

```json
{
  "mcpServers": {
    "korea-ecommerce-integrated-channel": {
      "command": "/absolute/path/korea-ecommerce-integrated-channel-mcp/.venv/bin/korea-ecommerce-integrated-channel-mcp",
      "cwd": "/absolute/path/korea-ecommerce-integrated-channel-mcp",
      "env": {
        "KEIC_DATABASE_PATH": "/absolute/path/korea_ecommerce_integrated_channel.sqlite3",
        "KEIC_PROFILE_DIRECTORY": "/absolute/path/korea-ecommerce-integrated-channel-mcp/profiles"
      }
    }
  }
}
```

처음에는 `KEIC_ALLOW_MUTATIONS=false`를 유지합니다. `channel_capabilities`, `channel_health`, `profile_list`, `profile_preview` 순서로 연결 상태와 등록 내용을 확인한 다음 폐기 가능한 상품 한 개로 테스트합니다.

## 5. 상품 등록 요청 구조

에이전트가 `product_publish`를 호출할 때는 기준 상품, 대상 판매처, 판매처별 등록 양식 또는 프로필, 중복 실행을 막기 위한 작업 키를 전달합니다.

```json
{
  "command": {
    "product": {
      "sku": "DEMO-001",
      "name": "테스트 상품",
      "price": 19900,
      "stock": 10,
      "description_html": "<p>설명</p>",
      "image_urls": ["https://example.com/product.jpg"]
    },
    "channels": ["smartstore", "coupang"],
    "profile_id": "example",
    "idempotency_key": "catalog-DEMO-001-v1",
    "dry_run": true,
    "confirm": "PREVIEW"
  }
}
```

미리보기 응답의 `approval_token`을 실행 요청에 추가하고 `dry_run=false`, `confirm="EXECUTE"`로 바꾸면 실제 요청이 전송됩니다. 그 사이 상품 내용이나 대상 판매처를 바꾸면 토큰이 거부되므로 다시 미리보기해야 합니다.
