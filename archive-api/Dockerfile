FROM python:3.12-slim
WORKDIR /app
# git is needed for the wiki-agent pip dependency (git+https:// source)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
