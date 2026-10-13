FROM python:3.14-slim

# Install system deps and Poetry
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash \
    && rm -rf /var/lib/apt/lists/* \
    && pip install poetry

# Configure Poetry: do not create virtual env (we're in a container)
ENV POETRY_VENV_IN_PROJECT=false \
    POETRY_NO_INTERACTION=1

COPY . /app

WORKDIR /app

# Install dependencies using Poetry
RUN poetry install --only main

# Copy entrypoint script and make executable
COPY docker/entrypoint.sh /app/docker/entrypoint.sh
RUN chmod +x /app/docker/entrypoint.sh

ENTRYPOINT [ "/app/docker/entrypoint.sh" ]
CMD ["--help"]
