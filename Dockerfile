# ScamBreaker-Z application image for IBM LinuxONE (s390x).
# Built on the LinuxONE Community Cloud ML image, which already ships TensorFlow with the
# IBM zDNN plugin (Telum NNPA), XGBoost, scikit-learn and llama.cpp built with zDNN.
ARG BASE=registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x:latest
FROM ${BASE}

USER root
ARG PY=/opt/venv/bin/python
RUN ${PY} -m pip install --no-cache-dir "flask>=3,<4" "redis>=5,<6" "gunicorn>=22,<24"

WORKDIR /app
COPY scambreaker /app/scambreaker
COPY web /app/web
COPY knowledge /app/knowledge
COPY scenarios /app/scenarios

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TF_CPP_MIN_LOG_LEVEL=2 \
    HOME=/tmp \
    SB_WORK_DIR=/secure/work \
    SB_DATA_DIR=/secure/work/data \
    SB_MODEL_DIR=/secure/work/models \
    SB_AUDIT_DIR=/secure/work/audit \
    SB_SECRETS_DIR=/secure/work/secrets \
    SB_RESULTS_DIR=/secure/work/results

USER 1000:1000
ENTRYPOINT []
CMD ["python", "-m", "scambreaker.serve.web"]
