# Ledgerly web app.
# Next.js resolves rewrites at *build* time, so BACKEND_URL (where /api/* is proxied) is a build argument.
FROM node:24-slim AS build
ARG BACKEND_URL=http://api:8000
ENV BACKEND_URL=$BACKEND_URL
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:24-slim
ARG BACKEND_URL=http://api:8000
ENV NODE_ENV=production PORT=3000 BACKEND_URL=$BACKEND_URL
WORKDIR /app
# full install: `next start` loads next.config.ts, which needs TypeScript
COPY --from=build /app/package.json /app/package-lock.json ./
RUN npm ci && npm cache clean --force
COPY --from=build /app/.next ./.next
COPY --from=build /app/next.config.ts /app/tsconfig.json ./
USER node
EXPOSE 3000
CMD ["npx", "next", "start", "-p", "3000"]
