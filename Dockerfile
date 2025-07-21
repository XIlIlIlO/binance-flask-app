FROM python:3.10

WORKDIR /app

COPY requirements.txt .

RUN pip install -r requirements.txt

COPY . .

# Railway가 포트를 감지하기 위해 필요
EXPOSE 8080

CMD ["python", "app.py"]
