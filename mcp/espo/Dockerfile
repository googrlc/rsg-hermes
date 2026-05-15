FROM node:22-slim

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install --omit=dev

COPY src ./src
COPY scripts ./scripts

ENV NODE_ENV=production
ENV ESPO_MCP_PORT=3000

CMD ["npm", "start"]
