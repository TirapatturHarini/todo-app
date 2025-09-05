# Use a lightweight Python image
# ./frontend/Dockerfile
FROM node:18-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

# EXPOSE 3000
# CMD ["npm", "start"]

# Copy static files into container
COPY index.html style.css script.js ./

# Expose the port (default 3000, can be overridden by ENV PORT)
EXPOSE 3000

CMD ["npm", "start"]