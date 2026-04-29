# Kernel code embedding

This repo contains a simple script to generate documents for embedding kernel code using tree-sitter. Also, it has an approximator of the price in Gemini for embedding kernel code, which is based on the number of tokens in the code and the price per token.

## Download the kernel source code

```
wget https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-7.0.2.tar.xz
tar -xf linux-7.0.2.tar.xz
```

## Install deps

```
uv sync
```

## TODO

- [ ] Add https://github.com/sirius94/tree-sitter-gas to support GNU assembly syntax instead handwritten checker.
