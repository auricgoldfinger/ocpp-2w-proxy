FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# replace this with your application's default port
EXPOSE 8321

# Set the working directory in the container
WORKDIR /app

# Use the system Python from the image instead of downloading one
ENV UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install dependencies first (cached layer as long as the lock file is unchanged)
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy the application
COPY . /app

# Put the virtual environment on PATH
ENV PATH="/app/.venv/bin:$PATH"

CMD ["python", "ocpp-2w-proxy.py"]
