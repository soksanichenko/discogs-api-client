FROM python:3.12-slim

WORKDIR /app

COPY sources/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sources/ .

EXPOSE 8777

CMD ["python", "proxy.py"]
