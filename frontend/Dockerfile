# syntax=docker/dockerfile:1.7
# AYVID frontend (Next.js 15 standalone). Multi-stage, non-root, ~150MB final.

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* must be present at BUILD time, not runtime, because Next.js
# inlines them into the static bundle. The nav badge reads
# NEXT_PUBLIC_BUILD_SHA so the deployed frontend's commit is visible at a
# glance — drift between bundle and backend becomes immediately obvious.
ARG NEXT_PUBLIC_BUILD_SHA=dev
ARG NEXT_PUBLIC_API_BASE
ENV NEXT_PUBLIC_BUILD_SHA=${NEXT_PUBLIC_BUILD_SHA} \
    NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE} \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app

# Re-declare so the runtime layer also has the SHA visible (for `docker
# inspect` / Cloud Run env). The bundle was already stamped in the
# builder stage above; this is for ops, not the browser.
ARG BUILD_SHA=dev
ARG BUILD_TIME=unknown
ARG NEXT_PUBLIC_BUILD_SHA=dev
ENV BUILD_SHA=${BUILD_SHA} \
    BUILD_TIME=${BUILD_TIME} \
    NEXT_PUBLIC_BUILD_SHA=${NEXT_PUBLIC_BUILD_SHA}
LABEL build_sha=${BUILD_SHA}
LABEL build_time=${BUILD_TIME}

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs && \
    adduser  --system --uid 1001 --ingroup nodejs nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost:3000 || exit 1

CMD ["node", "server.js"]
