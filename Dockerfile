FROM python:3.12-slim-bookworm
WORKDIR /bot

# Add build arguments for version information
ARG VERSION="0.0.0"
ARG RELEASE_NOTES=""

# Copy application files
COPY . /bot

# Create version file from build arguments with proper JSON escaping
RUN echo "{\"current_version\": \"$VERSION\", \"release_notes\": $RELEASE_NOTES}" > /bot/version.json && \
    # Verify the JSON is valid
    python -c "import json; json.load(open('/bot/version.json'))" || \
    # If invalid, create a safe fallback version
    (echo "{\"current_version\": \"$VERSION\", \"release_notes\": \"Release notes could not be parsed\"}" > /bot/version.json)

RUN python -m pip install -r requirements.txt
ENTRYPOINT ["python", "maintenance_bot.py"]