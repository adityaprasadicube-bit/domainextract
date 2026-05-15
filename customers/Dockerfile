## Base image
#FROM python:3.11-slim
#
## Set work directory
#WORKDIR /app
#
## 🔑 Install system dependencies for pyodbc
#RUN apt-get update && apt-get install -y \
#    unixodbc \
#    unixodbc-dev \
#    gcc \
#    g++ \
#    curl \
#    && rm -rf /var/lib/apt/lists/*
#
## Copy requirements and install Python deps
#COPY requirements.txt .
#RUN pip install --no-cache-dir -r requirements.txt
#
## Copy Django project files
#COPY . .
#
## Expose Django port
#EXPOSE 8000
#
## Environment variables for MongoDB (can override at runtime)
#ENV MONGO_HOST=host.docker.internal
#ENV MONGO_PORT=27017
#
## Run Django development server
#CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

# Base image - Use Bookworm (Debian 12) for better package availability
FROM python:3.11-bookworm

# Set work directory
WORKDIR /app

# 🔑 Install system dependencies for pyodbc + mdbtools for Access file support
RUN apt-get update && apt-get install -y \
    unixodbc \
    unixodbc-dev \
    gcc \
    g++ \
    curl \
    mdbtools \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Django project files
COPY . .

# Create temp directory for file uploads
RUN mkdir -p /tmp/subscriber_uploads

# Expose Django port
EXPOSE 8000

# Environment variables for MongoDB (can override at runtime)
ENV MONGO_HOST=host.docker.internal
ENV MONGO_PORT=27017

# Run Django development server
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]