# ST1 MSA 인프라 설치 순서

처음부터 다시 구축할 경우 이 순서대로 진행.

---

## 전제 조건

- AWS CLI 설정 완료 (Account ID: `476293896981`, Region: `ap-south-1`)
- `kubectl`, `eksctl`, `helm`, `docker` 설치
- ACM 인증서 `*.sory.cloud` 발급 완료 (`arn:aws:acm:ap-south-1:476293896981:certificate/cb5cc79c-9c9e-4336-94a7-b849736b8bf3`)
- Route53 Hosted Zone `sory.cloud` 존재

---

## 1. VPC 구성

총 3개의 VPC. 콘솔 또는 CLI로 생성.

| VPC | CIDR | 용도 |
|-----|------|------|
| vpc-0ec62e7a79497a2e1 | 10.1.0.0/16 | cluster1 (board) |
| vpc-0bd8544a863831e12 | 10.0.0.0/16 | cluster2 (auth) |
| vpc-02ddcca7c17dfca9f | 10.2.0.0/16 | RDS |

### cluster1 VPC 서브넷 (3 public + 3 private)
퍼블릭 서브넷에 `kubernetes.io/role/elb=1` 태그,
프라이빗 서브넷에 `kubernetes.io/role/internal-elb=1` 태그 추가.

### cluster2 VPC 서브넷 (3 public + 3 private)
퍼블릭 서브넷에 `kubernetes.io/role/elb=1` 태그.
프라이빗 서브넷에 `kubernetes.io/role/internal-elb=1` + `kubernetes.io/cluster/st1-eks-cluster-2=shared` 태그.

> ⚠️ internal ALB용 서브넷에 반드시 `kubernetes.io/cluster/<클러스터명>=shared` 태그가 있어야 ALB Controller가 인식함.

---

## 2. VPC 피어링

| 피어링 ID | 연결 | 용도 |
|---------|------|------|
| pcx-09e74f386c0ea9d95 | cluster1 ↔ cluster2 | auth 내부 통신 |
| pcx-004027727cc40d1a1 | cluster1 ↔ RDS | RDS Proxy DNS |
| pcx-0f0ed7d6d91920360 | cluster2 ↔ RDS | RDS Proxy DNS |

피어링 생성 후 **양쪽** 라우팅 테이블에 상대 CIDR 경로 추가.
DNS Resolution 활성화:

```bash
# cluster1 ↔ RDS 피어링
aws ec2 modify-vpc-peering-connection-options \
  --vpc-peering-connection-id pcx-004027727cc40d1a1 \
  --requester-peering-connection-options AllowDnsResolutionFromRemoteVpc=true \
  --region ap-south-1

aws ec2 modify-vpc-peering-connection-options \
  --vpc-peering-connection-id pcx-004027727cc40d1a1 \
  --accepter-peering-connection-options AllowDnsResolutionFromRemoteVpc=true \
  --region ap-south-1
```

---

## 3. RDS 구성

### 3-1. RDS 인스턴스 생성
- ID: `st1-board-db`
- Engine: MySQL 8.0
- Class: db.t3.micro
- VPC: `vpc-02ddcca7c17dfca9f` (RDS VPC)
- SG: `st1-rds-sg` (sg-04d2278607415d3c0)

### 3-2. DB 및 테이블 생성

```sql
CREATE DATABASE st1_db;
CREATE DATABASE st1_auth;

USE st1_db;
CREATE TABLE board (
  id INT AUTO_INCREMENT PRIMARY KEY,
  title VARCHAR(200) NOT NULL,
  content TEXT NOT NULL,
  author VARCHAR(100) NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE guestbook (
  id INT AUTO_INCREMENT PRIMARY KEY,
  author VARCHAR(100) NOT NULL,
  message TEXT NOT NULL,
  password VARCHAR(255) NOT NULL DEFAULT '',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

USE st1_auth;
CREATE TABLE users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(100) UNIQUE NOT NULL,
  password VARCHAR(255) NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### 3-3. RDS Proxy 생성

Secrets Manager에 DB 자격증명 저장:
```bash
aws secretsmanager put-secret-value \
  --secret-id arn:aws:secretsmanager:ap-south-1:476293896981:secret:board-db-secret-bsW6ay \
  --secret-string '{"username":"admin","password":"asdf1234"}' \
  --region ap-south-1
```

IAM Role 생성 (`st1-rds-proxy-role`) 후 Proxy 생성:
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

aws rds register-db-proxy-targets \
  --db-proxy-name st1-board-proxy \
  --db-instance-identifiers st1-board-db \
  --region ap-south-1
```

> ⚠️ `ClientPasswordAuthType: MYSQL_NATIVE_PASSWORD` 반드시 명시. 기본값(`MYSQL_CACHING_SHA2_PASSWORD`)이면 AUTH_FAILURE 발생.

---

## 4. Route53 Resolver — Cross-VPC DNS

cluster1(board) EKS에서 RDS Proxy DNS 해석을 위한 설정.

### 4-1. RDS VPC SG에 DNS 포트 허용 (sg-04d2278607415d3c0)

```bash
for CIDR in 10.1.0.0/16 10.0.0.0/16; do
  for PROTO in tcp udp; do
    aws ec2 authorize-security-group-ingress \
      --group-id sg-04d2278607415d3c0 \
      --protocol $PROTO --port 53 --cidr $CIDR --region ap-south-1
  done
done
```

### 4-2. Route53 Resolver Inbound Endpoint 생성 (RDS VPC)

```bash
aws route53resolver create-resolver-endpoint \
  --creator-request-id st1-rds-inbound \
  --name st1-rds-inbound \
  --security-group-ids sg-04d2278607415d3c0 \
  --direction INBOUND \
  --ip-addresses SubnetId=subnet-00d38e67fb13a7955 SubnetId=subnet-0ce8c01327d8ea836 \
  --region ap-south-1
```

Inbound Endpoint IP 확인 후 기록 (현재: `10.2.1.43`, `10.2.2.186`).

### 4-3. cluster1 CoreDNS에 forwarding 추가

```bash
kubectl --context="std-001@st1-eks-cluster.ap-south-1.eksctl.io" \
  edit configmap coredns -n kube-system
```

Corefile 하단에 추가:
```
proxy-ch4gia0i2pgx.ap-south-1.rds.amazonaws.com:53 {
    forward . 10.2.1.43 10.2.2.186
    cache 30
}
```

```bash
kubectl --context="std-001@st1-eks-cluster.ap-south-1.eksctl.io" \
  rollout restart deployment/coredns -n kube-system
```

---

## 5. EKS 클러스터 생성

```bash
# cluster1 (board) — Kubernetes 1.34
eksctl create cluster \
  --name st1-eks-cluster \
  --region ap-south-1 \
  --version 1.34 \
  --vpc-private-subnets <private-subnet-ids> \
  --vpc-public-subnets <public-subnet-ids> \
  --nodegroup-name standard-workers \
  --node-type t3.medium \
  --nodes 1 \
  --nodes-min 1 \
  --nodes-max 3

# cluster2 (auth)
eksctl create cluster \
  --name st1-eks-cluster-2 \
  --region ap-south-1 \
  --version 1.34 \
  --vpc-private-subnets <private-subnet-ids> \
  --vpc-public-subnets <public-subnet-ids> \
  --nodegroup-name standard-workers \
  --node-type t3.medium \
  --nodes 1 \
  --nodes-min 1 \
  --nodes-max 3
```

---

## 6. ALB Controller 설치 (각 클러스터 반복)

```bash
# OIDC 연결
eksctl utils associate-iam-oidc-provider \
  --cluster <클러스터명> --region ap-south-1 --approve

# IAM Policy 생성 (최초 1회)
curl -o alb-policy.json https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/main/docs/install/iam_policy.json
aws iam create-policy \
  --policy-name AWSLoadBalancerControllerIAMPolicy \
  --policy-document file://alb-policy.json

# IAM Service Account 생성
eksctl create iamserviceaccount \
  --cluster <클러스터명> \
  --namespace kube-system \
  --name aws-load-balancer-controller \
  --attach-policy-arn arn:aws:iam::476293896981:policy/AWSLoadBalancerControllerIAMPolicy \
  --override-existing-serviceaccounts \
  --approve \
  --region ap-south-1

# Helm 설치
helm repo add eks https://aws.github.io/eks-charts && helm repo update
helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName=<클러스터명> \
  --set serviceAccount.create=false \
  --set serviceAccount.name=aws-load-balancer-controller
```

---

## 7. ECR 리포지토리 생성

```bash
for REPO in st1-board-nginx st1-board-fastapi st1-auth-nginx st1-auth-fastapi; do
  aws ecr create-repository --repository-name $REPO --region ap-south-1
done
```

---

## 8. 이미지 빌드 및 푸시

```bash
aws ecr get-login-password --region ap-south-1 | \
  docker login --username AWS --password-stdin 476293896981.dkr.ecr.ap-south-1.amazonaws.com

ECR=476293896981.dkr.ecr.ap-south-1.amazonaws.com

# board nginx
docker build -t $ECR/st1-board-nginx:v4 ~/workspace/st1-msa/board/nginx
docker push $ECR/st1-board-nginx:v4

# board fastapi
docker build -t $ECR/st1-board-fastapi:v12 ~/workspace/st1-msa/board/fastapi
docker push $ECR/st1-board-fastapi:v12

# auth nginx
docker build -t $ECR/st1-auth-nginx:v2 ~/workspace/st1-msa/auth/nginx
docker push $ECR/st1-auth-nginx:v2

# auth fastapi
docker build -t $ECR/st1-auth-fastapi:v9 ~/workspace/st1-msa/auth/fastapi
docker push $ECR/st1-auth-fastapi:v9
```

---

## 9. Kubernetes Secret 생성

```bash
# cluster1
kubectl --context="std-001@st1-eks-cluster.ap-south-1.eksctl.io" \
  create secret generic st1-board-secret \
  --from-env-file=~/workspace/st1-msa/board/.env

# cluster2
kubectl --context="std-001@st1-eks-cluster-2.ap-south-1.eksctl.io" \
  create secret generic st1-auth-secret \
  --from-env-file=~/workspace/st1-msa/auth/.env
```

---

## 10. 애플리케이션 배포

```bash
# cluster1
kubectl --context="std-001@st1-eks-cluster.ap-south-1.eksctl.io" \
  apply -f ~/workspace/st1-msa/k8s/board/deploy.yaml

# cluster2
kubectl --context="std-001@st1-eks-cluster-2.ap-south-1.eksctl.io" \
  apply -f ~/workspace/st1-msa/k8s/auth/deploy.yaml
```

배포 후 auth ALB ADDRESS 확인:
```bash
kubectl --context="std-001@st1-eks-cluster-2.ap-south-1.eksctl.io" \
  get ingress st1-auth-ingress
```

board deploy.yaml의 `AUTH_ALB` 값을 새 internal ALB DNS로 업데이트 후 재적용.

---

## 11. Route53 레코드 등록

board ALB의 Hosted Zone ID 확인:
```bash
aws elbv2 describe-load-balancers --region ap-south-1 \
  --query "LoadBalancers[?contains(DNSName,'st1board')].{DNS:DNSName,ZoneId:CanonicalHostedZoneId}"
```

Route53에서 `st1.sory.cloud` → board ALB ALIAS (A 레코드) 생성.

---

## 12. 최종 확인

```bash
# 파드 상태
kubectl --context="std-001@st1-eks-cluster.ap-south-1.eksctl.io" get pods
kubectl --context="std-001@st1-eks-cluster-2.ap-south-1.eksctl.io" get pods

# 인그레스
kubectl --context="std-001@st1-eks-cluster.ap-south-1.eksctl.io" get ingress
kubectl --context="std-001@st1-eks-cluster-2.ap-south-1.eksctl.io" get ingress

# 헬스체크
curl https://st1.sory.cloud/health
```

---

## 현재 리소스 정보

| 항목 | 값 |
|------|-----|
| Account ID | 476293896981 |
| Region | ap-south-1 |
| 도메인 | https://st1.sory.cloud |
| board ALB | k8s-default-st1board-1d74f00d83-2092791144.ap-south-1.elb.amazonaws.com |
| auth ALB | internal-k8s-default-st1authi-2dddb1f60e-1809381502.ap-south-1.elb.amazonaws.com |
| RDS Proxy | st1-board-proxy.proxy-ch4gia0i2pgx.ap-south-1.rds.amazonaws.com |
| RDS | st1-board-db.ch4gia0i2pgx.ap-south-1.rds.amazonaws.com |
| ACM | arn:aws:acm:ap-south-1:476293896981:certificate/cb5cc79c-9c9e-4336-94a7-b849736b8bf3 |
| Resolver Endpoint | rslvr-in-10884c55598f4347b (10.2.1.43, 10.2.2.186) |
