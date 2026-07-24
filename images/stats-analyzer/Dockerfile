# Build context is the repository root (Tekton derive-targets sets context ".").
FROM python:3.13-alpine
COPY src/analyzer.py /app/analyzer.py
USER 65532:65532
EXPOSE 8080
ENV PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python", "/app/analyzer.py"]
