# Workflow prerequisite — AWS IAM setup for ECR mirror

The `push-ecr` job in `.github/workflows/release.yml` mirrors the released
image from GHCR to the private ECR repository
`237509402889.dkr.ecr.us-east-1.amazonaws.com/cloudmorph/tessera-cloud-prod`
using GitHub OIDC to assume an AWS IAM role — no long-lived AWS credentials
in GitHub secrets.

The OIDC provider already exists in account `237509402889` (verified
2026-05-14):

```
arn:aws:iam::237509402889:oidc-provider/token.actions.githubusercontent.com
```

The IAM role `cloudmorph-github-ecr-push` does NOT yet exist. **It must be
created before the next tagged release** or the `push-ecr` workflow job
will fail. The release will still publish to GHCR and PyPI; only the
ECR mirror step requires this role. Until it exists, the founder must
continue to manually `docker pull from GHCR + tag + push to ECR` after
each release (the v0.2.0 ship-out playbook).

## Steps to create the role

### 1. Trust policy (who can assume the role)

Save as `trust-policy.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::237509402889:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:CloudMorphAI/cloudmorph-tessera:ref:refs/tags/v*"
        }
      }
    }
  ]
}
```

The `StringLike` condition on `sub` restricts the role to assume calls from
**tag-push workflow runs** of the `cloudmorph-tessera` repo only. PR builds,
push-to-branch builds, or workflow_dispatch from a fork cannot assume this
role.

### 2. Permissions policy (what the role can do)

Save as `ecr-push-policy.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "ecr:GetAuthorizationToken",
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer",
        "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload",
        "ecr:PutImage"
      ],
      "Resource": "arn:aws:ecr:us-east-1:237509402889:repository/cloudmorph/tessera-cloud-prod"
    }
  ]
}
```

`GetAuthorizationToken` must be `Resource: "*"` per AWS docs — that action
returns a tokenization endpoint and isn't scoped to a specific repository.
The other actions are scoped to the single Tessera repo only — the role
cannot push to any other ECR repo in the account.

### 3. Create the role and attach the policies

```bash
aws iam create-role \
  --role-name cloudmorph-github-ecr-push \
  --assume-role-policy-document file://trust-policy.json \
  --description "GH Actions OIDC role for cloudmorph-tessera release.yml to mirror images to ECR"

aws iam put-role-policy \
  --role-name cloudmorph-github-ecr-push \
  --policy-name ecr-push-tessera-cloud-prod \
  --policy-document file://ecr-push-policy.json
```

### 4. Verify

```bash
aws iam get-role --role-name cloudmorph-github-ecr-push
aws iam get-role-policy --role-name cloudmorph-github-ecr-push \
  --policy-name ecr-push-tessera-cloud-prod
```

After this, the next `git tag v0.2.x && git push origin v0.2.x` will fire
release.yml, and the `push-ecr` job will succeed automatically.

## What if a release happens before this is set up?

The `push-ecr` job will fail with something like:
`AccessDenied: Could not assume role cloudmorph-github-ecr-push`.

The other jobs (`sbom`, `sign`, `attest-sbom`, `pypi-publish`) are not
dependent on `push-ecr`, so they will succeed. The image will be in GHCR
and PyPI but not yet in private ECR. Manual mirror:

```bash
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin \
    237509402889.dkr.ecr.us-east-1.amazonaws.com
docker pull ghcr.io/cloudmorphai/tessera:${VERSION}
docker tag  ghcr.io/cloudmorphai/tessera:${VERSION} \
  237509402889.dkr.ecr.us-east-1.amazonaws.com/cloudmorph/tessera-cloud-prod:${VERSION}
docker push 237509402889.dkr.ecr.us-east-1.amazonaws.com/cloudmorph/tessera-cloud-prod:${VERSION}
```

This is exactly what the v0.2.0 ship-out playbook did. After the IAM role
is created, this manual step goes away.

## Security notes

- The role has zero standing permissions — `sts:AssumeRoleWithWebIdentity`
  only works from GitHub Actions OIDC tokens matching the trust condition.
- The `sub` condition restricts to tag-push workflows of one specific
  repository. A compromised workflow in any other CloudMorphAI repo cannot
  assume this role.
- The ECR push policy is scoped to a single repository. Even if the role
  were misused, it cannot affect other ECR resources.
- Recommend periodic rotation: re-create the role every 12 months. The
  trust policy hash will change but the workflow doesn't need any update
  as long as the role name and ARN stay the same.
