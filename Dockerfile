FROM python:3.12-slim AS verify
WORKDIR /build
COPY app.py lab.py test_lab.py ./
RUN python -m unittest -v

FROM python:3.12-slim
WORKDIR /app
COPY --from=verify --chown=10001:10001 /build/app.py /app/app.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8080
CMD ["python", "/app/app.py"]
