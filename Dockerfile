FROM node:22-slim AS frontend-builder

WORKDIR /frontend

COPY frontend/package.json ./package.json
COPY frontend/tsconfig.json ./tsconfig.json
COPY frontend/tsconfig.node.json ./tsconfig.node.json
COPY frontend/vite.config.ts ./vite.config.ts
COPY frontend/index.html ./index.html
COPY frontend/src ./src

RUN npm install
RUN npm run build

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY frontend/package.json ./frontend/package.json
COPY --from=frontend-builder /frontend/dist ./frontend/dist
COPY README.md ./README.md

CMD ["python", "-m", "ai_me.main"]
