#!/usr/bin/env bash
# Accelerator and LLM benchmarks on the LinuxONE VM (run from ~/scambreaker-z after `docker compose build`).
set -uo pipefail
IMG=scambreaker-z:latest
KDIR=/opt/venv/lib/python3.12/site-packages/tensorflow/core/kernels
R=/secure/work/results
COMMON=(-u 1000:1000 -v /secure/work:/secure/work -e HOME=/tmp -e TF_CPP_MIN_LOG_LEVEL=2)

echo "== LSTM A/B: with the ibm-zdnn-plugin kernels vs plain TensorFlow CPU kernels =="
rm -f $R/nnpa_bench.json
sudo docker run --rm "${COMMON[@]}" $IMG python -m scambreaker.bench.nnpa_bench --mode default --label nnpa --out $R/nnpa_bench.json
# hide the plugin's kernel directory -> TensorFlow loads no zDNN kernels
sudo docker run --rm "${COMMON[@]}" --tmpfs $KDIR $IMG python -m scambreaker.bench.nnpa_bench --mode default --label cpu --out $R/nnpa_bench.json

echo "== IBM Granite 3.3 2B: tokens/s with and without the zDNN backend =="
for q in Q8_0 Q4_K_M; do
  for layers in -1 0; do
    sudo docker run --rm "${COMMON[@]}" $IMG python -m scambreaker.bench.llm_bench \
      --model /secure/work/llm/granite-3.3-2b-instruct-be.$q.gguf --layers $layers --threads 3 --reps 2 \
      --out $R/llm_bench_$q.json 2>&1 | tail -2
  done
done
