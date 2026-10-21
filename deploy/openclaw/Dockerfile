FROM node:24-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates python3 make g++ && rm -rf /var/lib/apt/lists/*
RUN npm install -g --allow-scripts=@google/genai,esbuild,koffi,protobufjs,openclaw openclaw@2026.9.7
RUN npm install -g --allow-scripts=@google/genai,esbuild,koffi,protobufjs,openclaw @openclaw/whatsapp@2026.9.7
ENV OPENCLAW_STATE_DIR=/home/node/.openclaw OPENCLAW_CONFIG_PATH=/home/node/.openclaw/openclaw.json
COPY --chown=node:node aros-tools /home/node/aros-tools
COPY --chown=node:node openclaw.json /home/node/openclaw-template.json
COPY --chown=node:node planner/AGENTS.md /home/node/planner-instructions.md
USER node
WORKDIR /home/node
EXPOSE 18789
ENTRYPOINT ["openclaw"]
CMD ["gateway", "run"]
