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

ARG SLICE_NAME="MumbleServer_v1_5_735.ice"
COPY slices/${SLICE_NAME} .

# build slice
RUN slice2py ${SLICE_NAME}
    
# Expose the port for the web server
EXPOSE 5000

# Set environment variables
ENV MUMBLE_ICE_HOST=mumble-server
ENV MUMBLE_ICE_PORT=6502
ENV MUMBLE_ICE_SECRET=""
ENV CVP_ICE_HOST=channel-push
ENV CVP_HTTP_HOST=0.0.0.0
ENV CVP_HTTP_PORT=5001

# Run the application
CMD ["python", "channel_push.py"]
