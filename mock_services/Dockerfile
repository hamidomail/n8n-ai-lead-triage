FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn>=0.30"
COPY server.py .
EXPOSE 8787
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8787"]
