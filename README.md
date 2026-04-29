# Kernel code embedding

This repo contains a simple script to generate documents for embedding kernel code using tree-sitter. Also, it has an approximator of the price in Gemini for embedding kernel code, which is based on the number of tokens in the code and the price per token.

We use the tokenizer from the `gemma-4` model, beause there is no public tokenizer for the `gemini-1.5-pro` model, and the `gemma-4` model is the closest one to the `gemini-1.5-pro` model in terms of architecture and tokenization.

## Download the kernel source code

```
wget https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-7.0.2.tar.xz
tar -xf linux-7.0.2.tar.xz
```

## Install deps

```
uv sync
```

## Run

```
uv run token_counting.py
```

The script writes:

- `kernel_embedding_statistics.csv`: per-file chunk, character, token, and cost totals.
- `kernel_embedding_documents/documents.jsonl`: one embedding-ready document per line.
- `kernel_embedding_documents/manifest.json`: run metadata and corpus totals.

Each JSONL document has this shape:

```json
{
  "id": "stable sha256 id",
  "file_path": "linux-7.0.2/path/to/source.c",
  "kind": "function",
  "start_byte": 123,
  "end_byte": 456,
  "characters": 333,
  "tokens": 111,
  "text": "// File: linux-7.0.2/path/to/source.c\n// Type: function\n..."
}
```

Output:

```
Progress: 1/66330 files (0.0%) | chunks: 31 | cost so far: $0.000441 | estimated total cost: $29.251530 | 0.3 files/s
Progress: 3316/66330 files (5.0%) | chunks: 139424 | cost so far: $2.393766 | estimated total cost: $47.882536 | 668.8 files/s
Progress: 6632/66330 files (10.0%) | chunks: 324201 | cost so far: $5.530366 | estimated total cost: $55.311995 | 907.3 files/s
Progress: 9948/66330 files (15.0%) | chunks: 649663 | cost so far: $10.640390 | estimated total cost: $70.946630 | 896.8 files/s
Progress: 13264/66330 files (20.0%) | chunks: 928529 | cost so far: $14.813023 | estimated total cost: $74.076282 | 909.9 files/s
Progress: 16580/66330 files (25.0%) | chunks: 1214853 | cost so far: $19.445015 | estimated total cost: $77.791787 | 915.3 files/s
Progress: 19896/66330 files (30.0%) | chunks: 1463711 | cost so far: $24.341835 | estimated total cost: $81.151685 | 917.5 files/s
Progress: 23212/66330 files (35.0%) | chunks: 1786412 | cost so far: $30.053810 | estimated total cost: $85.880975 | 893.4 files/s
Progress: 26528/66330 files (40.0%) | chunks: 2207146 | cost so far: $38.310628 | estimated total cost: $95.791011 | 829.9 files/s
Progress: 29844/66330 files (45.0%) | chunks: 2487119 | cost so far: $42.289034 | estimated total cost: $93.989800 | 846.0 files/s
Progress: 33160/66330 files (50.0%) | chunks: 2759293 | cost so far: $46.994588 | estimated total cost: $94.003347 | 853.0 files/s
Progress: 36476/66330 files (55.0%) | chunks: 2843297 | cost so far: $48.846694 | estimated total cost: $88.825562 | 901.9 files/s
Progress: 39792/66330 files (60.0%) | chunks: 2964042 | cost so far: $50.159514 | estimated total cost: $83.611795 | 961.4 files/s
Progress: 43108/66330 files (65.0%) | chunks: 3096819 | cost so far: $51.362304 | estimated total cost: $79.030844 | 1017.5 files/s
Progress: 46424/66330 files (70.0%) | chunks: 3344585 | cost so far: $53.347272 | estimated total cost: $76.221880 | 1060.3 files/s
Progress: 49740/66330 files (75.0%) | chunks: 3517276 | cost so far: $54.856529 | estimated total cost: $73.153068 | 1103.6 files/s
Progress: 53056/66330 files (80.0%) | chunks: 3760728 | cost so far: $57.147074 | estimated total cost: $71.444613 | 1138.5 files/s
Progress: 56372/66330 files (85.0%) | chunks: 7365746 | cost so far: $101.379598 | estimated total cost: $119.288099 | 703.5 files/s
Progress: 59688/66330 files (90.0%) | chunks: 8263526 | cost so far: $112.072353 | estimated total cost: $124.543613 | 711.3 files/s
Progress: 63004/66330 files (95.0%) | chunks: 8718325 | cost so far: $116.831504 | estimated total cost: $122.999074 | 734.2 files/s
Progress: 66320/66330 files (100.0%) | chunks: 8783588 | cost so far: $118.647074 | estimated total cost: $118.664964 | 760.7 files/s
Progress: 66330/66330 files (100.0%) | chunks: 8919900 | cost so far: $120.505107 | estimated total cost: $120.505107 | 751.4 files/s

Processing complete. Processed 66330 files.
Used 16 worker processes.
Estimated total cost: $120.505107
Statistics saved to kernel_embedding_statistics.csv
```

## TODO

- [ ] Add https://github.com/sirius94/tree-sitter-gas to support GNU assembly syntax instead handwritten checker.
