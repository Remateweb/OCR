FROM python:3.11-slim

# System dependencies: ffmpeg + libs for OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY server.py .
COPY ocr_extractor.py .
COPY stream_manager.py .
COPY static/ static/

# Create directories for runtime data
RUN mkdir -p frames output

# Expose port
EXPOSE 8000

# Run with uvicorn
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
