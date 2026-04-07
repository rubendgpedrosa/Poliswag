# Use an official Python runtime as a parent image
FROM python:3.11-slim-bookworm

# Environment
ENV TZ=Europe/Lisbon \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Set timezone
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Install system packages (wkhtmltopdf for imgkit, tzdata for timezone support)
RUN apt-get update && \
    apt-get install -y --no-install-recommends wkhtmltopdf tzdata && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /app

# Cached pip layer: only invalidated when requirements.txt changes
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy the rest of the source (overridden at runtime by the .:/app bind mount)
COPY . /app

# Make port 8989 available to the world outside this container
EXPOSE 8989

# Run the bot
CMD ["python", "main.py"]
