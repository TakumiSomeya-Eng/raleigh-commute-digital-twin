# infra/ — Phase 2 (AWS Deployment)

This directory is intentionally empty in Phase 1.

Phase 2 Terraform modules will be added here after Phase 1 local validation is complete:

- `s3/` — S3 bucket + prefix layout (FR-12.1)
- `ecr/` — ECR repositories for Python and ROS 2 images (FR-12.2)
- `eks/` — EKS cluster for ROS 2 fusion jobs (FR-12.3)
- `stepfn/` — Step Functions state machine orchestration (FR-12.4)
- `iam/` — Least-privilege IAM roles (FR-12.7)
- `observability/` — Cost budgets, CloudWatch dashboard, auto-teardown Lambda (FR-12.6)

See: TRD §9 (Phase 2 placeholder) and FRD §FR-12.
