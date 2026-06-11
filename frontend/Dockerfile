# legger frontend image (Task H1): Next.js standalone output.
#
# next.config.ts sets `output: "standalone"`, so `next build` emits a
# self-contained server at .next/standalone (node_modules pruned to the
# traced runtime set). public/ and .next/static are copied next to it per
# the Next docs (the standalone server then serves them itself — no CDN in
# this deployment).

FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# BACKEND_URL is baked at BUILD time: next.config.ts (the /api/backend/*
# rewrite) is serialized into the standalone server. The default matches
# the compose service name; override with --build-arg for other topologies.
# In production the rewrite is mostly a fallback — Caddy routes
# /api/backend/* to the api service before the frontend ever sees it.
ARG BACKEND_URL=http://api:8000
ENV BACKEND_URL=$BACKEND_URL \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS runner
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
WORKDIR /app

RUN addgroup -S -g 1001 nodejs && adduser -S -u 1001 -G nodejs nextjs

COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

# busybox wget ships with alpine; the root page is a cheap SSR render.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:3000/ || exit 1

CMD ["node", "server.js"]
