# ==============================================================================
# Stage 1: Build React Application
# ==============================================================================
FROM node:20-alpine AS build
ARG HTTP_PROXY HTTPS_PROXY NO_PROXY

WORKDIR /app

# Copy dependency definitions
COPY package*.json ./

# Install dependencies
RUN npm ci

# Copy source code
COPY . .

# Build production bundle
RUN npm run build

# ==============================================================================
# Stage 2: Serve with Production Nginx
# ==============================================================================
FROM nginx:alpine

# Copy custom Nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy build artifacts from Stage 1
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
