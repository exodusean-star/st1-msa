#!/bin/bash
# ALB Ingress Controller 설치 스크립트
# 사용법: bash install-alb-controller.sh <클러스터명>
# 예시:   bash install-alb-controller.sh st1-eks-cluster
#         bash install-alb-controller.sh st1-eks-cluster-2

set -e

CLUSTER_NAME=$1
REGION="ap-south-1"
ACCOUNT_ID="476293896981"
POLICY_NAME="AWSLoadBalancerControllerIAMPolicy"

if [ -z "$CLUSTER_NAME" ]; then
  echo "사용법: bash install-alb-controller.sh <클러스터명>"
  exit 1
fi

echo ">>> [1] IAM 정책 생성"
curl -sO https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v2.7.2/docs/install/iam_policy.json

POLICY_ARN=$(aws iam create-policy \
  --policy-name $POLICY_NAME \
  --policy-document file://iam_policy.json \
  --query 'Policy.Arn' --output text 2>/dev/null \
  || aws iam list-policies \
    --query "Policies[?PolicyName=='$POLICY_NAME'].Arn" \
    --output text)
echo "IAM 정책: $POLICY_ARN"
rm -f iam_policy.json

echo ">>> [2] IAM 서비스 어카운트 생성"
eksctl create iamserviceaccount \
  --cluster $CLUSTER_NAME \
  --region $REGION \
  --namespace kube-system \
  --name aws-load-balancer-controller \
  --attach-policy-arn $POLICY_ARN \
  --approve \
  --override-existing-serviceaccounts

echo ">>> [3] Helm 리포지토리 추가"
helm repo add eks https://aws.github.io/eks-charts
helm repo update

echo ">>> [4] ALB Controller 설치"
helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName=$CLUSTER_NAME \
  --set serviceAccount.create=false \
  --set serviceAccount.name=aws-load-balancer-controller \
  --set region=$REGION \
  --set vpcId=$(aws eks describe-cluster \
    --name $CLUSTER_NAME \
    --region $REGION \
    --query 'cluster.resourcesVpcConfig.vpcId' --output text)

echo ">>> [5] 설치 확인"
kubectl get deployment -n kube-system aws-load-balancer-controller

echo ""
echo "========================================="
echo "완료! ALB Controller 설치: $CLUSTER_NAME"
echo "========================================="
