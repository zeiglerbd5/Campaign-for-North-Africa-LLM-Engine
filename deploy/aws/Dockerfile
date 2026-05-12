FROM python:3.13-slim

# Install CPU-only torch first so sentence-transformers picks it up
# instead of pulling the default ~5 GB CUDA-enabled wheel.
RUN pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu \
    torch

RUN pip install --no-cache-dir \
    openpyxl \
    requests \
    boto3 \
    anthropic \
    chromadb \
    sentence-transformers

# Pre-download the embedding model so first container start is fast
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

WORKDIR /app

COPY cna_engine/ ./cna_engine/
COPY run_game.py ./
COPY CNA_DATA_TABLES.xlsx ./
COPY files/CNA_UNIFIED_RULES.md ./files/
COPY files/CNA_AIR_RULES.md ./files/
COPY files/strategic_playbook.jsonl ./files/
COPY files/hex_database.json ./files/
COPY files/named_locations.json ./files/
COPY files/road_network.json ./files/

COPY deploy/aws/entrypoint.sh /app/entrypoint.sh
COPY deploy/aws/s3_sync.py /app/s3_sync.py
RUN chmod +x /app/entrypoint.sh

VOLUME ["/data"]

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["--backend", "bedrock", "--turns", "1"]
