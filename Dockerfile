FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_COLOR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /workspace

RUN python -m venv "$VIRTUAL_ENV" \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

FROM base AS deps

COPY requirements.txt ./
RUN pip install --retries 10 --timeout 120 -r requirements.txt

FROM deps AS runtime

COPY app ./app
COPY scripts ./scripts
COPY skills ./skills
COPY examples ./examples
COPY docs ./docs
COPY pyproject.toml README.md ./

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
