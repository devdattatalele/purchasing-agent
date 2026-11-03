FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.8.3 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY backend/ backend/
RUN uv sync --frozen --no-dev
COPY --from=web /web/dist web/dist
COPY eval/ eval/
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "buyer.api:app", "--host", "0.0.0.0", "--port", "8000"]
