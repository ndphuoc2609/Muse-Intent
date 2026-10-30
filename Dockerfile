FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY migrations migrations
ENV DATABASE_URL=postgresql://persona:persona@db:5432/persona_dialogue
EXPOSE 8000
CMD ["sh", "-c", "python -c \"from app.db import make_engine, init_db; init_db(make_engine())\" && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
