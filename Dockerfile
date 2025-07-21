FROM python:3.10

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8080  # 필수! Railway는 이걸로 포트를 감지함
CMD ["python", "app.py"]
