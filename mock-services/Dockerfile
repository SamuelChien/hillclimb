FROM python:3.12-slim
WORKDIR /app

RUN pip install --no-cache-dir fastapi[standard] uvicorn

COPY app.py ./

ENV PORT=8081
EXPOSE 8081
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8081"]
