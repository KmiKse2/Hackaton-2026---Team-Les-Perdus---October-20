FROM debian:bookworm-slim AS build
ARG FLUTTER_VERSION=3.41.6
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git unzip xz-utils libglu1-mesa \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "${FLUTTER_VERSION}" https://github.com/flutter/flutter.git /opt/flutter
ENV PATH="/opt/flutter/bin:${PATH}"
RUN flutter config --no-analytics && flutter precache --web
WORKDIR /app
COPY pubspec.yaml pubspec.lock ./
RUN flutter pub get
COPY . .
ARG DEMO_API_TOKEN=""
# Empty base URL makes all browser API requests relative to the Nginx origin.
RUN flutter build web --release --no-web-resources-cdn \
    --dart-define=API_BASE_URL= --dart-define=DEMO_API_TOKEN="${DEMO_API_TOKEN}"

FROM nginx:1.28-alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/build/web /usr/share/nginx/html
EXPOSE 80
