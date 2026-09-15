# ReproAgent service image (CLI + FastAPI).  The *sandbox* image is docker/sandbox.Dockerfile.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 REPROAGENT_DATA_DIR=/data/runs
WORKDIR /app

COPY pyproject.toml README.md ./
COPY reproagent ./reproagent
RUN pip install -e . && pip install numpy

COPY evals ./evals
VOLUME ["/data"]
EXPOSE 8000
CMD ["reproagent", "serve", "--host", "0.0.0.0", "--port", "8000"]
