FROM node:20-slim

WORKDIR /app
COPY package.json ./
RUN npm install

COPY . .

# Drop root — the node image ships an unprivileged `node` user (uid 1000). /app must
# stay writable by it: `npm run build` emits dist/ there in production, and the
# anonymous node_modules volume inherits this ownership when it is created.
RUN chown -R node:node /app
USER node

EXPOSE 5173
CMD ["npm", "run", "dev"]
