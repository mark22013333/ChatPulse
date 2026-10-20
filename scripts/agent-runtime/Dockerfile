FROM node:22.22.0-bookworm-slim@sha256:dd9d21971ec4395903fa6143c2b9267d048ae01ca6d3ea96f16cb30df6187d94
WORKDIR /opt/chatpulse-probe
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund && npm cache clean --force
COPY fixture.mjs ./
ENV PATH="/opt/chatpulse-probe/node_modules/.bin:/usr/local/bin:/usr/bin:/bin"
USER 1000:1000
ENTRYPOINT ["node", "/opt/chatpulse-probe/fixture.mjs"]
