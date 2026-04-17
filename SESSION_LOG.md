# ST1 MSA 구축 세션 정리

## 목표
두 개의 EKS 클러스터에 MSA 구조로 서비스 배포
- **클러스터1** (`st1-eks-cluster`): board/guestbook 서비스
- **클러스터2** (`st1-eks-cluster-2`): auth/login 서비스
- **단일 도메인**: `https://st1.sory.cloud`

---

## 아키텍처 구성

```
사용자
  │
  ▼
st1.sory.cloud (Route53 ALIAS)
  │
  ▼
Board ALB (클러스터1)
  ├── /          → nginx (정적 홈페이지)
  ├── /company/  → nginx (정적 회사소개)
  ├── /board/    → FastAPI (게시판)
  ├── /guestbook/→ FastAPI (방명록)
  └── /auth/     → nginx proxy → Auth ALB (클러스터2)
                                    └── FastAPI (로그인/회원가입/관리자)
                                              │
                                              ▼
                                    RDS Proxy → RDS MySQL
```

---

## 1. 기능 구현

### 1-1. 방명록 비밀번호 삭제 기능

방명록 작성 시 비밀번호를 설정하고, 삭제 시 비밀번호 검증. admin은 비밀번호 없이 삭제 가능.

**DB 컬럼 추가**
```sql
ALTER TABLE guestbook ADD COLUMN password VARCHAR(255) NOT NULL DEFAULT '';
```

```bash
kubectl run mysql-client --image=mysql:8.0 --rm -it --restart=Never -- \
  mysql -h <RDS_ENDPOINT> -u admin -p<PASSWORD> st1_db \
  -e "ALTER TABLE guestbook ADD COLUMN password VARCHAR(255) NOT NULL DEFAULT '';"
```

**FastAPI - 방명록 삭제 라우트** (`board/fastapi/main.py`)
```python
@guestbook_router.post("/delete/{entry_id}")
async def guestbook_delete(
    request: Request,
    entry_id: int,
    password: str = Form(default="")
):
    user = get_current_user(request)
    db = get_db()
    try:
        with db.cursor() as cursor:
            cursor.execute("SELECT * FROM guestbook WHERE id=%s", (entry_id,))
            entry = cursor.fetchone()

        if not entry:
            return RedirectResponse(url="/guestbook/", status_code=303)

        is_admin = user and user["username"] == "admin"
        if not is_admin:
            if not entry["password"] or not bcrypt.checkpw(password.encode(), entry["password"].encode()):
                entries = []
                with db.cursor() as cursor:
                    cursor.execute("SELECT * FROM guestbook ORDER BY created_at DESC")
                    entries = cursor.fetchall()
                return templates.TemplateResponse(
                    request=request, name="guestbook.html",
                    context={"entries": entries, "user": user, "auth_url": AUTH_URL,
                             "board_url": BOARD_URL, "error": "비밀번호가 틀렸습니다."}
                )

        with db.cursor() as cursor:
            cursor.execute("DELETE FROM guestbook WHERE id=%s", (entry_id,))
        db.commit()
    finally:
        db.close()

    return RedirectResponse(url="/guestbook/", status_code=303)
```

### 1-2. 게시판 삭제 기능 (작성자 본인 + admin)

삭제 버튼은 게시글 상세 페이지에서만 노출. 작성자 본인 또는 admin만 삭제 가능.

**FastAPI - 게시글 삭제 라우트**
```python
@board_router.post("/delete/{post_id}")
async def board_delete(request: Request, post_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/board/", status_code=303)
    db = get_db()
    try:
        with db.cursor() as cursor:
            if user["username"] == "admin":
                cursor.execute("DELETE FROM board WHERE id=%s", (post_id,))
            else:
                cursor.execute("DELETE FROM board WHERE id=%s AND author=%s",
                               (post_id, user["username"]))
        db.commit()
    finally:
        db.close()
    return RedirectResponse(url="/board/", status_code=303)
```

**board_detail.html - 삭제 버튼**
```html
{% if user and (user.username == "admin" or user.username == post.author) %}
<form method="post" action="/board/delete/{{ post.id }}" style="display:inline"
      onsubmit="return confirm('삭제하시겠습니까?')">
  <button type="submit" class="btn btn-red">삭제</button>
</form>
{% endif %}
```

### 1-3. Admin 네비게이션 수정

admin 로그인 시 board 쿠키도 함께 발급받아 게시판/방명록 접근 가능.

**auth/fastapi/main.py - 로그인 후 board callback으로 리다이렉트**
```python
token = create_token(user["id"], user["username"])

if callback:
    separator = "&" if "?" in callback else "?"
    return RedirectResponse(
        url=f"{callback}{separator}token={token}&next={next}",
        status_code=303
    )

board_url = os.environ.get("BOARD_URL", "")
return RedirectResponse(
    url=f"{board_url}/board/callback?token={token}&next={next}",
    status_code=303
)
```

**board/fastapi/main.py - callback 엔드포인트**
```python
@app.get("/board/callback")
async def auth_callback(token: str, next: str = "/board/"):
    resp = RedirectResponse(url=next, status_code=303)
    resp.set_cookie(key="access_token", value=token, httponly=True, max_age=86400)
    return resp
```

**auth base.html - 게시판/방명록 nav 링크 추가**
```html
{% if user %}
  {% if board_url %}
    <a href="{{ board_url }}/board/">게시판</a>
    <a href="{{ board_url }}/guestbook/">방명록</a>
  {% endif %}
  <span class="nav-user">👤 {{ user.username }}</span>
  {% if user.username == "admin" %}
    <a href="/auth/admin">관리자</a>
  {% endif %}
  <a href="/auth/logout" class="btn btn-gray">로그아웃</a>
{% endif %}
```

---

## 2. 트러블슈팅: 클러스터2에 board가 잘못 배포된 문제

board/deploy.yaml이 클러스터2에 배포되어 있어서 이미지 업데이트가 반영되지 않던 문제.

**클러스터2에서 board 리소스 제거**
```bash
kubectl config use-context std-001@st1-eks-cluster-2.ap-south-1.eksctl.io

kubectl delete ingress st1-board-ingress
kubectl delete deployment nginx-deployment
kubectl delete deployment fastapi-deployment
kubectl delete service nginx-service
kubectl delete service fastapi-service
```

---

## 3. 트러블슈팅: 클러스터1 ALB Controller 없음

클러스터1에 ALB 컨트롤러가 없어서 Ingress에 ADDRESS가 없던 문제.

**ALB Controller 재설치**
```bash
kubectl config use-context std-001@st1-eks-cluster.ap-south-1.eksctl.io

# OIDC 연결
eksctl utils associate-iam-oidc-provider \
  --cluster st1-eks-cluster \
  --region ap-south-1 \
  --approve

# IAM Service Account 생성
eksctl create iamserviceaccount \
  --cluster st1-eks-cluster \
  --namespace kube-system \
  --name aws-load-balancer-controller \
  --attach-policy-arn arn:aws:iam::476293896981:policy/AWSLoadBalancerControllerIAMPolicy \
  --override-existing-serviceaccounts \
  --approve \
  --region ap-south-1

# Helm으로 설치
helm repo add eks https://aws.github.io/eks-charts
helm repo update
helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName=st1-eks-cluster \
  --set serviceAccount.create=false \
  --set serviceAccount.name=aws-load-balancer-controller
```

---

## 4. 민감정보 관리: Kubernetes Secret + .env 파일

### 구조

| 파일 | 용도 |
|------|------|
| `.env` | 실제 값 저장 (gitignore로 커밋 제외) |
| `.env.example` | 키 목록만 (커밋 가능) |
| `deploy.yaml` | `envFrom: secretRef`로 Secret 전체 주입 |

**board/.env**
```
DB_HOST=st1-board-proxy.proxy-ch4gia0i2pgx.ap-south-1.rds.amazonaws.com
DB_USER=admin
DB_PASSWORD=asdf1234
DB_NAME=st1_db
JWT_SECRET=st1-secret-key
AUTH_URL=http://st1.sory.cloud
AUTH_ALB=k8s-default-st1authi-5bd154f990-264161697.ap-south-1.elb.amazonaws.com
BOARD_URL=http://st1.sory.cloud
```

**auth/.env**
```
DB_HOST=st1-board-proxy.proxy-ch4gia0i2pgx.ap-south-1.rds.amazonaws.com
DB_USER=admin
DB_PASSWORD=asdf1234
DB_NAME=st1_auth
JWT_SECRET=st1-secret-key
BOARD_URL=http://st1.sory.cloud
```

**.gitignore**
```
**/.env
!**/.env.example
```

**deploy.yaml - envFrom 방식**
```yaml
envFrom:
- secretRef:
    name: st1-board-secret
```

**Secret 생성/업데이트 명령어**
```bash
# 클러스터1
kubectl config use-context std-001@st1-eks-cluster.ap-south-1.eksctl.io
kubectl delete secret st1-board-secret --ignore-not-found
kubectl create secret generic st1-board-secret --from-env-file=/home/sean/workspace/st1-msa/board/.env

# 클러스터2
kubectl config use-context std-001@st1-eks-cluster-2.ap-south-1.eksctl.io
kubectl delete secret st1-auth-secret --ignore-not-found
kubectl create secret generic st1-auth-secret --from-env-file=/home/sean/workspace/st1-msa/auth/.env
```

---

## 5. RDS Proxy 구성

### 5-1. IAM Role 생성

```bash
# Role 생성
aws iam create-role \
  --role-name st1-rds-proxy-role \
  --assume-role-policy-document '{
    "Version":"2012-10-17",
    "Statement":[{
      "Effect":"Allow",
      "Principal":{"Service":"rds.amazonaws.com"},
      "Action":"sts:AssumeRole"
    }]
  }' \
  --region ap-south-1

# Secrets Manager 접근 권한 부여
aws iam put-role-policy \
  --role-name st1-rds-proxy-role \
  --policy-name st1-rds-proxy-policy \
  --policy-document '{
    "Version":"2012-10-17",
    "Statement":[{
      "Effect":"Allow",
      "Action":["secretsmanager:GetSecretValue","secretsmanager:DescribeSecret"],
      "Resource":"arn:aws:secretsmanager:ap-south-1:476293896981:secret:board-db-secret-bsW6ay"
    }]
  }' \
  --region ap-south-1
```

### 5-2. Secrets Manager에 DB 자격증명 저장

```bash
aws secretsmanager put-secret-value \
  --secret-id arn:aws:secretsmanager:ap-south-1:476293896981:secret:board-db-secret-bsW6ay \
  --secret-string '{"username":"admin","password":"asdf1234"}' \
  --region ap-south-1
```

### 5-3. RDS Proxy 생성

RDS VPC(`vpc-02ddcca7c17dfca9f`)의 서브넷에 생성해야 함. (다른 VPC에 생성하면 DNS 문제 발생)

```bash
aws rds create-db-proxy \
  --db-proxy-name st1-board-proxy \
  --engine-family MYSQL \
  --auth '[{
    "AuthScheme":"SECRETS",
    "SecretArn":"arn:aws:secretsmanager:ap-south-1:476293896981:secret:board-db-secret-bsW6ay",
    "IAMAuth":"DISABLED",
    "ClientPasswordAuthType":"MYSQL_NATIVE_PASSWORD"
  }]' \
  --role-arn arn:aws:iam::476293896981:role/st1-rds-proxy-role \
  --vpc-subnet-ids subnet-00d38e67fb13a7955 subnet-0fe9a09f1de32b25c subnet-0ce8c01327d8ea836 \
  --vpc-security-group-ids sg-04d2278607415d3c0 \
  --no-require-tls \
  --region ap-south-1
```

> ⚠️ **주의**: `ClientPasswordAuthType`을 `MYSQL_NATIVE_PASSWORD`로 명시해야 함.
> RDS MySQL admin 계정이 `mysql_native_password`를 사용하는데 기본값이 `MYSQL_CACHING_SHA2_PASSWORD`라 AUTH_FAILURE 발생.

### 5-4. RDS 인스턴스 타겟 등록

```bash
aws rds register-db-proxy-targets \
  --db-proxy-name st1-board-proxy \
  --db-instance-identifiers st1-board-db \
  --region ap-south-1
```

### 5-5. 상태 확인

```bash
aws rds describe-db-proxy-targets \
  --db-proxy-name st1-board-proxy \
  --query "Targets[*].{Status:TargetHealth.State,Reason:TargetHealth.Reason}" \
  --region ap-south-1
```

---

## 6. 크로스 VPC DNS 문제 해결

### 문제 상황

EKS VPC(`vpc-0ec62e7a79497a2e1`)에서 RDS Proxy 엔드포인트 DNS 해석 불가.

```
pymysql.err.OperationalError: Can't connect to MySQL server on
'st1-board-proxy.proxy-ch4gia0i2pgx.ap-south-1.rds.amazonaws.com'
([Errno -2] Name or service not known)
```

### 원인 분석

RDS Proxy는 자체 VPC 내부에서만 DNS 해석 가능. EKS VPC의 CoreDNS가 해당 도메인을 모름.

### 해결: Route53 Resolver Inbound Endpoint

**1. RDS VPC SG에 DNS 포트 허용**
```bash
# TCP 53
aws ec2 authorize-security-group-ingress \
  --group-id sg-04d2278607415d3c0 \
  --protocol tcp --port 53 --cidr 10.1.0.0/16 --region ap-south-1

aws ec2 authorize-security-group-ingress \
  --group-id sg-04d2278607415d3c0 \
  --protocol tcp --port 53 --cidr 10.0.0.0/16 --region ap-south-1

# UDP 53
aws ec2 authorize-security-group-ingress \
  --group-id sg-04d2278607415d3c0 \
  --protocol udp --port 53 --cidr 10.1.0.0/16 --region ap-south-1

aws ec2 authorize-security-group-ingress \
  --group-id sg-04d2278607415d3c0 \
  --protocol udp --port 53 --cidr 10.0.0.0/16 --region ap-south-1
```

**2. Route53 Resolver Inbound Endpoint 생성 (RDS VPC에)**
```bash
aws route53resolver create-resolver-endpoint \
  --creator-request-id st1-rds-inbound \
  --name st1-rds-inbound \
  --security-group-ids sg-04d2278607415d3c0 \
  --direction INBOUND \
  --ip-addresses SubnetId=subnet-00d38e67fb13a7955 SubnetId=subnet-0ce8c01327d8ea836 \
  --region ap-south-1
```

**3. Inbound Endpoint IP 확인**
```bash
aws route53resolver list-resolver-endpoint-ip-addresses \
  --resolver-endpoint-id <ENDPOINT_ID> \
  --query "IpAddresses[*].Ip" \
  --region ap-south-1
# 결과: 10.2.1.43, 10.2.2.186
```

**4. CoreDNS에 forwarding 설정**
```bash
kubectl edit configmap coredns -n kube-system
```

Corefile에 아래 블록 추가:
```
proxy-ch4gia0i2pgx.ap-south-1.rds.amazonaws.com:53 {
    forward . 10.2.1.43 10.2.2.186
    cache 30
}
```

```bash
kubectl rollout restart deployment/coredns -n kube-system
```

**5. VPC Peering DNS 활성화**
```bash
# RDS ↔ EKS1 peering
aws ec2 modify-vpc-peering-connection-options \
  --vpc-peering-connection-id pcx-004027727cc40d1a1 \
  --requester-peering-connection-options AllowDnsResolutionFromRemoteVpc=true \
  --region ap-south-1

aws ec2 modify-vpc-peering-connection-options \
  --vpc-peering-connection-id pcx-004027727cc40d1a1 \
  --accepter-peering-connection-options AllowDnsResolutionFromRemoteVpc=true \
  --region ap-south-1
```

**6. DNS 해석 확인**
```bash
kubectl run dns-test --image=busybox --rm -it --restart=Never -- \
  sh -c "nslookup st1-board-proxy.proxy-ch4gia0i2pgx.ap-south-1.rds.amazonaws.com"
```

---

## 7. nginx API Gateway 구성 (단일 도메인)

board nginx가 `/auth/` 경로를 auth ALB로 프록시하여 도메인 하나로 전체 서비스 제공.

**nginx.conf**
```nginx
server {
    listen 80;

    location /auth/ {
        proxy_pass http://${AUTH_ALB}/auth/;
        proxy_set_header Host ${AUTH_ALB};
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /board/ {
        proxy_pass http://fastapi-service/board/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /guestbook/ {
        proxy_pass http://fastapi-service/guestbook/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /company/ {
        root /usr/share/nginx/html;
        index index.html;
    }

    location / {
        root /usr/share/nginx/html;
        index index.html;
    }
}
```

**Dockerfile - envsubst 사용**
```dockerfile
FROM nginx:alpine
COPY index.html /usr/share/nginx/html/index.html
COPY company/index.html /usr/share/nginx/html/company/index.html
COPY nginx.conf /etc/nginx/templates/default.conf.template
```

> `nginx:alpine` 공식 이미지는 `/etc/nginx/templates/*.template` 파일을 시작 시 `envsubst`로 자동 처리함.
> `AUTH_ALB` 환경변수는 deploy.yaml에서 주입.

**deploy.yaml - nginx에 AUTH_ALB 주입**
```yaml
- name: AUTH_ALB
  value: "k8s-default-st1authi-5bd154f990-264161697.ap-south-1.elb.amazonaws.com"
```

---

## 8. HTTPS 및 Route53 도메인 연결

### 8-1. Ingress에 HTTPS 설정

**board/deploy.yaml**
```yaml
annotations:
  alb.ingress.kubernetes.io/listen-ports: '[{"HTTP":80},{"HTTPS":443}]'
  alb.ingress.kubernetes.io/certificate-arn: arn:aws:acm:ap-south-1:476293896981:certificate/cb5cc79c-9c9e-4336-94a7-b849736b8bf3
  alb.ingress.kubernetes.io/ssl-redirect: '443'
```

### 8-2. Route53 ALIAS 레코드

```bash
# ALB 호스팅 존 ID 확인
aws elbv2 describe-load-balancers \
  --query "LoadBalancers[?contains(DNSName,'st1board')].{DNS:DNSName,ZoneId:CanonicalHostedZoneId}" \
  --region ap-south-1
```

Route53 콘솔에서:
- 레코드 타입: `A`
- 이름: `st1.sory.cloud`
- 값: ALB ALIAS

---

## 9. 이미지 빌드/배포 표준 절차

### ECR 로그인
```bash
aws ecr get-login-password --region ap-south-1 | \
  docker login --username AWS --password-stdin 476293896981.dkr.ecr.ap-south-1.amazonaws.com
```

### board 빌드/푸시
```bash
cd ~/workspace/st1-msa/board

# nginx
docker build -t st1-board-nginx:<버전> ./nginx
docker tag st1-board-nginx:<버전> 476293896981.dkr.ecr.ap-south-1.amazonaws.com/st1-board-nginx:<버전>
docker push 476293896981.dkr.ecr.ap-south-1.amazonaws.com/st1-board-nginx:<버전>

# fastapi
docker build -t st1-board-fastapi:<버전> ./fastapi
docker tag st1-board-fastapi:<버전> 476293896981.dkr.ecr.ap-south-1.amazonaws.com/st1-board-fastapi:<버전>
docker push 476293896981.dkr.ecr.ap-south-1.amazonaws.com/st1-board-fastapi:<버전>
```

### auth 빌드/푸시
```bash
cd ~/workspace/st1-msa/auth

docker build -t st1-auth-fastapi:<버전> ./fastapi
docker tag st1-auth-fastapi:<버전> 476293896981.dkr.ecr.ap-south-1.amazonaws.com/st1-auth-fastapi:<버전>
docker push 476293896981.dkr.ecr.ap-south-1.amazonaws.com/st1-auth-fastapi:<버전>
```

### 클러스터1 배포
```bash
kubectl config use-context std-001@st1-eks-cluster.ap-south-1.eksctl.io

# Secret 업데이트 (환경변수 변경 시)
kubectl delete secret st1-board-secret --ignore-not-found
kubectl create secret generic st1-board-secret --from-env-file=/home/sean/workspace/st1-msa/board/.env

kubectl apply -f ~/workspace/st1-msa/board/deploy.yaml
kubectl rollout restart deployment/fastapi-deployment
kubectl rollout restart deployment/nginx-deployment
kubectl rollout status deployment/fastapi-deployment
```

### 클러스터2 배포
```bash
kubectl config use-context std-001@st1-eks-cluster-2.ap-south-1.eksctl.io

# Secret 업데이트 (환경변수 변경 시)
kubectl delete secret st1-auth-secret --ignore-not-found
kubectl create secret generic st1-auth-secret --from-env-file=/home/sean/workspace/st1-msa/auth/.env

kubectl apply -f ~/workspace/st1-msa/auth/deploy.yaml
kubectl rollout restart deployment/auth-fastapi-deployment
kubectl rollout status deployment/auth-fastapi-deployment
```

---

## 10. 최종 서비스 구성 정보

| 항목 | 값 |
|------|-----|
| 도메인 | `https://st1.sory.cloud` |
| Board ALB | `k8s-default-st1board-1d74f00d83-2092791144.ap-south-1.elb.amazonaws.com` |
| Auth ALB | `internal-k8s-default-st1authi-2dddb1f60e-1809381502.ap-south-1.elb.amazonaws.com` (internal) |
| RDS Proxy | `st1-board-proxy.proxy-ch4gia0i2pgx.ap-south-1.rds.amazonaws.com` |
| RDS (직접) | `st1-board-db.ch4gia0i2pgx.ap-south-1.rds.amazonaws.com` |
| ACM 인증서 | `*.sory.cloud` |
| JWT Secret | `st1-secret-key` (두 클러스터 공유) |

### 서비스 URL

| 경로 | 설명 |
|------|------|
| `https://st1.sory.cloud/` | 포털 홈 |
| `https://st1.sory.cloud/company/` | 회사 소개 |
| `https://st1.sory.cloud/board/` | 게시판 |
| `https://st1.sory.cloud/guestbook/` | 방명록 |
| `https://st1.sory.cloud/auth/login` | 로그인 |
| `https://st1.sory.cloud/auth/register` | 회원가입 |
| `https://st1.sory.cloud/auth/admin` | 관리자 페이지 (admin 전용) |

### 계정 정보

| 계정 | 비밀번호 | 권한 |
|------|---------|------|
| admin | asdf1234 | 전체 관리, 게시글/방명록 삭제 |
| 일반 사용자 | - | 게시글 작성/본인글 삭제, 방명록 비밀번호 삭제 |

---

## 11. 2026-04-17 추가 작업

### 11-1. 게시글 수정 기능 (board)

작성자 본인 또는 admin이 글 수정 가능. `GET/POST /board/edit/{post_id}` 추가.

- `board/fastapi/main.py` — edit 라우트 추가
- `board/fastapi/templates/board_edit.html` — 수정 폼 템플릿 (신규)
- `board/fastapi/templates/board_detail.html` — 수정 버튼 추가
- 이미지: `st1-board-fastapi:v12`

### 11-2. Kubernetes 안정성 개선

**podAntiAffinity `required` → `preferred` 변경**
싱글 노드 환경에서 `required`는 롤링 업데이트 시 새 파드 스케줄 불가 문제 발생.
두 deploy.yaml 모두 `preferredDuringSchedulingIgnoredDuringExecution`으로 변경.

```yaml
affinity:
  podAntiAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
    - weight: 100
      podAffinityTerm:
        labelSelector:
          matchLabels:
            app: <app>
        topologyKey: kubernetes.io/hostname
```

**PodDisruptionBudget 클러스터 적용**
deploy.yaml에 정의되어 있었으나 미적용 상태였음. `kubectl apply`로 반영.

**board Ingress 정리**
`/board/` → fastapi 직접 룰 제거. 모든 트래픽을 nginx-service 단일 경로로 통일.

### 11-3. auth nginx 헬스체크 수정

ALB 헬스체크 기본 경로 `/` → auth nginx가 301 반환 → unhealthy 판정.

- `auth/nginx/nginx.conf`에 `location /health { return 200 'ok'; }` 추가
- `auth/deploy.yaml` Ingress annotation에 `alb.ingress.kubernetes.io/healthcheck-path: /health` 추가
- 이미지: `st1-auth-nginx:v2`

### 11-4. auth ALB → internal 전환 (VPC 피어링 활용)

board nginx가 `/auth/` 경로를 외부 인터넷으로 프록시하던 것을 VPC 피어링 내부 통신으로 변경.

**작업 순서:**
1. `auth/deploy.yaml` Ingress scheme `internet-facing` → `internal`
2. 기존 Ingress 삭제 후 재적용 (scheme은 생성 시에만 설정 가능)
3. cluster2 VPC 프라이빗 서브넷에 `kubernetes.io/cluster/st1-eks-cluster-2=shared` 태그 추가
   - 대상: subnet-0d4f62d43b42d151f, subnet-0575a825823e408c2, subnet-02848373c310b1303
4. `board/deploy.yaml` AUTH_ALB 값을 새 internal DNS로 업데이트

**새 auth ALB DNS:**
`internal-k8s-default-st1authi-2dddb1f60e-1809381502.ap-south-1.elb.amazonaws.com`

> AUTH_URL(`http://st1.sory.cloud`)은 board nginx 경유이므로 브라우저가 auth ALB에 직접 접근하지 않음.
> internal 전환 후에도 로그인 흐름 동일.

---

## 12. 주요 트러블슈팅 요약

| 문제 | 원인 | 해결 |
|------|------|------|
| 클러스터2에 board 배포됨 | 잘못된 context에서 kubectl apply | 클러스터2에서 board 리소스 삭제 |
| 클러스터1 Ingress ADDRESS 없음 | ALB Controller pod 없음 | Helm으로 ALB Controller 재설치 |
| 방명록 404 | nginx가 prefix strip → fastapi 라우터 미매칭 | `proxy_pass http://fastapi-service/guestbook/;`로 prefix 유지 |
| 로그인 후 board 쿠키 없음 | auth 도메인 쿠키는 board에서 못 읽음 | board `/board/callback` 엔드포인트로 board 도메인 쿠키 발급 |
| RDS Proxy AUTH_FAILURE | `MYSQL_CACHING_SHA2_PASSWORD` vs `mysql_native_password` 불일치 | Proxy 생성 시 `ClientPasswordAuthType: MYSQL_NATIVE_PASSWORD` 명시 |
| Proxy DNS 해석 불가 | Proxy가 다른 VPC → EKS CoreDNS가 모름 | Route53 Resolver Inbound Endpoint → CoreDNS forwarding 설정 |
| CoreDNS forwarding timeout | `10.2.0.2` 직접 접근 불가 | Inbound Endpoint IP(`10.2.1.43`, `10.2.2.186`)로 변경 |
