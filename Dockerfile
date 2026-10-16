FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# LibreOffice (Office -> PDF), poppler (page text + preview images), qpdf (repairs damaged PDFs).
# Fonts matter for faithful conversion: Liberation/Carlito/Caladea are metric-compatible with
# Arial/Times/Calibri/Cambria; Noto CJK covers Chinese/Japanese/Korean invoices.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer libreoffice-calc \
        poppler-utils qpdf \
        fonts-liberation fonts-dejavu-core fonts-carlito fonts-caladea fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY settings.py classifier.py core.py app.py ./
COPY .streamlit ./.streamlit

# Run as an unprivileged user; all temporary files go to /data (auto-deleted by the app)
RUN useradd -m -u 10001 appuser && mkdir -p /data && chown appuser /data
USER appuser
ENV FDP_WORK_DIR=/data HOME=/home/appuser

EXPOSE 8501
HEALTHCHECK CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health').status==200 else 1)"
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
