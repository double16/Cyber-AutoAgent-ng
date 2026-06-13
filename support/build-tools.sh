#!/usr/bin/env bash

#
# Builds and publishes the tools docker image. This image is large and doesn't
# change much, it makes sense to use a published version.
#
# If you'd rather publish to your own REGISTRY:
#   1. Change the `REGISTRY` and `IMAGE_NAME` variables.
#   2. Set the `APT_PROXY` variable if you have an HTTP proxy for Debian packages.
#   3. If using AWS ECR, set AWS credentials before running. Otherwise, comment out the `aws ecr-public` line.
#

set -xe

PLATFORMS="linux/amd64,linux/arm64"
COMP=zstd COMP_LEVEL=15  # default 3
REGISTRY="public.ecr.aws/bramblethorn"
IMAGE_NAME="cyber-autoagent-ng/tools"

VERSION="$(grep org.opencontainers.image.version docker/Dockerfile.tools | grep -Eo '[0-9][0-9.]+')"
test -n "${VERSION}" || exit 1

docker buildx build -f docker/Dockerfile.tools \
  ${PLATFORMS:+--platform ${PLATFORMS}} \
  --compress \
  --pull \
  ${CACHE_DIR:+--cache-to type=local,dest=${CACHE_DIR} --cache-from type=local,src=${CACHE_DIR}} \
  --output type=image,oci-mediatypes=true,compression=${COMP},compression-level=${COMP_LEVEL},force-compression=true \
  --build-arg "APT_PROXY=${APT_PROXY}" \
  -t "${REGISTRY}/${IMAGE_NAME}:${VERSION}" \
  .

docker tag "${REGISTRY}/${IMAGE_NAME}:${VERSION}" \
           "${REGISTRY}/${IMAGE_NAME}:latest"

# Login to AWS ECR assuming credentials are configured.
aws ecr-public get-login-password --region us-east-1 | docker login --username AWS --password-stdin "${REGISTRY}" || exit 1

docker push --all-tags "${REGISTRY}/${IMAGE_NAME}"
