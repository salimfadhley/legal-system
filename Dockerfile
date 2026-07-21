# live-index service (ADR 0005 / M7). Built on Halob:
#   docker build -t goldberg-live-index .
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

# Install the package + its dependencies from pyproject.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health',timeout=4).status==200 else 1)"

CMD ["python", "-m", "goldberg_system.service.main"]
