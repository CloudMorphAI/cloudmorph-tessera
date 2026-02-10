FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir jsonschema requests
COPY contracts ./contracts
COPY databricks/executor ./databricks/executor
WORKDIR /app/databricks/executor
ENV PYTHONUNBUFFERED=1
CMD ["python", "src/main.py"]
