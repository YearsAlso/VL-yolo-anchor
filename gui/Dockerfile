# syntax=docker/dockerfile:1
# VL-YOLO-Anchor GUI image: build the React SPA with Vite, serve it with nginx
# and reverse-proxy /api to the backend service. Build context must be ./gui.

# --- Build stage ---
FROM node:20-slim AS build
WORKDIR /app
# Cache npm deps separately.
COPY package.json package-lock.json* ./
RUN npm ci || npm install
COPY . .
# Empty base keeps SPA calls same-origin "/api"; nginx proxies them to backend.
ARG VITE_API_BASE=""
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build

# --- Runtime stage ---
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
