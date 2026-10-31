FROM python:3.12-slim

WORKDIR /app

COPY sources/requirements.txt .
# pip is left unpinned on purpose: always take the latest security fixes.
# hadolint ignore=DL3013
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY sources/ .

EXPOSE 8777

CMD ["python", "proxy.py"]
