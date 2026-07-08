# RepoMedic itself, containerized.
#
# Note: when running inside this container, RepoMedic uses its local executor
# (the container is the isolation boundary). Mount the repository to
# investigate at /target.
#
#   docker build -t repomedic .
#   docker run --rm -v /path/to/broken-repo:/target repomedic investigate /target --executor local

FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY repomedic ./repomedic
RUN pip install --no-cache-dir . && pip install --no-cache-dir pytest

ENTRYPOINT ["repomedic"]
CMD ["--help"]
