# Use Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY main.py .
# The daily access code's derivation - the server's and the operator's helper
# script must use the same one, so it ships as its own module.
COPY access_code.py .
COPY config ./config
COPY static ./static
COPY templates ./templates
COPY stockpicker ./stockpicker
COPY services ./services


# Expose port
EXPOSE 8080

# Set environment variables
ENV PORT=8080
ENV PYTHONUNBUFFERED=1

# Run the application
CMD ["python", "main.py"]
