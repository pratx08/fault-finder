FROM python:3.12-slim
WORKDIR /build
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir uv==0.11.29 && uv sync --frozen --no-dev --no-editable
ENV PATH="/build/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 HOME=/tmp
USER 65534:65534
WORKDIR /project
ENTRYPOINT ["python", "-m", "faultfinder.worker"]
