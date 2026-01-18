FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir jsonschema boto3 google-cloud-storage azure-storage-blob
COPY contracts ./contracts
COPY aws/executor ./aws/executor
WORKDIR /app/aws/executor
ENV PYTHONUNBUFFERED=1
CMD ["python", "src/main.py"]
