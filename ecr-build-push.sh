#!/bin/bash
# ECR 리포지토리 생성 + 이미지 빌드/푸시

set -e

ACCOUNT_ID="476293896981"
REGION="ap-south-1"
ECR="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ">>> [1] ECR 리포지토리 생성"
for REPO in st1-board-nginx st1-board-fastapi st1-auth-nginx st1-auth-fastapi; do
  aws ecr create-repository --repository-name $REPO --region $REGION 2>/dev/null \
    && echo "생성: $REPO" \
    || echo "이미 존재: $REPO"
done

echo ""
echo ">>> [2] ECR 로그인"
aws ecr get-login-password --region $REGION \
  | docker login --username AWS --password-stdin $ECR

echo ""
echo ">>> [3] board-nginx 빌드/푸시"
docker build -t $ECR/st1-board-nginx:v1 $SCRIPT_DIR/board/nginx/
docker push $ECR/st1-board-nginx:v1

echo ""
echo ">>> [4] board-fastapi 빌드/푸시"
docker build -t $ECR/st1-board-fastapi:v1 $SCRIPT_DIR/board/fastapi/
docker push $ECR/st1-board-fastapi:v1

echo ""
echo ">>> [5] auth-nginx 빌드/푸시"
docker build -t $ECR/st1-auth-nginx:v1 $SCRIPT_DIR/auth/nginx/
docker push $ECR/st1-auth-nginx:v1

echo ""
echo ">>> [6] auth-fastapi 빌드/푸시"
docker build -t $ECR/st1-auth-fastapi:v1 $SCRIPT_DIR/auth/fastapi/
docker push $ECR/st1-auth-fastapi:v1

echo ""
echo "========================================="
echo "완료!"
echo "board-nginx  : $ECR/st1-board-nginx:v1"
echo "board-fastapi: $ECR/st1-board-fastapi:v1"
echo "auth-nginx   : $ECR/st1-auth-nginx:v1"
echo "auth-fastapi : $ECR/st1-auth-fastapi:v1"
echo "========================================="
