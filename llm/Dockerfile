# llama.cpp server for IBM Z (s390x), built with the VXE vector extensions enabled.
# Serves IBM Granite (big-endian GGUF) on the internal network only.
FROM ubuntu:24.04 AS build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ARG LLAMA_TAG=b6754
RUN git clone --depth 1 --branch ${LLAMA_TAG} https://github.com/ggml-org/llama.cpp /src
# llama.cpp targets z17 as -march=arch15, which needs GCC 15; Ubuntu 24.04 ships GCC 13, so
# build for z16 (still VX/VXE/VXE2 vector code, runs on z17).
RUN grep -rl -- "-march=arch15" /src/ggml | xargs -r sed -i 's/-march=arch15/-march=z16/g'
RUN cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Release -DGGML_VXE=ON -DGGML_NATIVE=ON -DLLAMA_CURL=OFF \
      -DBUILD_SHARED_LIBS=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=ON \
    && cmake --build /build -j"$(nproc)" --target llama-server llama-bench

FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=build /build/bin/llama-server /build/bin/llama-bench /usr/local/bin/
USER 1000:1000
ENTRYPOINT ["/usr/local/bin/llama-server"]
