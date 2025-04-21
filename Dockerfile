FROM python:3.12-slim-bookworm
WORKDIR /bot

# Add build arguments for version information
ARG VERSION="0.0.0"
ARG RELEASE_NOTES=""

# Copy application files
COPY . /bot

# Create version file from build arguments
RUN echo "{\"current_version\": \"$VERSION\", \"release_notes\": $RELEASE_NOTES}" > /bot/version.json

RUN python -m pip install -r requirements.txt
ENTRYPOINT ["python", "maintenance_bot.py"]