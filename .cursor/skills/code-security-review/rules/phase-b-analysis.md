# Phase B: AI 소스 코드 분석 규칙

> Phase B sub-agent가 읽는 규칙 파일.
> **라운드 기반 실행 (Ralph Loop 철학)**:
> - Orchestrator가 9개 카테고리를 3 라운드로 분할하여 별도 sub-agent에 dispatch
> - 각 sub-agent는 **자기 라운드 섹션만** 실행 (다른 라운드 무시)
> - 라운드 분할로 단일 agent의 context 고갈 방지 + 전 카테고리 완전 커버리지 보장

---

## 공통 사항

### 점검 기준

> **OWASP Top 10 (2021)** + **OWASP API Top 10 (2023)** + **OWASP Top 10 for LLM (2025)** + **KISA SW 개발보안 가이드 47개 항목** 기반.
> Phase A 도구(Semgrep, Gitleaks, Trivy)가 패턴/시그니처로 잘 탐지하는 영역은 도구에 위임.
> AI는 도구가 **맥락 이해 부족으로 구조적으로 탐지 불가능한 영역**에 집중.

### 핵심 원칙

- **위협 질문에 답하는 것이 목표입니다.** 질문의 답을 찾기 위해 코드를 탐색하세요.
- 도구가 잘 잡는 영역 (Semgrep=패턴, Gitleaks=시크릿, Trivy=CVE/IaC)은 중복 불필요.
- **자기 라운드 카테고리만 분석.** 다른 라운드는 다른 sub-agent가 담당.
- 도구가 이미 탐지한 것과 동일한 취약점 발견 시 JSON에 포함 (중복 제거는 merge_findings.py가 hash 기반으로 처리).

### `not_applicable` 판정 규칙 (모든 라운드 공통)

> **Skip 방지**: 모든 점검 항목은 반드시 Grep/Read로 확인한 뒤에만 판정할 수 있습니다.

1. **evidence 필수**: `not_applicable` 판정 시 `"evidence"` 필드에 근거를 기록해야 합니다
   - 예: `"Grep 'jwt|token' 0건"`, `"auth middleware Read 완료 — 해당 패턴 미존재"`
   - evidence 없는 `not_applicable`은 **무효**
2. **Grep 선행 의무**: 각 카테고리의 보조 Grep 패턴을 **반드시 실행**한 후 판정
   - Grep을 실행하지 않고 "해당 코드 없음"으로 skip하는 것은 금지
3. **카테고리 전체 skip 금지**: 한 카테고리의 모든 항목을 `not_applicable`로 처리하려면, 해당 카테고리의 **모든 보조 Grep 패턴 결과가 0건**이어야 함
   - 하나라도 매치가 있으면 관련 항목을 `checked_ok` 또는 `finding`으로 판정
4. **Orchestrator 검증 대상**: Orchestrator가 사전 Grep으로 코드 존재를 확인한 카테고리는, sub-agent가 `not_applicable`로 처리할 수 없음 (프롬프트에 명시됨)

### 컴포넌트 발견 (모든 라운드 공통 첫 단계)

1. `Glob`으로 프로젝트 루트 디렉토리 구조 파악
2. 각 컴포넌트 식별 (백엔드, 프론트엔드, 관리자 서비스, 에이전트 등)
3. 컴포넌트별 기술 스택 파악 (Go/Python/TypeScript 등)
4. **모든 컴포넌트를 분석 대상에 포함** — 하나라도 빠뜨리지 않는다
5. 발견된 컴포넌트 목록을 출력 JSON의 `component_coverage`에 기록

---

## 라운드 1: 인증/인가 + 비즈니스 로직 + 에러 처리

> Orchestrator가 "라운드 1"을 지시했으면 이 섹션을 실행하세요.

### 위협 질문

아래 질문에 **각 컴포넌트마다** 답하세요. 질문의 답을 찾기 위해 코드를 탐색합니다.

1. **인증 우회**: 공격자가 인증 없이 보호된 리소스에 접근할 수 있는 모든 경로는?
   - 인증 미들웨어가 빠진 라우터 그룹
   - 환경변수 토글로 인증 비활성화 가능 여부
   - Mock/Debug 헤더로 인증 우회 가능한 분기
   - URL 정규화 우회 (double encoding, 트레일링 슬래시, `/../`, 대소문자 변형)로 경로 기반 인증 bypass
   - `X-HTTP-Method-Override` 헤더로 메서드 기반 인가 우회
2. **인증 체인 장애**: 외부 인증 서비스(IAM, OAuth)가 장애일 때, fallback이 보안을 약화시키는가?
   - introspect/validate 실패 시 unsigned 토큰 허용
   - DB 연결 실패 시 인증 bypass
3. **토큰 신뢰**: JWT/커스텀 토큰의 서명·만료 검증이 완전한가?
   - 알고리즘 미고정 (none 허용), exp 미검증, exp==0/음수 시 만료 우회
   - 표준 JWT 외 자체 토큰의 서명/만료 검증 결함
   - 기본 시크릿 하드코딩
   - `aud`(audience) / `scope` 미검증 → 다른 서비스/환경용 토큰 재사용
   - WebSocket/SSE 인증이 query parameter로 토큰 전달 (로그·프록시·Referer 노출)
4. **권한 상승**: 사용자 A가 사용자 B의 데이터를 조회/수정할 수 있는 경로는?
   - 객체 접근 시 소유권 검증 누락 (IDOR)
   - 관리자 API에 일반 사용자 접근 (BFLA)
   - 자동 생성 계정(JIT provisioning)이 과도한 기본 권한을 갖는 경우
5. **Fail-open**: 인증/인가 실패 또는 에러 시 요청이 그대로 통과하는 곳은?
6. **정보 노출**: 에러 응답이 내부 구조(스택트레이스, DB 쿼리, 파일 경로)를 드러내는 곳은?

### 분석 절차

각 컴포넌트에 대해 수행:

1. **보조 Grep 패턴 실행 (필수)**: 아래 "보조 Grep 패턴" 섹션의 **모든 패턴을 실행**하고 결과를 기록. `not_applicable` 판정은 공통 규칙("not_applicable 판정 규칙") 준수
2. **인증 구조 파악**: `Glob`으로 `*auth*`, `*middleware*`, `*jwt*`, `*session*` 검색 → 각 파일 `Read`
3. **모든 조건 분기 추적**: 정상/실패/에러 경로 → fallback 동작, 외부 서비스 장애 시 동작 확인
4. **JWT 검증**: 알고리즘 고정, `exp` 필수 여부, 기본 시크릿 (Grep: `jwt.Parse, jwt.Verify, algorithm, exp`)
5. **커스텀 토큰**: 표준 JWT 외 자체 토큰 서명/만료 검증 로직 추적
6. **환경변수 토글**: Grep `AUTH_ENABLED, SKIP_AUTH, BYPASS_AUTH` → 비활성 시 전체 bypass 가능성
7. **라우터 매핑**: 라우터 등록 파일 `Read` → 인증 미적용 그룹 나열. `/internal/`, `/debug/`, `/admin` 특별 확인
8. **Mock/Debug 헤더**: Grep `X-Mock-, X-Debug-, X-Test-` → 프로덕션 분기 여부
9. **IDOR/BFLA**: DB 조회 시 소유권 검증, 관리자 API 접근 제어, JIT 프로비저닝 기본 권한 확인
10. **에러 처리**: 에러 응답에 내부 정보 노출 여부, 보안 이벤트 로깅 여부, 디버그 코드 잔존 여부

### 보조 Grep 패턴

```
Auth 토글: AUTH_ENABLED, ENABLE_AUTH, SKIP_AUTH, BYPASS_AUTH, authRequired
Mock/디버그: X-Mock-, X-Debug-, X-Test-, mock-user, test-user
기본 시크릿: default-jwt, default-secret, changeme, your-secret-key
JWT: jwt.Parse, jwt.Verify, jwt.Sign, algorithm, exp, iat, nbf
Fail-open: return nil, return next, c.Next() (인증 파일 내)
CSRF: csrf_exempt, csrfProtection, csurf, SameSite
쿠키: set-cookie, httpOnly, secure, sameSite, cookie(, session(
Client Token: localStorage.setItem, sessionStorage.setItem
OAuth: oauth, openid, state=, redirect_uri, authorization_code
디버그: debug=True, DEBUG=, app.debug, NODE_ENV
Mass Assignment: Object.assign(, _.merge(, **kwargs
WS 토큰: access_token=, token=, ?token, query.token, query.access_token
Method Override: X-HTTP-Method-Override, X-Method-Override, _method
aud/scope: audience, aud, scope, iss (JWT 검증 파일 내)
URL 정규화: normalize(, filepath.Clean(, path.Clean(, url.Parse(
```

### 분류 참조: 점검 항목

> 발견한 취약점을 아래 항목으로 분류하세요.

| 카테고리 | 점검 항목 | 위험도 | 근거 |
|----------|----------|--------|------|
| **auth_logic** | 인증 미적용 | Critical | A07, API2, KISA 보안기능#1 |
| | JWT 결함 (알고리즘, 만료, none) | Critical/High | A07, API2 |
| | 인증 외부 의존성 장애 시 fallback | Critical/High | A07, CWE-636 |
| | 커스텀 토큰 서명 검증 결함 | High | A02, A07 |
| | 세션 관리 (고정, 만료, 미재발급) | High | A07, KISA 보안기능#15-16 |
| | Refresh Token (회전, 취소 불가) | High | API2 |
| | Client-side Token 저장 (localStorage) | High | A02 |
| | CSRF 보호 누락 | High | A01, KISA 입력검증#8 |
| | 쿠키 보안 속성 누락 | Medium/High | KISA 보안기능#10 |
| | OAuth/OpenID 취약점 | High | A07, CWE-346 |
| | 비밀번호 재설정 취약점 | High | A07, CWE-640 |
| | WS/SSE query param 토큰 노출 | High | A07, CWE-598 |
| | Token aud/scope 미검증 (토큰 재사용) | High | A07, CWE-287 |
| | URL 정규화 우회 (경로 기반 인증 bypass) | High | A01, CWE-706 |
| | HTTP Method Override 인가 우회 | Medium | A01, CWE-650 |
| | 계정 열거 | Medium | A07, CWE-204 |
| **business_logic** | IDOR / BOLA | Critical/High | A01, API1, KISA 보안기능#2 |
| | BFLA | High | A01, API5 |
| | 자동 프로비저닝(JIT) 기본 권한 과도 | High | A01, API5, CWE-269 |
| | Mass Assignment | High | API3 |
| | 비즈니스 값 조작 | High/Medium | A04, API6 |
| | Business Flow Abuse | High/Medium | API6 |
| | 파일 업로드 검증 부재 | High | A04, KISA 입력검증#6 |
| **error_handling** | Fail-open | Critical/High | A07 |
| | 정보 노출 (스택트레이스, 쿼리, 경로) | Medium | A05, KISA 에러처리#1 |
| | 예외 처리 누락 | Medium | KISA 에러처리#2-3 |
| | Log Injection | Medium | A09 |
| | 보안 이벤트 로깅 부재 | Medium | A09, KISA |
| | 디버그 코드 잔존 | Medium/High | A05, KISA 캡슐화#2 |

---

## 라운드 2: 데이터 흐름 + 동시성 + 암호화

> Orchestrator가 "라운드 2"를 지시했으면 이 섹션을 실행하세요.

### 위협 질문

아래 질문에 **각 컴포넌트마다** 답하세요.

1. **입력 신뢰**: 사용자 입력이 검증 없이 민감한 연산(DB 쿼리, OS 명령, 템플릿 렌더링)에 도달하는 경로는?
   - cross-function taint: 입력 → 처리 → DB/출력까지 추적
   - DB에서 읽은 값을 검증 없이 재사용 (2차 주입)
2. **외부 데이터 신뢰**: 외부에서 주입 가능한 데이터(Host 헤더, postMessage, 역직렬화)가 신뢰되는 곳은?
   - `request.host`로 URL/이메일 링크 생성
   - `postMessage` 수신 시 origin 미검증
   - pickle/yaml/XML 역직렬화
   - HTTP Request Smuggling: 리버스 프록시와 백엔드 간 Content-Length / Transfer-Encoding 해석 차이
   - Cache Poisoning: Host/X-Forwarded-Host 등 unkeyed 헤더로 CDN/캐시 오염
3. **동시성 공격**: 동시 요청이 보호 없이 동일 자원(잔액, 재고, 권한)을 조작할 수 있는 곳은?
   - TOCTOU, Race Condition
   - 리소스 누수 (DB 연결/파일 핸들 미해제)
4. **암호화 오용**: 암호화, 해시, 난수가 보안 목적에 부적절하게 사용되는 곳은?
   - ECB 모드, 하드코딩 IV
   - 솔트 없는 비밀번호 해시
   - `math/rand`가 보안 목적에 사용
   - 비밀 값 비교에 constant-time 미적용

### 분석 절차

각 컴포넌트에 대해 수행:

1. **보조 Grep 패턴 실행 (필수)**: 아래 "보조 Grep 패턴" 섹션의 **모든 패턴을 실행**하고 결과를 기록. `not_applicable` 판정은 공통 규칙("not_applicable 판정 규칙") 준수
2. **핵심 API 입력 추적**: 핵심 API의 입력 파라미터 → 처리 함수 → DB/출력 경로를 따라가며, 검증/이스케이프 시점 확인
3. **외부 데이터 신뢰 검증**: Host 헤더, postMessage, 역직렬화 사용처에서 검증 여부 확인
4. **리다이렉트/Open Redirect**: 리다이렉트 URL의 화이트리스트 검증 여부
5. **동시성 보호**: 핵심 자원에 대한 동시 접근 보호 (잠금, 트랜잭션) 확인
6. **ReDoS**: 정규식 중첩 quantifier 확인
7. **암호화 검증**: 알고리즘, 키 길이, 솔트, 난수 생성기, 비교 방식 확인

### 보조 Grep 패턴

```
리다이렉트: redirect(, res.redirect, Location:, window.location
외부 요청: requests.get(, axios(, fetch(, http.get(
역직렬화: JSON.parse(, pickle., yaml.load, deserialize
동적 실행: eval(, exec(, Function(, setTimeout(
SSTI: render_template_string(, Template(, ejs.render(
Prototype Pollution: __proto__, constructor.prototype, _.merge(, deepmerge(
postMessage: addEventListener.*message, postMessage(
Email: sendmail, send_mail, transporter.sendMail, smtp
ReDoS: re.compile(, new RegExp( (중첩 quantifier 확인)
Timing: == secret, === token, == password
Path Traversal: path.join(, os.path.join(, ../, normalize(, realpath(
NoSQL: $where, $regex, $gt, $ne, mongoose.find(, collection.find(
Host Header: request.host, req.hostname, req.headers.host
Request Smuggling: Transfer-Encoding, chunked, Content-Length (프록시 설정 파일)
Cache: Cache-Control, Vary, X-Forwarded-Host, X-Original-URL
```

### 분류 참조: 점검 항목

| 카테고리 | 점검 항목 | 위험도 | 근거 |
|----------|----------|--------|------|
| **data_flow** | 입력→출력 taint tracking | Critical/High | A03, KISA 입력검증#1-2 |
| | Sanitization 위치 (출력 시점 검증) | High | A03 |
| | 2차 주입 | High | A03 |
| | SSTI | Critical/High | A03 |
| | Prototype Pollution | High | A03 |
| | postMessage origin 미검증 | High | A01 |
| | Email Header Injection | High | A03 |
| | Open Redirect | Medium/High | A01, KISA 입력검증#7 |
| | 안전하지 않은 역직렬화 | Critical/High | A08, KISA API오용#2 |
| | Path Traversal (맥락 의존) | High | A01, KISA 입력검증#4 |
| | NoSQL Injection | High | A03, CWE-943 |
| | Host Header Injection | High | A03, CWE-644 |
| | HTTP Parameter Pollution | Medium | A03, CWE-235 |
| | HTTP Request Smuggling (CL/TE) | High | A05, CWE-444 |
| | Cache Poisoning (unkeyed header) | Medium | A05, CWE-349 |
| **concurrency** | TOCTOU | High | KISA 시간상태#1 |
| | Race Condition | High/Medium | KISA 시간상태#1 |
| | 리소스 누수 | Medium | KISA 코드오류#2 |
| | ReDoS | High | A04, API4 |
| **crypto_misuse** | 암호화 모드 (ECB, 하드코딩 IV) | High | A02, KISA 보안기능#4 |
| | 솔트 없는 해시 | High | A02, KISA 보안기능#12 |
| | 맥락별 난수 오용 | Medium | A02, KISA 보안기능#8 |
| | Timing Attack | High | A02 |

---

## 라운드 3: API 설계 + LLM/AI + PII

> Orchestrator가 "라운드 3"을 지시했으면 이 섹션을 실행하세요.

### 위협 질문

아래 질문에 **각 컴포넌트마다** 답하세요.

1. **API 노출**: 보호되지 않거나 과도하게 열린 API 엔드포인트는?
   - 인증 없이 접근 가능한 관리/내부/디버그 엔드포인트
   - 앱 레벨 인증 없이 네트워크 정책에만 의존하는 엔드포인트
   - CORS `*`, Body Size 무제한, Rate Limit 미적용
   - 빌드/배포 설정(.npmrc, CI config, Dockerfile ARG) 내 시크릿 노출
   - Webhook 수신 시 서명/HMAC 미검증 → 위조 이벤트 처리
   - 파일 다운로드 시 Content-Disposition 미설정, MIME sniffing 방지 헤더 누락
2. **LLM 조작**: 클라이언트가 LLM의 system 행동을 조작할 수 있는 경로는?
   - 사용자가 role:"system" 메시지를 직접 전송 가능한지
   - prompt 크기, max_tokens 제한이 있는지
   - SSE 스트리밍 직렬화가 안전한지
3. **LLM 에이전시**: LLM/MCP 도구가 확인 없이 파괴적 작업(삭제, 수정)을 수행할 수 있는 경로는?
   - 삭제/수정 MCP 도구의 확인 게이트 존재 여부
   - RAG 벡터 DB의 테넌트 격리 여부
   - LLM 출력에 대한 검증/필터링 여부
   - SSE data 필드에서 `json.Marshal` 대신 문자열 연결(`fmt.Sprintf`, `+` 연산)로 JSON 구성 → 주입 가능
4. **PII 유출**: 개인정보가 마스킹 없이 저장, 전송, 로깅, 또는 LLM에 전달되는 경로는?
   - 데이터셋 미리보기, API 응답에 PII 노출
   - 로그에 PII 평문 기록
   - 사용자 입력이 마스킹 없이 LLM API로 전달
   - LLM 대화 데이터의 보존/삭제 정책 존재 여부
   - PII 접근에 대한 감사 로그 기록 여부

### 분석 절차

각 컴포넌트에 대해 수행:

1. **보조 Grep 패턴 실행 (필수)**: 아래 "보조 Grep 패턴" 섹션의 **모든 패턴을 실행**하고 결과를 기록. `not_applicable` 판정은 공통 규칙("not_applicable 판정 규칙") 준수
2. **LLM/AI 코드 분석**: Grep 매치가 있는 항목에 대해 system role 주입, PII→LLM 경로, MCP 도구 확인 절차, prompt/max_tokens 제한, SSE 직렬화를 분석
3. **API 표면 점검**: Rate Limiting, CORS, Body Size, SSRF, 보안 헤더 확인
4. **엔드포인트 보호**: 관리/내부/디버그 엔드포인트 인증 여부, 네트워크 의존 보호 여부 확인
5. **빌드/배포 설정**: `.npmrc`, `Dockerfile`, CI config 내 시크릿 노출 확인
6. **Webhook 수신**: `Grep`으로 `webhook, hook, callback, event` 검색 → 서명/HMAC 검증 여부 확인
7. **파일 다운로드**: 파일 제공 핸들러에서 Content-Disposition, X-Content-Type-Options: nosniff 헤더 설정 여부 확인
8. **PII 흐름 추적**: 데이터 수집 → 저장 → 표시 → LLM 전달 경로에서 마스킹 여부, 보존 정책, 접근 감사 확인

### 보조 Grep 패턴

```
CORS: Access-Control-Allow-Origin, cors(, allowedOrigins, credentials
Body Size: BodyLimit, bodyParser, maxBytes, max_content_length
보안 헤더: X-Frame-Options, frame-ancestors, frameguard
GraphQL: depthLimit, costAnalysis, introspection
WebSocket: WebSocket(, ws.on(, wss.on(, socket.io, upgrade
Unsafe consumption: verify=False, rejectUnauthorized, NODE_TLS_REJECT
SSRF: requests.get(, axios(, fetch(, http.get( (사용자 입력 기반)
LLM system role: system role, system message, role.*system, messages.*role
PII→LLM: inference, completion, chat, prompt
MCP/Tool 실행: tool_call, function_call, execute, delete, remove
Token 제한: max_tokens, maxTokens, token_limit, prompt_length
SSE/스트리밍: text/event-stream, StreamResponse, Flush, event-stream
빌드 시크릿: .npmrc, //registry, authToken, ARG.*SECRET, ARG.*TOKEN
Webhook: webhook, hook, callback, hmac, signature, X-Hub-Signature
파일 다운로드: Content-Disposition, attachment, X-Content-Type-Options, nosniff
SSE 직렬화: fmt.Sprintf.*data:, "data: " +, string concat.*event-stream
```

### 분류 참조: 점검 항목

| 카테고리 | 점검 항목 | 위험도 | 근거 |
|----------|----------|--------|------|
| **api_design** | Rate Limiting 부재 | High/Medium | A04, API4 |
| | 과도한 데이터 노출 | Medium | API3 |
| | SSRF | High | A10, API7 |
| | 미보호 엔드포인트 (관리/디버그) | High/Medium | API5, API9 |
| | 네트워크 의존 보호 (앱 레벨 인증 없음) | High | A01, API5, CWE-653 |
| | CORS 설정 오류 | High | A01, API8 |
| | GraphQL 남용 | High | API4 |
| | Decompression Bomb | High/Medium | API4 |
| | Unsafe API Consumption | High/Medium | API10 |
| | WebSocket 보안 | High | CWE-1385 |
| | Clickjacking / 보안 헤더 | Medium | A05, CWE-1021 |
| | Body Size 제한 | Medium | API4 |
| | Webhook 서명/HMAC 미검증 | High | A02, CWE-345 |
| | 파일 다운로드 보안 (Content-Disposition, MIME sniffing) | Medium | A05, CWE-430 |
| | 빌드/배포 설정 내 시크릿 | High | A05, A09, CWE-798 |
| **llm_ai** | Prompt Injection | High | LLM01 |
| | PII 미스크러빙 → LLM 전달 | High | LLM02 |
| | Excessive Agency (MCP 도구 무확인 실행) | High | LLM06 |
| | Unbounded Consumption | High/Medium | LLM10 |
| | RAG 접근제어 (테넌트 격리) | Medium | LLM08 |
| | 오정보 방지 (출력 검증 없음) | Medium | LLM09 |
| | SSE/스트리밍 안전성 | Medium | LLM05 |
| **pii_handling** | PII 마스킹 없음 (미리보기, 응답) | Medium | LLM02 |
| | 데이터 보존/삭제 정책 부재 | Medium | LLM06 |
| | PII 접근 감사 로그 미기록 | Medium | A09 |

---

## 공통: 심각도 기준

### AI 발견 취약점

| 심각도 | 기준 |
|--------|------|
| Critical | RCE, 인증 우회, 대규모 데이터 유출 |
| High | SQLi, 권한 상승, IDOR, JWT 결함 |
| Medium | SSRF, XSS, 정보 노출, 설정 취약 |
| Low | 설정 문제, 코드 품질 |
| Info | 개선 권장, deprecated |

### 도구 출력 심각도 매핑 (간단 모드에서 도구 결과 처리 시 적용)

> 감사 모드에서는 `merge_findings.py`가 결정적으로 처리하므로 아래 규칙을 직접 적용할 필요 없음.
> **간단 모드에서 도구 raw 출력을 직접 처리할 때** 아래 기준을 따르세요.

#### Semgrep: Impact x Likelihood 매트릭스

> Semgrep 결과의 `extra.metadata.impact`와 `extra.metadata.likelihood` 필드를 사용하여 심각도를 결정합니다.

| Impact | Likelihood HIGH | Likelihood MED | Likelihood LOW |
|--------|----------------|----------------|----------------|
| HIGH | Critical | High | High |
| MEDIUM | High | High | Medium |
| LOW | High | Medium | Low |

**CWE 최소 보장**: CWE-78, 94, 95, 89, 502, 611, 918 → 최소 High (매트릭스 결과가 Medium 이하이면 High로 승격)

> impact/likelihood 필드가 없으면 Semgrep 네이티브 severity(ERROR→high, WARNING→medium, INFO→info)를 사용합니다.

#### Gitleaks

| 조건 | 심각도 |
|------|--------|
| 시크릿 탐지 (모든 RuleID) | Critical (고정) |

> FP 가능성(placeholder, 예시 파일 등)은 severity가 아닌 리포트의 FP 의견에서 표기합니다.

#### Trivy

| 도구 출력 Severity | 매핑 |
|--------------------|------|
| CRITICAL | critical |
| HIGH | high |
| MEDIUM | medium |
| LOW | low |
| UNKNOWN | medium |

---

## 공통: 출력 JSON 스키마

```json
{
  "metadata": { "repository": "...", "scan_date": "...", "mode": "audit", "round": 1 },
  "component_coverage": {
    "backend/go": { "categories": ["auth_logic", "business_logic", "error_handling"], "status": "checked" },
    "admin/": { "categories": ["auth_logic", "business_logic", "error_handling"], "status": "checked" },
    "frontend/": { "categories": ["auth_logic"], "status": "checked" }
  },
  "checklist": [
    { "category": "auth_logic", "item": "인증 미적용", "status": "finding", "finding_id": "AI-001" },
    { "category": "auth_logic", "item": "JWT 결함", "status": "checked_ok" },
    { "category": "auth_logic", "item": "인증 외부 의존성 fallback", "status": "not_applicable", "evidence": "Grep 'introspect|fallback' 0건" }
  ],
  "findings": [
    {
      "id": "AI-001",
      "severity": "critical|high|medium|low|info",
      "category": "business_logic|data_flow|auth_logic|concurrency|error_handling|api_design|crypto_misuse|llm_ai|pii_handling",
      "title": "취약점 제목",
      "file": "파일 경로",
      "line": 83,
      "cwe": "CWE-XXX",
      "owasp": "A0X:2021",
      "code_snippet": "취약 코드 발췌",
      "remediation": "수정 권고"
    }
  ],
  "summary": { "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0 }
}
```

- **`component_coverage`**: 분석한 컴포넌트별로 점검한 카테고리와 상태 기록. 빠뜨린 컴포넌트가 없는지 확인용.
- **`checklist`**: 자기 라운드의 **모든 점검 항목**에 대해 `"finding"` / `"checked_ok"` / `"not_applicable"` 중 하나를 기록. 항목을 건너뛰지 마세요.
  - **`not_applicable` 필수 조건**: `"evidence"` 필드에 해당 항목이 적용 불가인 근거를 기록해야 합니다 (예: `"Grep 'chat|prompt|mcp' 0건"`, `"코드에 해당 패턴 미존재 확인"`). evidence 없는 `not_applicable`은 무효입니다.
- **`category`**: 자기 라운드에 해당하는 카테고리만 사용

> **참고**: `confidence`/`confidence_note`(FP 가능성 판단)는 Phase B에서 부여하지 않습니다. Phase C(FP Triage)에서 모든 finding에 대해 일괄 판정합니다.

JSON 파일을 Write 도구로 저장하세요.
**결과 반환**: summary 객체 + component_coverage 요약을 반환.
