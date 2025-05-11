FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements file
COPY requirements.txt .

# Install dependencies
RUN --mount=type=cache,target=/var/cache/apt --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt


# Copy the application code
COPY channel_push.py .
COPY static/ static/

# Expose the port for the web server
EXPOSE 5000

# Set environment variables
ENV ICE_HOST=localhost
ENV ICE_PORT=6502
ENV WEB_HOST=0.0.0.0
ENV WEB_PORT=5000

# Run the application
CMD ["python", "channel_push.py"]
