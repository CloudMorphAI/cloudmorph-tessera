FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir jsonschema snowflake-connector-python
COPY contracts ./contracts
COPY snowflake/executor ./snowflake/executor
WORKDIR /app/snowflake/executor
ENV PYTHONUNBUFFERED=1
CMD ["python", "src/main.py"]
