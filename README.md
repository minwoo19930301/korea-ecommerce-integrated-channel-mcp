# Korea E-commerce Integrated Channel MCP

<!-- PROJECT-PRESENTATION:START -->
<a href="https://github.com/minwoo19930301/korea-ecommerce-integrated-channel-mcp"><img src=".github/project-cover.svg" alt="Korea E-commerce Integrated Channel MCP" width="960"></a>

[![QUICK START](https://img.shields.io/badge/QUICK%20START-374151?style=for-the-badge)](#실제-사용-흐름) [![SOURCE](https://img.shields.io/badge/SOURCE-444444?style=for-the-badge)](https://github.com/minwoo19930301/korea-ecommerce-integrated-channel-mcp)
<!-- PROJECT-PRESENTATION:END -->

상품이 10개일 때는 판매자센터를 몇 번 오가며 직접 등록할 수 있습니다. 상품이 1,000개, 10,000개가 되면 이야기가 달라집니다. 판매처마다 화면이 다르고, 같은 가격과 재고를 여러 번 입력해야 하며, 한 곳에서 빠뜨린 수정 사항이 그대로 판매 사고로 이어지기도 합니다.

Korea E-commerce Integrated Channel MCP는 이런 반복 작업을 한곳으로 모으기 위해 만든 복합 MCP 서버입니다. 한 번 정리한 상품 정보를 네이버 스마트스토어, 쿠팡, 11번가, 지마켓·옥션에 맞춰 등록하고, 이후 수정·판매중지·재개·삭제도 같은 방식으로 처리합니다.

MCP는 Codex, Claude 같은 AI 에이전트가 외부 프로그램을 도구처럼 호출할 수 있게 해주는 연결 방식입니다. 판매자는 여러 관리자 화면을 하나씩 조작하는 대신 에이전트에게 원하는 작업을 설명하고, 실제 반영 전에 결과를 확인할 수 있습니다.

## 어떤 일을 대신하나요?

| 지금까지 하던 일 | 이 MCP를 연결한 뒤 |
| --- | --- |
| 판매처마다 로그인해서 같은 상품을 다시 입력 | 상품 정보를 한 번 전달하고 여러 판매처에 등록 |
| 가격이나 재고가 바뀔 때마다 각 화면을 찾아 수정 | 같은 상품을 선택해 여러 판매처를 함께 수정 |
| 품절 상품을 판매처별로 찾아 판매중지 | 한 번의 요청으로 판매중지 또는 판매재개 |
| 어느 판매처에서 실패했는지 따로 기록 | 판매처별 성공과 실패 결과를 한곳에서 확인 |
| 실수로 다른 상품을 수정하거나 삭제할까 걱정 | 실제 반영 전 미리보기와 별도 승인을 거쳐 실행 |

## 이런 분에게 맞습니다

- 여러 국내 판매처에서 같은 상품을 판매하는 셀러
- 상품 수가 많아 판매자센터의 반복 입력이 부담스러운 운영팀
- 엑셀이나 내부 상품 DB를 바탕으로 등록 작업을 자동화하려는 개발자
- AI 에이전트에게 상품 운영 업무를 맡기되, 실제 반영 전에는 직접 확인하고 싶은 팀

## 지원 판매처

| 판매처 | 현재 지원하는 작업 | 상태 |
| --- | --- | --- |
| 네이버 스마트스토어 | 등록, 수정, 판매중지, 재개, 삭제 | 판매자용 커머스API 연결 코드 포함 |
| 쿠팡 | 등록, 수정, 판매중지, 재개, 삭제 | 옵션 상품까지 판매 상태 변경 |
| 11번가 | 등록, 수정, 판매중지, 재개, 삭제 | 판매자 계정별 규격 확인이 필요한 실험 단계 |
| 지마켓·옥션 | 등록, 수정, 판매중지, 재개, 삭제 | ESM 통합 상품 기준 |

실제 판매자 계정의 카테고리, 배송지, 반품지, 상품고시 번호는 계정마다 다릅니다. 저장소에 연결 코드는 들어 있지만, 바로 실상품을 올리는 완성형 서비스는 아닙니다. 처음에는 폐기 가능한 테스트 상품 한 개로 연결을 확인해야 합니다.

### 네이버 쇼핑 API가 종료된다던데요?

2026년 7월 31일 종료되는 API는 상품 검색 결과를 가져오던 네이버 개발자센터의 `검색 > 쇼핑 API`입니다. 이 프로젝트가 사용하는 것은 스마트스토어 판매자가 상품을 등록하고 수정하는 `커머스API`라서 서로 다른 서비스입니다.

네이버 커머스API는 2026년 7월 7일에도 2.82.0 문서가 갱신됐고, 현재 상품 등록·수정·삭제 기능을 제공하고 있습니다. 따라서 스마트스토어 연결은 그대로 유지합니다.

- [쇼핑 검색 API 종료 공지](https://developers.naver.com/notice/article/32564)
- [스마트스토어 커머스API 소개](https://apicenter.commerce.naver.com/docs/introduction)
- [커머스API 상품 문서](https://apicenter.commerce.naver.com/docs/commerce-api/current/%EC%83%81%ED%92%88)

## 실제 사용 흐름

1. 판매처별 API 자격증명을 로컬 환경에 넣습니다.
2. 각 계정에서 쓰는 카테고리, 배송 정책, 상품고시 정보를 판매처별 양식에 채웁니다.
3. 에이전트에게 상품과 올릴 판매처를 말하고 미리보기를 요청합니다.
4. 내용이 맞으면 미리보기에서 받은 승인값으로 실제 등록을 실행합니다.

에이전트에는 이렇게 요청할 수 있습니다.

> SKU `DEMO-001` 상품을 스마트스토어와 쿠팡에 올릴 준비를 해줘. 실제 등록은 하지 말고 판매처별로 어떤 내용이 전송될지 먼저 보여줘.

미리보기를 확인한 뒤에는 같은 요청을 그대로 실행합니다. 상품명, 가격, 대상 판매처가 바뀌면 다시 미리보기를 받아야 합니다. 영구 삭제에는 한 번 더 확인 절차가 붙습니다.

## 5분 안에 로컬에서 열기

Python 3.11 이상이 필요합니다.

```bash
git clone https://github.com/minwoo19930301/korea-ecommerce-integrated-channel-mcp.git
cd korea-ecommerce-integrated-channel-mcp
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
pytest -q
```

서버 실행:

```bash
korea-ecommerce-integrated-channel-mcp
```

자격증명을 넣지 않아도 미리보기와 테스트는 실행할 수 있습니다. 실제 판매처 반영은 기본값으로 꺼져 있습니다.

설치 경로, 환경변수, MCP 클라이언트 설정은 [설치 가이드](docs/SETUP.md)에 정리했습니다.

## 현재 한계

- 실제 판매자 자격증명이 없어 실상품 등록 테스트는 하지 않았습니다.
- 판매처마다 필수값이 달라 계정에 맞는 등록 양식을 먼저 만들어야 합니다.
- 11번가는 판매자 계약에 따라 요청 주소와 XML 형식이 달라질 수 있습니다.
- 여러 판매처에 동시에 요청하더라도 한 판매처의 실패를 다른 판매처에서 자동으로 되돌리지는 않습니다.

개발 구조, 안전 규칙, 테스트와 배포 절차는 [AGENTS.md](AGENTS.md)를 참고하세요. 세부 데이터 흐름은 [아키텍처 문서](docs/ARCHITECTURE.md), 보안 관련 주의사항은 [SECURITY.md](SECURITY.md)에 있습니다.

## 라이선스

MIT
