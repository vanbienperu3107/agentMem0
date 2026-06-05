FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir \
    fastapi==0.115.* \
    uvicorn==0.32.* \
    "mem0ai<2.0" \
    qdrant-client==1.12.* \
    openai==1.55.*
COPY app.py .
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002"]
