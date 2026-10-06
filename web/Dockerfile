# Web para el servidor (infra/oracle): Next pasa /api/* a la API del contenedor "api".
FROM node:22-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ENV API_URL=http://api:8000 NEXT_TELEMETRY_DISABLED=1
RUN npm run build
EXPOSE 3000
CMD ["npm", "start"]
